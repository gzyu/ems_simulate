"""
ICD 文件解析导入模块
解析 IEC 61850 ICD/SCD/CID 文件，将数据模型映射为系统测点（遥测/遥信/遥控/遥调）

ICD 文件遵循 IEC 61850 SCL (Substation Configuration Language) 的 XML Schema，
主要结构:
  <SCL>
    <IED>
      <AccessPoint>
        <Server>
          <LDevice inst="...">
            <LN lnClass="..." inst="..." lnType="...">
              <DOI name="..."> ...
    <DataTypeTemplates>
      <LNodeType id="..." lnClass="...">
        <DO name="..." type="..."/>
      <DOType id="..." cdc="...">
        <DA name="..." fc="..." bType="..."/>
        <SDO name="..." type="..."/>

分类策略（基于 CDC 和 FC）:
  - CDC=MV/CMV/SAV + FC=MX  → 遥测 (Yc)，数据属性路径 mag.f / cVal.mag.f
  - CDC=SPS/DPS/INS + FC=ST → 遥信 (Yx)，数据属性路径 stVal
  - CDC=SPC/DPC + FC=CO     → 遥控 (Yk)，数据属性路径 ctlVal / Oper.ctlVal
  - CDC=APC/INC + FC=CO     → 遥调 (Yt)，数据属性路径 Oper.ctlVal / setVal
"""

import os
import xml.etree.ElementTree as ET
from typing import List, Dict, Tuple, Optional

from src.data.controller.db import local_session
from src.data.model.point_yc import PointYc
from src.data.model.point_yx import PointYx
from src.data.model.point_yk import PointYk
from src.data.model.point_yt import PointYt
from src.data.log import log

# IEC 61850 SCL 命名空间
SCL_NS = "http://www.iec.ch/61850/2003/SCL"


# CDC 到测点类型的映射
CDC_YC = {"MV", "CMV", "SAV", "WYE", "DEL", "SEQ", "HMV"}          # 遥测
CDC_YX = {"SPS", "DPS", "INS", "ENS", "ACT", "ACD", "SEC", "BCR"}   # 遥信
CDC_YK = {"SPC", "DPC"}                                              # 遥控
CDC_YT = {"APC", "INC", "ASG", "ING", "SPG", "BAC"}                 # 遥调


def _ns(tag: str) -> str:
    """给 tag 加 SCL 命名空间前缀"""
    return f"{{{SCL_NS}}}{tag}"


