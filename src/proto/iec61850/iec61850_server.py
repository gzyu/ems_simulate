"""
IEC 61850 MMS 服务端封装
基于 pyiec61850 (libiec61850 Python bindings) 实现动态数据模型的 IED Server

支持两种地址格式:
  1. 简单地址 (如 "1", "2"): 使用固定的 MMXU1/GGIO1/GGIO2 逻辑节点结构
  2. 完整引用路径 (如 "MEAS/M0GGIO1.AnIn1.mag.f"): 按 ICD 文件结构动态创建 LD/LN/DO/DA
"""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple, Union
from .log import log

try:
    from pyiec61850 import pyiec61850 as iec61850
    HAS_IEC61850 = True
except ImportError:
    HAS_IEC61850 = False
    log.warning("pyiec61850 未安装，IEC 61850 功能不可用。请执行: pip install pyiec61850")


# IEC 61850 功能约束 (Functional Constraint)
FC_MX = None  # 测量值 (Measured Value)
FC_ST = None  # 状态 (Status)
FC_CO = None  # 控制 (Control)
FC_CF = None  # 配置 (Configuration)

if HAS_IEC61850:
    FC_MX = iec61850.IEC61850_FC_MX
    FC_ST = iec61850.IEC61850_FC_ST
    FC_CO = iec61850.IEC61850_FC_CO
    FC_CF = iec61850.IEC61850_FC_CF


def _is_full_ref(address) -> bool:
    """判断地址是否为完整的 IEC 61850 引用路径 (包含 '/')"""
    return isinstance(address, str) and '/' in address


def _parse_ref(address: str):
    """解析完整 IEC 61850 引用路径

    格式: {ld_inst}/{ln_name}.{do_name}.{da_path}
    例: "MEAS/M0GGIO1.AnIn1.mag.f" -> ("MEAS", "M0GGIO1", "AnIn1", "mag.f")

    Returns:
        (ld_inst, ln_name, do_name, da_path) 或 None (解析失败)
    """
    try:
        parts = address.split('/', 1)
        if len(parts) != 2:
            return None
        ld_inst = parts[0]
        rest = parts[1]
        # 至少要有 LN.DO 两部分
        rest_parts = rest.split('.', 2)
        if len(rest_parts) < 2:
            return None
        ln_name = rest_parts[0]
        do_name = rest_parts[1]
        da_path = rest_parts[2] if len(rest_parts) > 2 else ""
        return (ld_inst, ln_name, do_name, da_path)
    except Exception:
        return None


