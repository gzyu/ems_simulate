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

        # 保持底层 C 对象的 Python 引用，防止被垃圾回收导致崩溃
        self._keep_alive: List[Any] = []

        self._build_base_model()

    def _build_base_model(self):
        """构建基础 IED 模型（只含 LLN0）"""
        self._model = iec61850.IedModel_create(self.model_name)
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
        addr_str = str(address)

        # 确保 address 是合法的 IEC 61850 节点名称 (不能包含 . / \ 等)
        safe_addr = str(address).replace('.', '_').replace('/', '_').replace('\\', '_').replace('-', '_')

        ref = None
        if frame_type == 0:  # 遥测 - MV (Measured Value)
            do_name = f"MV_{safe_addr}"
            do = iec61850.DataObject_create(do_name, iec61850.toModelNode(self._mmxu), 0)
            mag = iec61850.DataObject_create("mag", iec61850.toModelNode(do), 0)
            da = iec61850.DataAttribute_create(
                "f", iec61850.toModelNode(mag),
                iec61850.IEC61850_FLOAT32,
                FC_MX, 0, 0, 0
            )
            ref = f"{self.model_name}{self.ld_name}/MMXU1.{do_name}.mag.f"
            self._point_attrs[addr_str] = da
            self._point_fc[addr_str] = "MX"
            self._point_iec_type[addr_str] = "float"
            self._keep_alive.extend([do_name, do, mag, da])

        elif frame_type == 1:  # 遥信 - SPS (Single Point Status)
            do_name = f"SPS_{safe_addr}"
            do = iec61850.DataObject_create(do_name, iec61850.toModelNode(self._ggio1), 0)
            da = iec61850.DataAttribute_create(
                "stVal", iec61850.toModelNode(do),
                iec61850.IEC61850_BOOLEAN,
                FC_ST, 0, 0, 0
            )
            ref = f"{self.model_name}{self.ld_name}/GGIO1.{do_name}.stVal"
            self._point_attrs[addr_str] = da
            self._point_fc[addr_str] = "ST"
            self._point_iec_type[addr_str] = "boolean"
            self._keep_alive.extend([do_name, do, da])

        elif frame_type == 2:  # 遥控 - SPC (Single Point Control)
            do_name = f"SPC_{safe_addr}"
            do = iec61850.DataObject_create(do_name, iec61850.toModelNode(self._ggio1), 0)
            da = iec61850.DataAttribute_create(
                "ctlVal", iec61850.toModelNode(do),
                iec61850.IEC61850_BOOLEAN,
                FC_CO, 0, 0, 0
            )
            ref = f"{self.model_name}{self.ld_name}/GGIO1.{do_name}.ctlVal"
            self._point_attrs[addr_str] = da
            self._point_fc[addr_str] = "CO"
            self._point_iec_type[addr_str] = "boolean"
            self._keep_alive.extend([do_name, do, da])

        elif frame_type == 3:  # 遥调 - APC (Analog Point Control)
            do_name = f"APC_{safe_addr}"
            do = iec61850.DataObject_create(do_name, iec61850.toModelNode(self._ggio2), 0)
            da = iec61850.DataAttribute_create(
                "ctlVal", iec61850.toModelNode(do),
                iec61850.IEC61850_FLOAT32,
                FC_CO, 0, 0, 0
            )
            ref = f"{self.model_name}{self.ld_name}/GGIO2.{do_name}.ctlVal"
            self._point_attrs[addr_str] = da
            self._point_fc[addr_str] = "CO"
            self._point_iec_type[addr_str] = "float"
            self._keep_alive.extend([do_name, do, da])

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

        # === 1. 获取或创建 DO (去重) ===
        do_key = f"{ld_inst}/{ln_name}.{do_name}"
        do_obj = self._do_map.get(do_key)
        if do_obj is None:
            do_obj = iec61850.DataObject_create(do_name, iec61850.toModelNode(ln), 0)
            self._do_map[do_key] = do_obj
            self._keep_alive.append(do_obj)

        # === 2. 根据 da_path 创建 DA 层级结构 (去重) ===
        # da_path 示例: "mag.f", "stVal", "q.validity", "Oper.ctlVal", "cVal.mag.f"
        da_parts = da_path.split('.')

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
        if leaf in ("validity", "source", "orCat", "ctlNum"):
            return iec61850.IEC61850_INT32

        # 时间戳: seconds
        if leaf == "seconds":
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

    def start(self):
        """启动 IEC 61850 MMS 服务器"""
        if self._is_running:
            return

        self._server = iec61850.IedServer_create(self._model)

        # 设置 MMS TCP 端口
        iec61850.IedServer_setServerIdentity(self._server, "EMS", self.model_name, "1.0")

        self._is_running = True
        iec61850.IedServer_start(self._server, self.port)

        if iec61850.IedServer_isRunning(self._server):
            log.info(f"IEC 61850 服务器已启动, 端口: {self.port}")
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
        if leaf in ("stVal", "ctlVal", "subEna", "blkEna"):
            return "boolean"
        if leaf in ("validity", "source", "orCat", "ctlNum", "frVal", "actVal", "frValSec"):
            return "integer"
        if leaf in ("seconds", "fraction"):
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

        跳过简单地址模式的默认 LD (self.ld_name)，
        因为 LLN0/MMXU1/GGIO1/GGIO2 只是内部实现细节。

        Returns:
            逻辑设备名称列表，如 ["MEAS", "CTRL"]
        """
        ld_list = []
        for ld_inst in self._ld_map:
            if ld_inst == self.ld_name:
                continue  # 跳过简单地址模式的默认 LD
            prefix = f"{ld_inst}/"
            if any(key.startswith(prefix) for key in self._ln_map):
                ld_list.append(ld_inst)
        return ld_list

    def browse_logical_nodes(self, ld_inst: str) -> list[str]:
        """浏览指定逻辑设备下的逻辑节点列表

        对于动态 LD (ICD 导入), 返回所有 LN (包括 LLN0);
        对于简单地址模式的默认 LD, 不应被调用 (browse_logical_devices 已排除)。

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
                # 排除简单地址模式的固定节点
                if ln_name in ("MMXU1", "GGIO1", "GGIO2"):
                    continue
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

    def destroy(self):
        """销毁服务器和模型"""
        self.stop()
        if self._model:
            iec61850.IedModel_destroy(self._model)
            self._model = None
