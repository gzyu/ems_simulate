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

        # 地址 -> MMS 引用路径 的映射
        self._point_refs: Dict[Tuple[Union[int, str], int], str] = {}  # (address, frame_type) -> ref
        # 地址 -> DataAttribute 对象的映射
        self._point_attrs: Dict[Tuple[Union[int, str], int], Any] = {}

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

        # 注册默认 LD
        self._ld_map[self.ld_name] = self._ld

    def _get_or_create_ld(self, ld_inst: str):
        """获取或创建逻辑设备"""
        if ld_inst in self._ld_map:
            return self._ld_map[ld_inst]
        ld = iec61850.LogicalDevice_create(ld_inst, self._model)
        iec61850.LogicalNode_create("LLN0", ld)
        self._ld_map[ld_inst] = ld
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

    def add_point(self, address, frame_type: int) -> Optional[str]:
        """添加测点到数据模型

        Args:
            address: 测点地址，支持两种格式:
                     - 简单地址 (int/str 不含 '/'): 使用 MMXU1/GGIO1/GGIO2 固定结构
                     - 完整引用路径 (str 含 '/'): 按 ICD 结构动态创建 LD/LN/DO/DA
            frame_type: 帧类型 (0=遥测, 1=遥信, 2=遥控, 3=遥调)

        Returns:
            MMS 引用路径，用于后续读写
        """
        key = (address, frame_type)
        if key in self._point_refs:
            return self._point_refs[key]  # 已存在

        if _is_full_ref(address):
            return self._add_point_from_ref(address, frame_type)
        else:
            return self._add_point_simple(address, frame_type)

    def _add_point_simple(self, address, frame_type: int) -> Optional[str]:
        """简单地址模式: 使用固定的 MMXU1/GGIO1/GGIO2 结构添加测点"""
        key = (address, frame_type)

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
            self._point_attrs[key] = da
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
            self._point_attrs[key] = da
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
            self._point_attrs[key] = da
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
            self._point_attrs[key] = da
            self._keep_alive.extend([do_name, do, da])

        if ref:
            self._point_refs[key] = ref
            log.info(f"IEC61850 已成功添加测点(简单模式): address={address}, frame_type={frame_type}, ref={ref}")
        else:
            log.error(f"IEC61850 添加测点失败: address={address}, frame_type={frame_type}")

        return ref

    def _add_point_from_ref(self, address: str, frame_type: int) -> Optional[str]:
        """完整引用路径模式: 按 ICD 文件结构动态创建 LD/LN/DO/DA

        address 格式: {ld_inst}/{ln_name}.{do_name}.{da_path}
        例如: "MEAS/M0GGIO1.AnIn1.mag.f"
        """
        parsed = _parse_ref(address)
        if not parsed:
            log.error(f"IEC61850 无法解析引用路径: {address}")
            return None

        ld_inst, ln_name, do_name, da_path = parsed
        key = (address, frame_type)

        # 获取或创建逻辑节点
        ln = self._get_or_create_ln(ld_inst, ln_name)

        # 根据 frame_type 创建对应的 DO 和 DA 结构
        ref = None
        if frame_type == 0:  # 遥测 - MV (mag.f 结构)
            do = iec61850.DataObject_create(do_name, iec61850.toModelNode(ln), 0)
            mag = iec61850.DataObject_create("mag", iec61850.toModelNode(do), 0)
            da = iec61850.DataAttribute_create(
                "f", iec61850.toModelNode(mag),
                iec61850.IEC61850_FLOAT32,
                FC_MX, 0, 0, 0
            )
            ref = f"{self.model_name}{ld_inst}/{ln_name}.{do_name}.mag.f"
            self._point_attrs[key] = da
            self._keep_alive.extend([do, mag, da])

        elif frame_type == 1:  # 遥信 - SPS (stVal)
            do = iec61850.DataObject_create(do_name, iec61850.toModelNode(ln), 0)
            da = iec61850.DataAttribute_create(
                "stVal", iec61850.toModelNode(do),
                iec61850.IEC61850_BOOLEAN,
                FC_ST, 0, 0, 0
            )
            ref = f"{self.model_name}{ld_inst}/{ln_name}.{do_name}.stVal"
            self._point_attrs[key] = da
            self._keep_alive.extend([do, da])

        elif frame_type == 2:  # 遥控 - SPC (ctlVal)
            do = iec61850.DataObject_create(do_name, iec61850.toModelNode(ln), 0)
            da = iec61850.DataAttribute_create(
                "ctlVal", iec61850.toModelNode(do),
                iec61850.IEC61850_BOOLEAN,
                FC_CO, 0, 0, 0
            )
            ref = f"{self.model_name}{ld_inst}/{ln_name}.{do_name}.ctlVal"
            self._point_attrs[key] = da
            self._keep_alive.extend([do, da])

        elif frame_type == 3:  # 遥调 - APC (ctlVal)
            do = iec61850.DataObject_create(do_name, iec61850.toModelNode(ln), 0)
            da = iec61850.DataAttribute_create(
                "ctlVal", iec61850.toModelNode(do),
                iec61850.IEC61850_FLOAT32,
                FC_CO, 0, 0, 0
            )
            ref = f"{self.model_name}{ld_inst}/{ln_name}.{do_name}.ctlVal"
            self._point_attrs[key] = da
            self._keep_alive.extend([do, da])

        if ref:
            self._point_refs[key] = ref
            log.info(f"IEC61850 已成功添加测点(引用模式): address={address}, frame_type={frame_type}, ref={ref}")
        else:
            log.error(f"IEC61850 添加测点失败(引用模式): address={address}, frame_type={frame_type}")

        return ref

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

    def get_point_value(self, address, frame_type: int) -> Any:
        """获取测点值

        Args:
            address: 测点地址 (简单地址或完整引用路径)
            frame_type: 帧类型

        Returns:
            测点值 (float 或 bool)
        """
        if not self._server or not self._is_running:
            return 0

        key = (address, frame_type)
        da = self._point_attrs.get(key)
        if not da:
            log.warning(f"IEC61850 读取测点值时未找到 DataAttribute: address={address}, frame_type={frame_type}")
            return 0

        # 类型安全检查：确保 da 是 SWIG 包装的 DataAttribute 对象
        if not hasattr(da, 'this'):
            log.error(f"IEC61850 数据属性对象类型错误: address={address}, type={type(da)}")
            return 0

        try:
            if frame_type == 0 or frame_type == 3:  # float
                value = iec61850.IedServer_getFloatAttributeValue(self._server, da)
                return float(value) if value is not None else 0.0
            elif frame_type == 1 or frame_type == 2:  # bool
                value = iec61850.IedServer_getBooleanAttributeValue(self._server, da)
                return bool(value) if value is not None else False
        except Exception as e:
            log.error(f"IEC61850 调用底层获取值函数失败: address={address}, error={e}")
            return 0

    def set_point_value(self, address, value: Any, frame_type: int) -> None:
        """设置测点值

        Args:
            address: 测点地址 (简单地址或完整引用路径)
            value: 要设置的值
            frame_type: 帧类型
        """
        if not self._server or not self._is_running:
            return

        key = (address, frame_type)
        da = self._point_attrs.get(key)
        if not da:
            log.warning(f"IEC61850 设置测点值时未找到 DataAttribute: address={address}, frame_type={frame_type}")
            return

        if not hasattr(da, 'this'):
            log.error(f"IEC61850 数据属性对象类型错误(设置值): address={address}, type={type(da)}")
            return

        try:
            if frame_type == 0 or frame_type == 3:  # float
                iec61850.IedServer_updateFloatAttributeValue(
                    self._server, da, float(value)
                )
            elif frame_type == 1 or frame_type == 2:  # bool
                iec61850.IedServer_updateBooleanAttributeValue(
                    self._server, da, bool(value)
                )
        except Exception as e:
            log.error(f"IEC61850 调用底层设置值函数失败: address={address}, value={value}, error={e}")

    def destroy(self):
        """销毁服务器和模型"""
        self.stop()
        if self._model:
            iec61850.IedModel_destroy(self._model)
            self._model = None