class IcdPointImporter:
    """ICD 文件解析导入器"""

    def __init__(self, channel_id: int):
        self.channel_id = channel_id
        self.yc_count = 0
        self.yx_count = 0
        self.yk_count = 0
        self.yt_count = 0

        # DataTypeTemplates 缓存
        self._ln_types: Dict[str, ET.Element] = {}   # id -> LNodeType
        self._do_types: Dict[str, ET.Element] = {}   # id -> DOType
        self._da_types: Dict[str, ET.Element] = {}   # id -> DAType

    def _clear_existing_points(self) -> None:
        """清除该通道已有的测点数据"""
        try:
            with local_session() as session:
                with session.begin():
                    session.query(PointYc).where(PointYc.channel_id == self.channel_id).delete()
                    session.query(PointYx).where(PointYx.channel_id == self.channel_id).delete()
                    session.query(PointYk).where(PointYk.channel_id == self.channel_id).delete()
                    session.query(PointYt).where(PointYt.channel_id == self.channel_id).delete()
            log.info(f"已清除通道 {self.channel_id} 的旧测点数据")
        except Exception as e:
            log.error(f"清除旧测点数据失败: {e}")
            raise e

    def import_from_icd(self, file_path: str) -> Tuple[int, int, int, int]:
        """从 ICD/SCD/CID 文件导入测点

        Args:
            file_path: ICD 文件路径

        Returns:
            (yc_count, yx_count, yk_count, yt_count) 各类型导入数量
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        # 清除旧数据
        self._clear_existing_points()

        # 解析 XML
        tree = ET.parse(file_path)
        root = tree.getroot()

        # 检测是否有命名空间
        self._detect_namespace(root)

        # 构建 DataTypeTemplates 缓存
        self._build_type_cache(root)

        # 收集所有测点
        yc_points = []
        yx_points = []
        yk_points = []
        yt_points = []

        # 遍历所有 IED -> Server -> LDevice -> LN
        for ied in root.iter(self._tag("IED")):
            ied_name = ied.get("name", "IED")

            for ap in ied.iter(self._tag("AccessPoint")):
                server = ap.find(self._tag("Server"))
                if server is None:
                    continue

                for ld in server.iter(self._tag("LDevice")):
                    ld_inst = ld.get("inst", "LD0")

                    # 处理 LN0 和 LN
                    for ln_elem in self._get_logical_nodes(ld):
                        ln_class = ln_elem.get("lnClass", "")
                        ln_inst = ln_elem.get("inst", "")
                        ln_prefix = ln_elem.get("prefix", "")
                        ln_type = ln_elem.get("lnType", "")

                        # 构造 LN 名称: prefix + lnClass + inst
                        if ln_elem.tag == self._tag("LN0") or ln_elem.tag == "LN0":
                            ln_name = "LLN0"
                        else:
                            ln_name = f"{ln_prefix}{ln_class}{ln_inst}"

                        # 从 DataTypeTemplates 获取 LN 类型定义
                        ln_type_def = self._ln_types.get(ln_type)
                        if ln_type_def is None:
                            continue

                        # 遍历 DO
                        for do_elem in ln_type_def.findall(self._tag("DO")):
                            do_name = do_elem.get("name", "")
                            do_type_id = do_elem.get("type", "")

                            do_type_def = self._do_types.get(do_type_id)
                            if do_type_def is None:
                                continue

                            cdc = do_type_def.get("cdc", "")
                            ref_prefix = f"{ld_inst}/{ln_name}.{do_name}"

                            # 根据 CDC 分类
                            if cdc in CDC_YC:
                                da_ref = self._get_value_ref(do_type_def, cdc, "MX")
                                if da_ref:
                                    yc_points.append({
                                        "code": f"{ld_inst}_{ln_name}_{do_name}",
                                        "name": self._get_do_desc(do_elem, do_type_def, do_name),
                                        "reg_addr": f"{ref_prefix}.{da_ref}",
                                        "cdc": cdc,
                                    })

                            elif cdc in CDC_YX:
                                da_ref = self._get_value_ref(do_type_def, cdc, "ST")
                                if da_ref:
                                    yx_points.append({
                                        "code": f"{ld_inst}_{ln_name}_{do_name}",
                                        "name": self._get_do_desc(do_elem, do_type_def, do_name),
                                        "reg_addr": f"{ref_prefix}.{da_ref}",
                                        "cdc": cdc,
                                    })

                            elif cdc in CDC_YK:
                                da_ref = self._get_control_ref(do_type_def, cdc)
                                if da_ref:
                                    yk_points.append({
                                        "code": f"{ld_inst}_{ln_name}_{do_name}",
                                        "name": self._get_do_desc(do_elem, do_type_def, do_name),
                                        "reg_addr": f"{ref_prefix}.{da_ref}",
                                        "cdc": cdc,
                                    })

                            elif cdc in CDC_YT:
                                da_ref = self._get_control_ref(do_type_def, cdc)
                                if da_ref:
                                    yt_points.append({
                                        "code": f"{ld_inst}_{ln_name}_{do_name}",
                                        "name": self._get_do_desc(do_elem, do_type_def, do_name),
                                        "reg_addr": f"{ref_prefix}.{da_ref}",
                                        "cdc": cdc,
                                    })

        # 批量写入数据库
        self._save_yc(yc_points)
        self._save_yx(yx_points)
        self._save_yk(yk_points)
        self._save_yt(yt_points)

        log.info(
            f"ICD导入完成: 遥测={self.yc_count}, 遥信={self.yx_count}, "
            f"遥控={self.yk_count}, 遥调={self.yt_count}"
        )
        return (self.yc_count, self.yx_count, self.yk_count, self.yt_count)

    def _detect_namespace(self, root: ET.Element) -> None:
        """检测 XML 根元素是否使用了 SCL 命名空间"""
        tag = root.tag
        if tag.startswith("{"):
            # 有命名空间
            ns = tag.split("}")[0] + "}"
            self._ns_prefix = ns
        else:
            self._ns_prefix = ""

    def _tag(self, name: str) -> str:
        """根据检测到的命名空间构造完整 tag"""
        if self._ns_prefix:
            return f"{self._ns_prefix}{name}"
        return name

    def _build_type_cache(self, root: ET.Element) -> None:
        """构建 DataTypeTemplates 缓存"""
        dtt = root.find(self._tag("DataTypeTemplates"))
        if dtt is None:
            log.warning("ICD 文件中未找到 DataTypeTemplates 节")
            return

        for lt in dtt.findall(self._tag("LNodeType")):
            lt_id = lt.get("id", "")
            if lt_id:
                self._ln_types[lt_id] = lt

        for dt in dtt.findall(self._tag("DOType")):
            dt_id = dt.get("id", "")
            if dt_id:
                self._do_types[dt_id] = dt

        for dat in dtt.findall(self._tag("DAType")):
            dat_id = dat.get("id", "")
            if dat_id:
                self._da_types[dat_id] = dat

        log.info(
            f"DataTypeTemplates 解析完成: "
            f"LNodeType={len(self._ln_types)}, "
            f"DOType={len(self._do_types)}, "
            f"DAType={len(self._da_types)}"
        )

    def _get_logical_nodes(self, ld: ET.Element) -> list:
        """获取 LDevice 下所有逻辑节点 (LN0 + LN)"""
        nodes = []
        ln0 = ld.find(self._tag("LN0"))
        if ln0 is not None:
            nodes.append(ln0)
        nodes.extend(ld.findall(self._tag("LN")))
        return nodes

    def _get_do_desc(self, do_elem: ET.Element, do_type_def: ET.Element, do_name: str) -> str:
        """获取 DO 的描述信息"""
        # 优先从 DO 元素获取 desc
        desc = do_elem.get("desc", "")
        if not desc:
            # 从 DOType 获取 desc
            desc = do_type_def.get("desc", "")
        if not desc:
            desc = do_name
        return desc

    def _get_value_ref(self, do_type: ET.Element, cdc: str, target_fc: str) -> Optional[str]:
        """获取测量/状态类型 DO 的值引用路径

        根据 CDC 类型确定数据属性路径:
        - MV: mag.f (或 mag.i)
        - CMV: cVal.mag.f
        - SPS: stVal
        - DPS: stVal
        - INS/ENS: stVal
        - BCR: actVal
        """
        if cdc == "MV":
            # 查找 mag SDO，然后找 f DA
            return self._find_da_path(do_type, ["mag", "f"], target_fc) or "mag.f"
        elif cdc == "CMV":
            return self._find_da_path(do_type, ["cVal", "mag", "f"], target_fc) or "cVal.mag.f"
        elif cdc == "SAV":
            return self._find_da_path(do_type, ["instMag", "f"], target_fc) or "instMag.f"
        elif cdc in ("SPS", "DPS", "INS", "ENS", "ACT", "ACD", "SEC"):
            return "stVal"
        elif cdc == "BCR":
            return "actVal"
        elif cdc in ("WYE", "DEL", "SEQ", "HMV"):
            # 复合 CDC，取第一个可用子 DO
            return self._find_composite_ref(do_type, target_fc)
        return None

    def _get_control_ref(self, do_type: ET.Element, cdc: str) -> Optional[str]:
        """获取控制类型 DO 的控制引用路径

        - SPC/DPC: Oper.ctlVal 或直接 ctlVal
        - APC/INC/BAC: Oper.ctlVal 或 setVal
        """
        # 先查 Oper SDO
        for sdo in do_type.findall(self._tag("SDO")):
            if sdo.get("name") == "Oper":
                return "Oper.ctlVal"

        # 查找直接的 ctlVal DA
        for da in do_type.findall(self._tag("DA")):
            if da.get("name") == "ctlVal":
                return "ctlVal"

        # APC/INC 的 setVal
        if cdc in ("APC", "INC", "ASG", "ING", "SPG"):
            for da in do_type.findall(self._tag("DA")):
                if da.get("name") == "setVal":
                    return "setVal"

        return "ctlVal"  # 默认

    def _find_da_path(self, do_type: ET.Element, path_parts: list, target_fc: str) -> Optional[str]:
        """在 DOType 中递归查找 DA 路径"""
        if not path_parts:
            return None

        name = path_parts[0]

        # 查找 DA
        for da in do_type.findall(self._tag("DA")):
            if da.get("name") == name:
                if len(path_parts) == 1:
                    return name
                # DA 有子类型
                da_type_id = da.get("type", "")
                if da_type_id and da_type_id in self._da_types:
                    sub_result = self._find_bda_path(
                        self._da_types[da_type_id], path_parts[1:]
                    )
                    if sub_result:
                        return f"{name}.{sub_result}"

        # 查找 SDO
        for sdo in do_type.findall(self._tag("SDO")):
            if sdo.get("name") == name:
                sdo_type_id = sdo.get("type", "")
                if sdo_type_id and sdo_type_id in self._do_types:
                    sub_result = self._find_da_path(
                        self._do_types[sdo_type_id], path_parts[1:], target_fc
                    )
                    if sub_result:
                        return f"{name}.{sub_result}"

        return None

    def _find_bda_path(self, da_type: ET.Element, path_parts: list) -> Optional[str]:
        """在 DAType 中递归查找 BDA 路径"""
        if not path_parts:
            return None

        name = path_parts[0]
        for bda in da_type.findall(self._tag("BDA")):
            if bda.get("name") == name:
                if len(path_parts) == 1:
                    return name
                bda_type_id = bda.get("type", "")
                if bda_type_id and bda_type_id in self._da_types:
                    sub_result = self._find_bda_path(
                        self._da_types[bda_type_id], path_parts[1:]
                    )
                    if sub_result:
                        return f"{name}.{sub_result}"
        return None

    def _find_composite_ref(self, do_type: ET.Element, target_fc: str) -> Optional[str]:
        """为复合 CDC (WYE/DEL 等) 查找第一个可用的 SDO"""
        for sdo in do_type.findall(self._tag("SDO")):
            sdo_name = sdo.get("name", "")
            sdo_type_id = sdo.get("type", "")
            if sdo_type_id in self._do_types:
                sub_type = self._do_types[sdo_type_id]
                sub_cdc = sub_type.get("cdc", "")
                if sub_cdc == "MV":
                    return f"{sdo_name}.mag.f"
        return None

    def _save_yc(self, points: list) -> None:
        """批量保存遥测测点"""
        if not points:
            return
        with local_session() as session:
            with session.begin():
                for idx, p in enumerate(points):
                    point = PointYc(
                        code=p["code"],
                        name=p["name"],
                        channel_id=self.channel_id,
                        rtu_addr=1,
                        reg_addr=p["reg_addr"],
                        func_code=0,  # IEC 61850 不需要功能码
                        decode_code="",
                        mul_coe=1.0,
                        add_coe=0.0,
                        max_limit=999999.0,
                        min_limit=-999999.0,
                    )
                    session.add(point)
                    self.yc_count += 1

    def _save_yx(self, points: list) -> None:
        """批量保存遥信测点"""
        if not points:
            return
        with local_session() as session:
            with session.begin():
                for p in points:
                    point = PointYx(
                        code=p["code"],
                        name=p["name"],
                        channel_id=self.channel_id,
                        rtu_addr=1,
                        reg_addr=p["reg_addr"],
                        func_code=0,
                        decode_code="",
                        bit=None,
                        reverse=False,
                    )
                    session.add(point)
                    self.yx_count += 1

    def _save_yk(self, points: list) -> None:
        """批量保存遥控测点"""
        if not points:
            return
        with local_session() as session:
            with session.begin():
                for p in points:
                    point = PointYk(
                        code=p["code"],
                        name=p["name"],
                        channel_id=self.channel_id,
                        rtu_addr=1,
                        reg_addr=p["reg_addr"],
                        func_code=0,
                        decode_code="",
                        bit=None,
                        command_type=0,
                    )
                    session.add(point)
                    self.yk_count += 1

    def _save_yt(self, points: list) -> None:
        """批量保存遥调测点"""
        if not points:
            return
        with local_session() as session:
            with session.begin():
                for p in points:
                    point = PointYt(
                        code=p["code"],
                        name=p["name"],
                        channel_id=self.channel_id,
                        rtu_addr=1,
                        reg_addr=p["reg_addr"],
                        func_code=0,
                        decode_code="",
                        mul_coe=1.0,
                        add_coe=0.0,
                        max_limit=999999.0,
                        min_limit=-999999.0,
                    )
                    session.add(point)
                    self.yt_count += 1