class IEC61850Server:
    """IEC 61850 MMS 服务端

    使用 pyiec61850 动态创建 IedModel 和 IedServer，
    将测点映射到 IEC 61850 数据模型。

    支持两种地址格式:
    1. 简单地址 (int/str 不含 '/'): 使用固定结构
        IedModel (model_name)
          └── LogicalDevice (ld_name)
                ├── LLN0
                ├── MMXU1 (遥测) → MV_{address}.mag.f
                ├── GGIO1 (遥信/遥控) → SPS_{address}.stVal / SPC_{address}.ctlVal
                └── GGIO2 (遥调) → APC_{address}.ctlVal

    2. 完整引用路径 (含 '/'): 按 ICD 结构动态创建
        IedModel (model_name)
          └── LogicalDevice ({ld_inst})   ← 动态创建
                ├── LLN0
                └── {ln_name}             ← 动态创建
                      └── {do_name}       ← 使用原始 DO 名
                            └── {da_path} ← 按 frame_type 创建
    """

    def __init__(
        self,
        ip: str = "0.0.0.0",
        port: int = 102,
        model_name: str = "EMS",
        ied_name: str = "EMSDevice",
        ld_name: str = "GenericLD",
    ):
        if not HAS_IEC61850:
            raise RuntimeError("pyiec61850 未安装，无法创建 IEC 61850 服务器")

        self.ip = ip
        self.port = port
        self.model_name = model_name
        self.ied_name = ied_name
        self.ld_name = ld_name

        self._server = None
        self._model = None
        self._ld = None
        self._is_running = False

        # 简单地址模式: 固定逻辑节点引用
        self._lln0 = None
        self._mmxu = None   # 遥测逻辑节点
        self._ggio1 = None  # 遥信/遥控逻辑节点
        self._ggio2 = None  # 遥调逻辑节点

        # 动态模型: LD/LN 缓存 (用于完整引用路径)
        self._ld_map: Dict[str, Any] = {}   # ld_inst -> LD 对象
        self._ln_map: Dict[str, Any] = {}   # "ld_inst/ln_name" -> LN 对象
        # DO/DA 去重缓存: 防止同一 LN 下重复创建同名 DO/DA
        self._do_map: Dict[str, Any] = {}   # "ld_inst/ln_name.do_name[.sub_do]" -> DO 对象
        self._da_map: Dict[str, Any] = {}   # "ld_inst/ln_name.do_name.da_path" -> DA 对象

        # 地址 -> MMS 引用路径 的映射
        self._point_refs: Dict[str, str] = {}  # address -> ref
        # 地址 -> DataAttribute 对象的映射
        self._point_attrs: Dict[str, Any] = {}
        # 地址 -> FC 的映射
        self._point_fc: Dict[str, str] = {}
        # 地址 -> iec_type 的映射
        self._point_iec_type: Dict[str, str] = {}

        # 集成 GOOSE 发布配置
        self._goose_interface: str = "eth0"          # GOOSE 网络接口
        self._goose_cb_list: List[Dict[str, Any]] = []  # 跟踪已注册的 GoCB 条目

        # 标准子 DA (q.validity, q.source, t.seconds 等) 引用列表，
        # 用于服务器启动后初始化默认值，避免 IED Scout 因值为空显示红色感叹号
        # 每个元素: (da_object, bda_name, iec_type_str)
        # iec_type_str: "int32", "boolean", "uint32"
        self._standard_bda_list: List[tuple] = []

        # 保持底层 C 对象的 Python 引用，防止被垃圾回收导致崩溃
        self._keep_alive: List[Any] = []

        self._build_base_model()

    def _build_base_model(self):
        """构建基础 IED 模型（只含 IedModel，LD/LN 在添加测点时懒创建）

        使用 ied_name 作为 IED 模型名称 (与 ICD 文件的 iedName 保持一致)。
        model_name 同时作为 MMS 引用路径前缀，应与 ied_name 一致。

        注意：不再预创建默认 LD (GenericLD) 和逻辑节点 (MMXU1/GGIO1/GGIO2)，
        避免 ICD 导入模式下服务器模型出现多余的 Logical Device，
        导致 IEDScout 等客户端加载 CID 文件连接时报 LIB0406 结构不匹配错误。
        """
        ied_model_name = self.ied_name if self.ied_name else self.model_name
        self._model = iec61850.IedModel_create(ied_model_name)
        # 同步 model_name 确保 MMS 引用路径前缀与 IED 名称一致
        self.model_name = ied_model_name

    def _ensure_base_ld(self):
        """懒创建默认 LD (simple address mode) 及其逻辑节点

        仅在添加简单地址测点时调用，避免 ICD 导入模式下产生多余的 GenericLD。
        """
        if self._ld is not None:
            return
        self._ld = iec61850.LogicalDevice_create(self.ld_name, self._model)
        self._lln0 = iec61850.LogicalNode_create("LLN0", self._ld)

        # 预创建简单地址模式的逻辑节点
        self._mmxu = iec61850.LogicalNode_create("MMXU1", self._ld)
        self._ggio1 = iec61850.LogicalNode_create("GGIO1", self._ld)
        self._ggio2 = iec61850.LogicalNode_create("GGIO2", self._ld)

        # 注册默认 LD 和其 LN (用于去重, 避免 _get_or_create_ln 重复创建)
        self._ld_map[self.ld_name] = self._ld
        self._ln_map[f"{self.ld_name}/LLN0"] = self._lln0
        self._ln_map[f"{self.ld_name}/MMXU1"] = self._mmxu
        self._ln_map[f"{self.ld_name}/GGIO1"] = self._ggio1
        self._ln_map[f"{self.ld_name}/GGIO2"] = self._ggio2

    def _get_or_create_ld(self, ld_inst: str):
        """获取或创建逻辑设备"""
        if ld_inst in self._ld_map:
            return self._ld_map[ld_inst]
        ld = iec61850.LogicalDevice_create(ld_inst, self._model)
        lln0 = iec61850.LogicalNode_create("LLN0", ld)
        self._ld_map[ld_inst] = ld
        # 将自动创建的 LLN0 也注册到 _ln_map, 避免 _get_or_create_ln 重复创建
        self._ln_map[f"{ld_inst}/LLN0"] = lln0
        log.info(f"IEC61850 动态创建逻辑设备: {ld_inst}")
        return ld

    def _get_or_create_ln(self, ld_inst: str, ln_name: str):
        """获取或创建逻辑节点"""
        key = f"{ld_inst}/{ln_name}"
        if key in self._ln_map:
            return self._ln_map[key]
        ld = self._get_or_create_ld(ld_inst)
        ln = iec61850.LogicalNode_create(ln_name, ld)
        self._ln_map[key] = ln
        log.info(f"IEC61850 动态创建逻辑节点: {key}")
        return ln

    def add_point(self, address, frame_type: int = 0, fc: str = "") -> Optional[str]:
        """添加测点到数据模型

        Args:
            address: 测点地址，支持两种格式:
                     - 简单地址 (int/str 不含 '/'): 使用 MMXU1/GGIO1/GGIO2 固定结构
                     - 完整引用路径 (str 含 '/'): 按 ICD 结构动态创建 LD/LN/DO/DA
            frame_type: 帧类型 (0=遥测, 1=遥信, 2=遥控, 3=遥调)，仅简单地址模式使用
            fc: IEC 61850 功能约束 (如 "MX", "ST", "CO", "DC")，仅完整引用路径模式使用

        Returns:
            MMS 引用路径，用于后续读写
        """
        addr_str = str(address)
        if addr_str in self._point_refs:
            return self._point_refs[addr_str]  # 已存在

        if _is_full_ref(address):
            return self._add_point_from_ref(address, frame_type, fc)
        else:
            return self._add_point_simple(address, frame_type)

    def _add_point_simple(self, address, frame_type: int) -> Optional[str]:
        """简单地址模式: 使用固定的 MMXU1/GGIO1/GGIO2 结构添加测点"""
        # 确保默认 LD 已创建（懒初始化）
        self._ensure_base_ld()
        addr_str = str(address)

        # 确保 address 是合法的 IEC 61850 节点名称 (不能包含 . / \ 等)
        safe_addr = str(address).replace('.', '_').replace('/', '_').replace('\\', '_').replace('-', '_')

        ref = None
        if frame_type == 0:  # 遥测 - MV (Measured Value)
            do_name = f"MV_{safe_addr}"
            do_key = f"{self.ld_name}/MMXU1.{do_name}"
            do = self._do_map.get(do_key)
            if do is None:
                do = iec61850.DataObject_create(do_name, iec61850.toModelNode(self._mmxu), 0)
                self._do_map[do_key] = do
                self._keep_alive.append(do)
                self._add_standard_das(do, do_key, "MX", 0, ["mag", "f"])
            mag_key = f"{do_key}.mag"
            mag = self._do_map.get(mag_key)
            if mag is None:
                mag = iec61850.DataObject_create("mag", iec61850.toModelNode(do), 0)
                self._do_map[mag_key] = mag
                self._keep_alive.append(mag)
            da = iec61850.DataAttribute_create(
                "f", iec61850.toModelNode(mag),
                iec61850.IEC61850_FLOAT32,
                FC_MX, 0, 0, 0
            )
            ref = f"{self.model_name}{self.ld_name}/MMXU1.{do_name}.mag.f"
            self._point_attrs[addr_str] = da
            self._point_fc[addr_str] = "MX"
            self._point_iec_type[addr_str] = "float"
            self._keep_alive.extend([do_name, da])

        elif frame_type == 1:  # 遥信 - SPS (Single Point Status)
            do_name = f"SPS_{safe_addr}"
            do_key = f"{self.ld_name}/GGIO1.{do_name}"
            do = self._do_map.get(do_key)
            if do is None:
                do = iec61850.DataObject_create(do_name, iec61850.toModelNode(self._ggio1), 0)
                self._do_map[do_key] = do
                self._keep_alive.append(do)
                self._add_standard_das(do, do_key, "ST", 1, ["stVal"])
            da = iec61850.DataAttribute_create(
                "stVal", iec61850.toModelNode(do),
                iec61850.IEC61850_BOOLEAN,
                FC_ST, 0, 0, 0
            )
            ref = f"{self.model_name}{self.ld_name}/GGIO1.{do_name}.stVal"
            self._point_attrs[addr_str] = da
            self._point_fc[addr_str] = "ST"
            self._point_iec_type[addr_str] = "boolean"
            self._keep_alive.extend([do_name, da])

        elif frame_type == 2:  # 遥控 - SPC (Single Point Control)
            do_name = f"SPC_{safe_addr}"
            do_key = f"{self.ld_name}/GGIO1.{do_name}"
            do = self._do_map.get(do_key)
            if do is None:
                do = iec61850.DataObject_create(do_name, iec61850.toModelNode(self._ggio1), 0)
                self._do_map[do_key] = do
                self._keep_alive.append(do)
            da = iec61850.DataAttribute_create(
                "ctlVal", iec61850.toModelNode(do),
                iec61850.IEC61850_BOOLEAN,
                FC_CO, 0, 0, 0
            )
            ref = f"{self.model_name}{self.ld_name}/GGIO1.{do_name}.ctlVal"
            self._point_attrs[addr_str] = da
            self._point_fc[addr_str] = "CO"
            self._point_iec_type[addr_str] = "boolean"
            self._keep_alive.extend([do_name, da])

        elif frame_type == 3:  # 遥调 - APC (Analog Point Control)
            do_name = f"APC_{safe_addr}"
            do_key = f"{self.ld_name}/GGIO2.{do_name}"
            do = self._do_map.get(do_key)
            if do is None:
                do = iec61850.DataObject_create(do_name, iec61850.toModelNode(self._ggio2), 0)
                self._do_map[do_key] = do
                self._keep_alive.append(do)
            da = iec61850.DataAttribute_create(
                "ctlVal", iec61850.toModelNode(do),
                iec61850.IEC61850_FLOAT32,
                FC_CO, 0, 0, 0
            )
            ref = f"{self.model_name}{self.ld_name}/GGIO2.{do_name}.ctlVal"
            self._point_attrs[addr_str] = da
            self._point_fc[addr_str] = "CO"
            self._point_iec_type[addr_str] = "float"
            self._keep_alive.extend([do_name, da])

        if ref:
            self._point_refs[addr_str] = ref
            log.info(f"IEC61850 已成功添加测点(简单模式): address={address}, frame_type={frame_type}, ref={ref}")
        else:
            log.error(f"IEC61850 添加测点失败: address={address}, frame_type={frame_type}")

        return ref

    def _add_point_from_ref(self, address: str, frame_type: int, fc: str = "") -> Optional[str]:
        """完整引用路径模式: 按 ICD 文件结构动态创建 LD/LN/DO/DA

        address 格式: {ld_inst}/{ln_name}.{do_name}.{da_path}
        例如: "MEAS/M0GGIO1.AnIn1.mag.f"

        关键: 同一 LN 下的同名 DO 只创建一次, 同一 DO 下的同名 DA 也只创建一次,
        避免客户端连接时因重复 key 崩溃 (如 Key: Health)。
        """
        parsed = _parse_ref(address)
        if not parsed:
            log.error(f"IEC61850 无法解析引用路径: {address}")
            return None

        ld_inst, ln_name, do_name, da_path = parsed
        if not da_path:
            log.warning(f"IEC61850 引用路径缺少 DA 路径: {address}, 跳过")
            return None

        addr_str = str(address)

        # 获取或创建逻辑节点
        ln = self._get_or_create_ln(ld_inst, ln_name)

        # 预拆分 da_path 以便在 DO 创建时用于 FC 推断
        da_parts = da_path.split('.') if da_path else []

        # === 1. 获取或创建 DO (去重) ===
        do_key = f"{ld_inst}/{ln_name}.{do_name}"
        do_obj = self._do_map.get(do_key)
        if do_obj is None:
            do_obj = iec61850.DataObject_create(do_name, iec61850.toModelNode(ln), 0)
            self._do_map[do_key] = do_obj
            self._keep_alive.append(do_obj)
            # 为新 DO 自动补充标准 DAs (q, t, dU)，确保 MMS 模型完整
            # 避免 .NET 客户端因缺少预期 DA 而 IndexOutOfRange
            self._add_standard_das(do_obj, do_key, fc, frame_type, da_parts)

        # === 2. 根据 da_path 创建 DA 层级结构 (去重) ===
        # da_path 示例: "mag.f", "stVal", "q.validity", "Oper.ctlVal", "cVal.mag.f"

        # 推断 FC: 优先用传入值, 否则从 DA 路径推断
        if not fc:
            fc = self._infer_fc(frame_type, da_parts[0])

        fc_const = self._resolve_fc_const(fc)
        if fc_const is None:
            log.warning(f"IEC61850 无法解析 FC: {fc}, 使用 MX 作为默认值")
            fc_const = FC_MX

        # 推断 DA 的 IEC 61850 数据类型
        iec_type = self._infer_iec_type(frame_type, da_parts)

        # 沿 da_path 逐级创建, 中间级用 DataObject (结构体), 叶子级用 DataAttribute
        parent = do_obj
        for i, part in enumerate(da_parts):
            is_leaf = (i == len(da_parts) - 1)
            part_key = f"{ld_inst}/{ln_name}.{do_name}.{'.'.join(da_parts[:i+1])}"

            if is_leaf:
                # 叶子节点: 创建 DataAttribute
                existing_da = self._da_map.get(part_key)
                if existing_da is not None:
                    # DA 已存在, 复用
                    da = existing_da
                    log.debug(f"IEC61850 DA 已存在, 复用: {part_key}")
                else:
                    da = iec61850.DataAttribute_create(
                        part, iec61850.toModelNode(parent),
                        iec_type, fc_const, 0, 0, 0
                    )
                    self._da_map[part_key] = da
                    self._keep_alive.append(da)
            else:
                # 中间节点: 创建 DataObject (结构体容器)
                existing_obj = self._do_map.get(part_key)
                if existing_obj is not None:
                    parent = existing_obj
                else:
                    sub_obj = iec61850.DataObject_create(part, iec61850.toModelNode(parent), 0)
                    self._do_map[part_key] = sub_obj
                    self._keep_alive.append(sub_obj)
                    parent = sub_obj

        # === 3. 构建完整 MMS 引用路径 ===
        ref = f"{self.model_name}{ld_inst}/{ln_name}.{do_name}.{da_path}"
        self._point_refs[addr_str] = ref
        self._point_fc[addr_str] = fc

        # 推断 iec_type 字符串
        iec_type_str = self._infer_iec_type_str(da_parts)
        self._point_iec_type[addr_str] = iec_type_str

        # 记录叶子 DA 用于后续读写
        leaf_key = f"{ld_inst}/{ln_name}.{do_name}.{da_path}"
        leaf_da = self._da_map.get(leaf_key)
        if leaf_da is not None:
            self._point_attrs[addr_str] = leaf_da

        return ref

    @staticmethod
    def _infer_fc(frame_type: int, top_da: str) -> str:
        """根据帧类型和顶级 DA 名称推断 FC"""
        DA_FC_MAP = {
            "mag": "MX", "instMag": "MX", "cVal": "MX", "mxVal": "MX", "fCVal": "MX",
            "stVal": "ST", "ctlVal": "CO", "setVal": "CO",
            "q": "MX", "t": "MX", "dU": "DC",
            "origin": "OR", "subVal": "SV", "blkEna": "BL",
            "Oper": "CO", "SBOw": "CO", "Cancel": "CO", "SBO": "CO",
        }
        fc = DA_FC_MAP.get(top_da)
        if fc:
            return fc
        # 回退: 根据帧类型推断
        return {0: "MX", 1: "ST", 2: "CO", 3: "CO"}.get(frame_type, "MX")

    @staticmethod
    def _resolve_fc_const(fc: str):
        """将 FC 字符串解析为 pyiec61850 常量"""
        if not HAS_IEC61850:
            return None
        FC_CONST_MAP = {
            "MX": iec61850.IEC61850_FC_MX,
            "ST": iec61850.IEC61850_FC_ST,
            "CO": iec61850.IEC61850_FC_CO,
            "CF": iec61850.IEC61850_FC_CF,
            "DC": iec61850.IEC61850_FC_DC,
            "EX": iec61850.IEC61850_FC_EX,
            "SG": iec61850.IEC61850_FC_SG,
            "SR": iec61850.IEC61850_FC_SR,
            "OR": iec61850.IEC61850_FC_OR,
            "BL": iec61850.IEC61850_FC_BL,
            "SV": iec61850.IEC61850_FC_SV,
            "SP": iec61850.IEC61850_FC_SP,
            "SE": iec61850.IEC61850_FC_SE,
            "US": iec61850.IEC61850_FC_US,
            "MS": iec61850.IEC61850_FC_MS,
            "RP": iec61850.IEC61850_FC_RP,
        }
        return FC_CONST_MAP.get(fc)

    @staticmethod
    def _infer_iec_type(frame_type: int, da_parts: list) -> int:
        """根据帧类型和 DA 路径推断 IEC 61850 数据类型"""
        if not HAS_IEC61850:
            return 0

        leaf = da_parts[-1] if da_parts else ""
        parent = da_parts[-2] if len(da_parts) > 1 else ""

        # 浮点数: mag.f, cVal.mag.f, instMag.f, ctlVal (遥调), setVal, wVal
        if leaf == "f":
            return iec61850.IEC61850_FLOAT32
        if frame_type == 3 and leaf in ("ctlVal", "setVal", "wVal"):
            return iec61850.IEC61850_FLOAT32
        if frame_type == 0 and leaf in ("ctlVal", "setVal"):
            return iec61850.IEC61850_FLOAT32

        # 布尔值: stVal (遥信/遥控), ctlVal (遥控)
        if leaf in ("stVal", "ctlVal") and frame_type in (1, 2):
            return iec61850.IEC61850_BOOLEAN

        # 整数/枚举: stVal (ENS/ENC), validity, source, orCat
        if leaf in ("stVal",) and frame_type == 1:
            return iec61850.IEC61850_INT32
        if leaf in ("validity", "source", "orCat", "ctlNum", "TimeAccuracy"):
            return iec61850.IEC61850_INT32
        if leaf in ("seconds", "detailQuality"):
            return iec61850.IEC61850_INT32
        if leaf == "fraction":
            return iec61850.IEC61850_INT32U

        # 可见字符串: dU, d
        if leaf in ("dU", "d", "du"):
            return iec61850.IEC61850_VISIBLE_STRING_255

        # 八位字节串: orIdent
        if leaf in ("orIdent",):
            return iec61850.IEC61850_OCTET_STRING_64

        # 默认: 根据帧类型
        if frame_type == 0 or frame_type == 3:
            return iec61850.IEC61850_FLOAT32
        return iec61850.IEC61850_BOOLEAN

    def _add_standard_das(
        self,
        do_obj,
        do_key: str,
        fc: str,
        frame_type: int,
        da_parts: list,
    ) -> None:
        """为 DO 自动补充标准 DA 结构 (q, t, dU)

        许多 IEC 61850 客户端（如 .NET IEC61850Lib）在创建 FcDataNodeModel 时
        期望每个 DO 下都有 q (品质), t (时标), dU (描述) 等标准 DA。
        如果服务端模型缺少这些 DA，客户端在 GetDataValues 时集合索引会不匹配，
        导致 IndexOutOfRangeException。

        Args:
            do_obj: DO 的 pyiec61850 对象
            do_key: DO 的缓存键 (如 "MEAS/M0GGIO1.AnIn1")
            fc: 主 DO 的 FC（可能为空，为空时需要推断）
            frame_type: 帧类型
            da_parts: DA 路径分段列表
        """
        # 推断 FC: 优先用传入值, 否则从 DA 路径推断
        if not fc:
            fc = self._infer_fc(frame_type, da_parts[0] if da_parts else "")
        if not fc:
            fc = "MX"

        # 控制类型不需要 q/t/dU
        if fc in ("CO",):
            return

        # q/t 的 FC: 遥信=ST, 其他=MX
        qt_fc = "ST" if fc == "ST" else "MX"
        qt_fc_const = self._resolve_fc_const(qt_fc)
        dc_fc_const = self._resolve_fc_const("DC")

        # --- 1. q (品质) - 结构体 ---
        # IEC 61850-7-3: 品质包含 Validity、Quality Details(detailQuality)、Source、Test、OperatorBlocked
        q_key = f"{do_key}.q"
        if q_key not in self._do_map and qt_fc_const:
            q_obj = iec61850.DataObject_create("q", iec61850.toModelNode(do_obj), 0)
            self._do_map[q_key] = q_obj
            self._keep_alive.append(q_obj)

            # q 的子 BDA: Validity、Quality Details、Source、Test、OperatorBlocked
            # Quality Details (detailQuality) 的各项作为叶子 BDA 直接挂载在 q 下
            for bda_name, bda_type, iec_type_str in [
                ("validity", iec61850.IEC61850_INT32, "int32"),
                ("detailQuality", iec61850.IEC61850_INT32U, "uint32"),
                ("source", iec61850.IEC61850_INT32, "int32"),
                ("operatorBlocked", iec61850.IEC61850_BOOLEAN, "boolean"),
                ("test", iec61850.IEC61850_BOOLEAN, "boolean"),
            ]:
                bda = iec61850.DataAttribute_create(
                    bda_name, iec61850.toModelNode(q_obj),
                    bda_type, qt_fc_const, 0, 0, 0
                )
                self._keep_alive.append(bda)
                self._standard_bda_list.append((bda, bda_name, iec_type_str))

        # --- 2. t (时标) - 结构体 ---
        # IEC 61850-7-2: 时标包含 seconds, fraction, 以及时间质量字段
        t_key = f"{do_key}.t"
        if t_key not in self._do_map and qt_fc_const:
            t_obj = iec61850.DataObject_create("t", iec61850.toModelNode(do_obj), 0)
            self._do_map[t_key] = t_obj
            self._keep_alive.append(t_obj)

            for bda_name, bda_type, iec_type_str in [
                ("seconds", iec61850.IEC61850_INT32, "int32"),
                ("fraction", iec61850.IEC61850_INT32U, "uint32"),
                ("LeapSecondsKnown", iec61850.IEC61850_BOOLEAN, "boolean"),
                ("ClockedFailure", iec61850.IEC61850_BOOLEAN, "boolean"),
                ("ClockNotSynchronized", iec61850.IEC61850_BOOLEAN, "boolean"),
                ("TimeAccuracy", iec61850.IEC61850_INT32U, "uint32"),
            ]:
                bda = iec61850.DataAttribute_create(
                    bda_name, iec61850.toModelNode(t_obj),
                    bda_type, qt_fc_const, 0, 0, 0
                )
                self._keep_alive.append(bda)
                self._standard_bda_list.append((bda, bda_name, iec_type_str))

        # --- 3. dU (描述) - 可见字符串 ---
        du_key = f"{do_key}.dU"
        if du_key not in self._da_map and dc_fc_const:
            du_da = iec61850.DataAttribute_create(
                "dU", iec61850.toModelNode(do_obj),
                iec61850.IEC61850_VISIBLE_STRING_255,
                dc_fc_const, 0, 0, 0
            )
            self._da_map[du_key] = du_da
            self._keep_alive.append(du_da)

    def _init_standard_bda_defaults(self):
        """初始化标准子 DA 的默认值

        在服务器启动后调用，为所有 q/t/detailQuality 的子 DA 设置默认初始值，
        避免 IED Scout 等客户端读取时因值为空显示红色感叹号。
        """
        if not self._server or not self._is_running:
            return

        import time as time_module
        now_seconds = int(time_module.time())

        for da, name, iec_type in self._standard_bda_list:
            try:
                if iec_type == "int32":
                    # validity → 0 (good), source → 0 (process)
                    iec61850.IedServer_updateInt32AttributeValue(
                        self._server, da, 0
                    )
                elif iec_type == "boolean":
                    # test → False, operatorBlocked → False,
                    # detailQuality BDAs → False,
                    # LeapSecondsKnown → False, ClockedFailure → False,
                    # ClockNotSynchronized → False
                    iec61850.IedServer_updateBooleanAttributeValue(
                        self._server, da, False
                    )
                elif iec_type == "uint32":
                    # TimeAccuracy → 0
                    iec61850.IedServer_updateUnsignedAttributeValue(
                        self._server, da, 0
                    )
            except Exception as e:
                log.warning(
                    f"初始化标准 DA 默认值失败: {name}({iec_type}), error={e}"
                )

    def start(self):
        """启动 IEC 61850 MMS 服务器"""
        if self._is_running:
            return

        self._server = iec61850.IedServer_create(self._model)

        # 设置 MMS TCP 端口
        iec61850.IedServer_setServerIdentity(self._server, "EMS", self.model_name, "1.0")

        # 启用集成 GOOSE 发布服务
        try:
            iec61850.IedServer_setGooseInterfaceId(self._server, self._goose_interface)
            log.info(f"GOOSE 网络接口已设置: {self._goose_interface}")
        except Exception as e:
            log.warning(f"设置 GOOSE 网络接口失败 ({self._goose_interface}): {e}")
        try:
            iec61850.IedServer_enableGoosePublishing(self._server)
            log.info("GOOSE 发布服务已启用")
        except Exception as e:
            log.warning(f"启用 GOOSE 发布服务失败: {e}")

        self._is_running = True
        iec61850.IedServer_start(self._server, self.port)

        if iec61850.IedServer_isRunning(self._server):
            log.info(f"IEC 61850 服务器已启动, 端口: {self.port}, GOOSE 发布: 已启用")
            # 服务器启动后初始化 q/t 子 DA 的默认值，避免 IED Scout 读取时显示红色感叹号
            self._init_standard_bda_defaults()
            # 启动后设置所有已注册 GoCB 的 GoEna=TRUE
            self._enable_all_goose_cbs()
        else:
            self._is_running = False
            log.error(f"IEC 61850 服务器启动失败, 端口: {self.port}")

    def stop(self):
        """停止 IEC 61850 MMS 服务器"""
        if self._server and self._is_running:
            iec61850.IedServer_stop(self._server)
            iec61850.IedServer_destroy(self._server)
            self._server = None
            self._is_running = False
            log.info("IEC 61850 服务器已停止")

    def restart(self) -> bool:
        """重启 MMS 服务器，保留 IedModel"""
        if self._server:
            try:
                iec61850.IedServer_stop(self._server)
                iec61850.IedServer_destroy(self._server)
            except Exception:
                pass
        self._server = None
        self._is_running = False
        import time as _time
        _time.sleep(1)
        self._server = iec61850.IedServer_create(self._model)
        if not self._server:
            log.error("重启失败: IedServer_create 返回空")
            return False
        iec61850.IedServer_setServerIdentity(self._server, "EMS", self.model_name, "1.0")
        try:
            iec61850.IedServer_setGooseInterfaceId(self._server, self._goose_interface)
        except Exception:
            pass
        try:
            iec61850.IedServer_enableGoosePublishing(self._server)
        except Exception:
            pass
        self._is_running = True
        iec61850.IedServer_start(self._server, self.port)
        if iec61850.IedServer_isRunning(self._server):
            log.info(f"IEC 61850 服务器重启成功, 端口: {self.port}")
            self._init_standard_bda_defaults()
            self._enable_all_goose_cbs()
            return True
        else:
            self._is_running = False
            log.error(f"IEC 61850 服务器重启失败, 端口: {self.port}")
            return False

    @property
    def is_running(self) -> bool:
        if self._server:
            return iec61850.IedServer_isRunning(self._server)
        return False

    @staticmethod
    def _infer_iec_type_str(da_parts: list) -> str:
        """从 DA 路径推断 iec_type 字符串（用于 get/set_point_value）"""
        leaf = da_parts[-1] if da_parts else ""
        parent = da_parts[-2] if len(da_parts) > 1 else ""

        if leaf == "f":
            return "float"
        if leaf in ("ctlVal", "setVal", "wVal") and parent in ("APC", "setMag"):
            return "float"
        if leaf in ("stVal", "ctlVal", "subEna", "blkEna",
                     "LeapSecondsKnown", "ClockedFailure", "ClockNotSynchronized"):
            return "boolean"
        if leaf in ("validity", "source", "orCat", "ctlNum", "frVal", "actVal", "frValSec",
                     "TimeAccuracy", "seconds", "fraction", "detailQuality"):
            return "integer"
        if leaf in ("dU", "d", "du"):
            return "string"
        return "unknown"

    def get_point_value(self, address, fc: str = "") -> Any:
        """获取测点值

        Args:
            address: 测点地址 (简单地址或完整引用路径)
            fc: 功能约束 (为空时使用注册时的 FC)

        Returns:
            测点值 (float/int/bool/str)
        """
        if not self._server or not self._is_running:
            return 0

        addr_str = str(address)
        da = self._point_attrs.get(addr_str)
        if not da:
            log.warning(f"IEC61850 读取测点值时未找到 DataAttribute: address={address}")
            return 0

        # 类型安全检查：确保 da 是 SWIG 包装的 DataAttribute 对象
        if not hasattr(da, 'this'):
            log.error(f"IEC61850 数据属性对象类型错误: address={address}, type={type(da)}")
            return 0

        # 获取 iec_type
        iec_type = self._point_iec_type.get(addr_str, "unknown")

        try:
            if iec_type == "float":
                value = iec61850.IedServer_getFloatAttributeValue(self._server, da)
                return float(value) if value is not None else 0.0
            elif iec_type == "boolean":
                value = iec61850.IedServer_getBooleanAttributeValue(self._server, da)
                return bool(value) if value is not None else False
            elif iec_type == "integer":
                if hasattr(iec61850, 'IedServer_getIntegerAttributeValue'):
                    value = iec61850.IedServer_getIntegerAttributeValue(self._server, da)
                    return int(value) if value is not None else 0
                # 回退到布尔
                value = iec61850.IedServer_getBooleanAttributeValue(self._server, da)
                return bool(value) if value is not None else False
            elif iec_type == "string":
                if hasattr(iec61850, 'IedServer_getStringAttributeValue'):
                    value = iec61850.IedServer_getStringAttributeValue(self._server, da)
                    return str(value).strip() if value else ""
                return ""
            else:
                # unknown: 自动探测 - 先尝试浮点, 再布尔, 再整数
                try:
                    value = iec61850.IedServer_getFloatAttributeValue(self._server, da)
                    return float(value) if value is not None else 0.0
                except Exception:
                    pass
                try:
                    value = iec61850.IedServer_getBooleanAttributeValue(self._server, da)
                    return bool(value) if value is not None else False
                except Exception:
                    pass
                if hasattr(iec61850, 'IedServer_getIntegerAttributeValue'):
                    try:
                        value = iec61850.IedServer_getIntegerAttributeValue(self._server, da)
                        return int(value) if value is not None else 0
                    except Exception:
                        pass
                return 0
        except Exception as e:
            log.error(f"IEC61850 调用底层获取值函数失败: address={address}, error={e}")
            return 0

    def set_point_value(self, address, value: Any, fc: str = "") -> None:
        """设置测点值

        Args:
            address: 测点地址 (简单地址或完整引用路径)
            value: 要设置的值
            fc: 功能约束 (为空时使用注册时的 FC)
        """
        if not self._server or not self._is_running:
            return

        addr_str = str(address)
        da = self._point_attrs.get(addr_str)
        if not da:
            log.warning(f"IEC61850 设置测点值时未找到 DataAttribute: address={address}")
            return

        if not hasattr(da, 'this'):
            log.error(f"IEC61850 数据属性对象类型错误(设置值): address={address}, type={type(da)}")
            return

        # 获取 iec_type
        iec_type = self._point_iec_type.get(addr_str, "unknown")

        try:
            if isinstance(value, str) or iec_type == "string":
                # 字符串类型 (如 dU 描述)
                if hasattr(iec61850, 'IedServer_updateStringAttributeValue'):
                    iec61850.IedServer_updateStringAttributeValue(
                        self._server, da, str(value)
                    )
                else:
                    log.warning(f"IEC61850 不支持字符串写入: address={address}")
            elif iec_type == "float":
                iec61850.IedServer_updateFloatAttributeValue(
                    self._server, da, float(value)
                )
            elif iec_type == "integer":
                if isinstance(value, int) and not isinstance(value, bool):
                    if hasattr(iec61850, 'IedServer_updateIntegerAttributeValue'):
                        iec61850.IedServer_updateIntegerAttributeValue(
                            self._server, da, int(value)
                        )
                    else:
                        iec61850.IedServer_updateBooleanAttributeValue(
                            self._server, da, bool(value)
                        )
                else:
                    iec61850.IedServer_updateBooleanAttributeValue(
                        self._server, da, bool(value)
                    )
            elif iec_type == "boolean":
                iec61850.IedServer_updateBooleanAttributeValue(
                    self._server, da, bool(value)
                )
            else:
                # unknown: 根据 value 类型选择
                if isinstance(value, float):
                    iec61850.IedServer_updateFloatAttributeValue(
                        self._server, da, float(value)
                    )
                elif isinstance(value, bool):
                    iec61850.IedServer_updateBooleanAttributeValue(
                        self._server, da, bool(value)
                    )
                elif isinstance(value, int):
                    if hasattr(iec61850, 'IedServer_updateIntegerAttributeValue'):
                        iec61850.IedServer_updateIntegerAttributeValue(
                            self._server, da, int(value)
                        )
                    else:
                        iec61850.IedServer_updateBooleanAttributeValue(
                            self._server, da, bool(value)
                        )
        except Exception as e:
            log.error(f"IEC61850 调用底层设置值函数失败: address={address}, value={value}, error={e}")

    def browse_logical_devices(self) -> list[str]:
        """浏览服务端数据模型的逻辑设备列表

        返回所有已创建的逻辑设备名称。
        默认 GenericLD 仅在添加了简单地址测点后才存在，
        ICD 导入模式的 LD 则动态创建。

        Returns:
            逻辑设备名称列表，如 ["MEAS", "CTRL"] 或 ["GenericLD"]
        """
        ld_list = []
        for ld_inst in self._ld_map:
            prefix = f"{ld_inst}/"
            if any(key.startswith(prefix) for key in self._ln_map):
                ld_list.append(ld_inst)
        return ld_list

    def browse_logical_nodes(self, ld_inst: str) -> list[str]:
        """浏览指定逻辑设备下的逻辑节点列表

        对于动态 LD (ICD 导入), 返回所有 LN (包括 LLN0);
        对于简单地址模式的默认 LD, 返回所有 LN (不含内部固定节点过滤)。

        Args:
            ld_inst: 逻辑设备实例名，如 "MEAS"

        Returns:
            逻辑节点名称列表，如 ["LLN0", "M0GGIO1", "METMMXU1"]
        """
        ln_names = []
        prefix = f"{ld_inst}/"
        for key in self._ln_map:
            if key.startswith(prefix):
                ln_name = key[len(prefix):]
                ln_names.append(ln_name)
        return sorted(ln_names)

    def browse_data_objects(self, ld_inst: str, ln_name: str) -> list[dict]:
        """浏览指定逻辑节点下的数据对象列表

        从 _point_refs 中提取指定 LN 下的 DO 名称和类型。

        Args:
            ld_inst: 逻辑设备实例名, 如 "MEAS"
            ln_name: 逻辑节点名称, 如 "M0GGIO1"

        Returns:
            DO 信息列表, 如 [{"name": "AnIn1", "frame_type": 0}, ...]
        """
        # 从 _point_refs 中提取匹配的 DO
        do_map: Dict[str, Optional[int]] = {}  # do_name -> frame_type
        prefix = f"{ld_inst}/{ln_name}."

        for (address, frame_type), ref in self._point_refs.items():
            if not isinstance(address, str) or '/' not in address:
                continue
            parsed = _parse_ref(address)
            if parsed and parsed[0] == ld_inst and parsed[1] == ln_name:
                do_name = parsed[2]
                if do_name not in do_map:
                    do_map[do_name] = frame_type

        return [{"name": name, "frame_type": ft} for name, ft in sorted(do_map.items())]

    def browse_data_attributes(self, ld_inst: str, ln_name: str, do_name: str) -> list[dict]:
        """浏览指定数据对象下的数据属性列表

        从 _point_refs 中提取指定 DO 下的 DA 路径信息。

        Args:
            ld_inst: 逻辑设备实例名
            ln_name: 逻辑节点名称
            do_name: 数据对象名称

        Returns:
            DA 信息列表, 如 [{"name": "mag", "path": "mag.f", "fc": "MX", "type": "Float32"}, ...]
        """
        fc_map = {0: "MX", 1: "ST", 2: "CO", 3: "CO"}
        type_map = {0: "Float32", 1: "Boolean", 2: "Boolean", 3: "Float32"}

        da_map: Dict[str, dict] = {}  # da_path -> info
        for (address, frame_type), ref in self._point_refs.items():
            if not isinstance(address, str) or '/' not in address:
                continue
            parsed = _parse_ref(address)
            if parsed and parsed[0] == ld_inst and parsed[1] == ln_name and parsed[2] == do_name:
                da_path = parsed[3]
                if da_path and da_path not in da_map:
                    # 提取第一层 DA 名称
                    first_da = da_path.split('.')[0]
                    da_map[da_path] = {
                        "name": first_da,
                        "path": da_path,
                        "fc": fc_map.get(frame_type, ""),
                        "type": type_map.get(frame_type, ""),
                    }

        return sorted(da_map.values(), key=lambda x: x["path"])

    def add_goose_control_block(
        self,
        name: str,
        app_id: int,
        data_set_ref: str,
        conf_rev: int,
        go_id: str = "",
        min_time: int = 10,
        max_time: int = 1000,
        ld_inst: str = None,
        entries: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """在 LLN0 下创建 GSEControlBlock，使 MMS 客户端可发现 GOOSE

        使用 libIEC61850 的 GSEControlBlock_create 创建标准 GoCB 结构，
        同时将 FCDA 条目添加到 DataSet 中，确保客户端能发现完整的数据集结构。

        Args:
            name: GSEControlBlock 名称 (如 "gcb1")
            app_id: APPID
            data_set_ref: DataSet 引用路径 (如 "LD0/LLN0$dsGOOSE1")
            conf_rev: 配置修订号
            go_id: GOOSE 标识符
            min_time: 最小重发时间 (ms)
            max_time: 最大重发时间 (ms)
            ld_inst: 逻辑设备实例名，默认使用 self.ld_name
            entries: 数据集条目列表，每个条目包含 name (fcda_ref), value, iec_type

        Returns:
            是否成功
        """
        if not self._model:
            log.warning("无法添加 GSEControlBlock: 模型未初始化")
            return False

        ld_inst = ld_inst or self.ld_name
        lln0_key = f"{ld_inst}/LLN0"
        lln0 = self._ln_map.get(lln0_key)
        if lln0 is None and ld_inst == self.ld_name:
            self._ensure_base_ld()
            lln0 = self._lln0
        if not lln0:
            log.warning(f"无法添加 GSEControlBlock: LLN0 未找到 (ld_inst={ld_inst})")
            return False

        try:
            # 1. 创建 DataSet (数据集)
            ds_name = data_set_ref.split("$")[-1] if "$" in data_set_ref else f"ds{name}"
            data_set = iec61850.DataSet_create(ds_name, lln0)
            if not data_set:
                log.warning(f"创建 DataSet {ds_name} 失败")
            else:
                self._keep_alive.append(data_set)
                # 为 DataSet 添加 FCDA 条目
                self._add_fcda_entries_to_dataset(data_set, entries, ld_inst)

            # 2. 创建 GSEControlBlock (标准 libIEC61850 GoCB)
            app_id_str = f"{app_id:04X}" if isinstance(app_id, int) else str(app_id)
            gse_cb = iec61850.GSEControlBlock_create(
                name, lln0, app_id_str,
                data_set_ref, conf_rev,
                False, min_time, max_time,
            )
            if not gse_cb:
                log.warning(f"创建 GSEControlBlock {name} 失败")
                return False
            self._keep_alive.append(gse_cb)

            # 3. 添加物理通信地址 (MAC/APPID/VLAN)
            try:
                iec61850.GSEControlBlock_addPhyComAddress(gse_cb, None)
            except Exception as phy_err:
                log.debug(f"添加 PhyComAddress 不可用 (非致命): {phy_err}")

            # 4. 记录 GoCB 信息并设置 GoEna=TRUE（如果服务器已在运行）
            self._goose_cb_list.append({
                "ld_inst": ld_inst,
                "name": name,
                "app_id": app_id,
            })
            if self._server and self._is_running:
                self._enable_single_goose_cb(ld_inst, name)

            log.info(
                f"GSEControlBlock 已添加到 MMS 数据模型: "
                f"name={name}, app_id=0x{app_id:04X}, "
                f"dataSet={data_set_ref}, entries={len(entries or [])}"
            )
            return True
        except Exception as e:
            log.error(f"添加 GSEControlBlock 失败: {e}", exc_info=True)
            return False

    def _add_fcda_entries_to_dataset(
        self,
        data_set,
        entries: Optional[List[Dict[str, Any]]],
        default_ld_inst: str,
    ) -> int:
        """向 DataSet 添加 FCDA 条目，使客户端可发现数据集中的成员

        Args:
            data_set: pyiec61850 DataSet 对象
            entries: 条目列表，每个条目含 name(fcda_ref), iec_type
            default_ld_inst: 默认 LD 实例名（当 FCDA 引用不包含 LD 时使用）

        Returns:
            成功添加的 FCDA 数量
        """
        if not entries:
            return 0

        # FC 字符串 -> pyiec61850 常量映射
        fc_str_to_const = {
            "MX": getattr(iec61850, "IEC61850_FC_MX", None),
            "ST": getattr(iec61850, "IEC61850_FC_ST", None),
            "CO": getattr(iec61850, "IEC61850_FC_CO", None),
            "CF": getattr(iec61850, "IEC61850_FC_CF", None),
            "DC": getattr(iec61850, "IEC61850_FC_DC", None),
            "SP": getattr(iec61850, "IEC61850_FC_SP", None),
            "SV": getattr(iec61850, "IEC61850_FC_SV", None),
            "SG": getattr(iec61850, "IEC61850_FC_SG", None),
            "SR": getattr(iec61850, "IEC61850_FC_SR", None),
            "OR": getattr(iec61850, "IEC61850_FC_OR", None),
            "BL": getattr(iec61850, "IEC61850_FC_BL", None),
            "MX": getattr(iec61850, "IEC61850_FC_MX", None),
        }

        # 类型 -> FC 推断映射
        iec_type_to_fc = {
            "boolean": "ST",
            "float": "MX",
            "integer": "ST",
            "string": "DC",
        }

        # 根据 DA 名称推断 FC
        da_to_fc = {
            "stVal": "ST", "ctlVal": "CO", "mag": "MX",
            "f": "MX", "q": "MX", "t": "MX", "dU": "DC",
            "setVal": "CO", "setMag": "MX",
            "seconds": "MX", "fraction": "MX",
        }

        added_count = 0

        for entry in entries:
            try:
                fcda_ref = entry.get("name", "")
                if not fcda_ref:
                    continue

                # 解析 FCDA 引用: "LD0/LLN0.GoCB1.stVal"
                # 格式: {ld_inst}/{ln_name}.{do_name}.{da_name}
                parsed = _parse_ref(fcda_ref)
                if not parsed:
                    log.warning(f"FCDA: 无法解析引用路径: {fcda_ref}, 跳过")
                    continue

                fcda_ld, fcda_ln, fcda_do, fcda_da = parsed
                if not fcda_do:
                    continue

                # 推断 FC
                fc_str = None
                iec_type = entry.get("iec_type", "")
                if iec_type and iec_type in iec_type_to_fc:
                    fc_str = iec_type_to_fc[iec_type]
                if not fc_str:
                    # 从 DA 名称推断
                    da_first = fcda_da.split(".")[0] if fcda_da else ""
                    fc_str = da_to_fc.get(da_first)
                if not fc_str:
                    fc_str = "MX"  # 默认

                fc_const = fc_str_to_const.get(fc_str)
                if fc_const is None:
                    fc_const = iec61850.IEC61850_FC_MX

                # 尝试通过不同的 API 添加 FCDA 条目
                entry_added = False

                # 方法 1: Fcda_create + DataSet_addEntry (标准 API)
                if hasattr(iec61850, "Fcda_create") and hasattr(iec61850, "DataSet_addEntry"):
                    try:
                        fcda_obj = iec61850.Fcda_create(
                            fcda_ld, "", fcda_ln, "",
                            fcda_do, fcda_da, fc_const,
                        )
                        if fcda_obj:
                            iec61850.DataSet_addEntry(data_set, fcda_obj)
                            entry_added = True
                    except Exception as api_err:
                        log.debug(f"Fcda_create/DataSet_addEntry 失败: {api_err}")

                # 方法 2: DataSet_addEntry 只接受引用字符串
                if not entry_added and hasattr(iec61850, "DataSet_addEntry"):
                    try:
                        # 构造 FCDA 引用字符串
                        ref_parts = [fcda_ld, fcda_ln]
                        if fcda_do:
                            ref_parts.append(fcda_do)
                        if fcda_da:
                            ref_parts.append(fcda_da)
                        fcda_ref_str = "/".join(ref_parts[:2]) + "." + ".".join(ref_parts[2:])
                        iec61850.DataSet_addEntry(data_set, fcda_ref_str)
                        entry_added = True
                    except Exception as api_err:
                        log.debug(f"DataSet_addEntry(字符串) 失败: {api_err}")

                # 方法 3: DataSet_addFcda
                if not entry_added and hasattr(iec61850, "DataSet_addFcda"):
                    try:
                        iec61850.DataSet_addFcda(
                            data_set, fcda_ld, "",
                            fcda_ln, "", fcda_do, fcda_da, fc_const,
                        )
                        entry_added = True
                    except Exception as api_err:
                        log.debug(f"DataSet_addFcda 失败: {api_err}")

                if entry_added:
                    added_count += 1
                else:
                    log.warning(
                        f"无法为 DataSet 添加 FCDA 条目: {fcda_ref} "
                        f"(当前 pyiec61850 版本可能不支持 DataSet_addEntry API)"
                    )

            except Exception as e:
                log.debug(f"添加 FCDA 条目异常: {e}")

        if added_count > 0:
            log.info(f"DataSet 已添加 {added_count}/{len(entries)} 个 FCDA 条目")
        elif entries:
            log.info(
                f"DataSet FCDA 条目添加失败 ({len(entries)} 个), "
                f"但 GoCB 本身已被创建, 不影响 MMS 浏览发现 "
                f"(GoCB 节点已存在于 LLN0 下)"
            )

        return added_count

    def _enable_single_goose_cb(self, ld_inst: str, cb_name: str):
        """设置单个 GoCB 的 GoEna=TRUE"""
        if not self._server or not self._is_running:
            return
        conn = None
        try:
            conn = iec61850.IedConnection_create()
            result = iec61850.IedConnection_connect(conn, "127.0.0.1", self.port)
            error = result if not isinstance(result, (list, tuple)) else result[1]
            if error != 0:
                log.warning(f"设置 GoCB GoEna 时无法连接到自身: 127.0.0.1:{self.port}, error={error}")
                return
            ref = f"{self.model_name}{ld_inst}/LLN0.{cb_name}.GoEna"
            iec61850.IedConnection_writeBooleanValue(
                conn, ref, iec61850.IEC61850_FC_GO, True
            )
            log.info(f"GoCB GoEna 已设为 TRUE: ld={ld_inst}, cb={cb_name}")
        except Exception as e:
            log.warning(f"设置 GoCB GoEna 失败 (ld={ld_inst}, cb={cb_name}): {e}")
        finally:
            if conn:
                try:
                    iec61850.IedConnection_destroy(conn)
                except Exception as destroy_err:
                    log.debug(f"销毁 IedConnection 失败: {destroy_err}")

    def _enable_all_goose_cbs(self):
        """服务器启动后，设置所有已注册 GoCB 的 GoEna=TRUE"""
        if not self._server or not self._is_running:
            return
        for cb_info in self._goose_cb_list:
            self._enable_single_goose_cb(
                cb_info["ld_inst"], cb_info["name"]
            )

    def set_goose_interface(self, interface: str):
        """设置 GOOSE 网络接口"""
        self._goose_interface = interface
        if self._server:
            try:
                iec61850.IedServer_setGooseInterfaceId(self._server, interface)
            except Exception:
                pass

    def destroy(self):
        """销毁服务器和模型"""
        self.stop()
        if self._model:
            iec61850.IedModel_destroy(self._model)
            self._model = None
