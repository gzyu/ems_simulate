"""
IEC 61850 MMS 服务端封装
基于 pyiec61850 (libiec61850 Python bindings) 实现动态数据模型的 IED Server
"""

import threading
import time
from typing import Any, Dict, List, Optional, Tuple
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


class IEC61850Server:
    """IEC 61850 MMS 服务端

    使用 pyiec61850 动态创建 IedModel 和 IedServer，
    将测点映射到 IEC 61850 数据模型。

    数据模型结构:
        IedModel (model_name)
          └── LogicalDevice (ld_name)
                ├── LLN0 (必需的逻辑节点)
                ├── MMXU1 (遥测 - Measured Value)
                │     └── MV_{address}.mag.f (float)
                ├── GGIO1 (遥信/遥控 - Generic IO)
                │     ├── SPS_{address}.stVal (bool - 遥信)
                │     └── SPC_{address}.ctlVal (bool - 遥控)
                └── GGIO2 (遥调 - Analog Control)
                      └── APC_{address}.ctlVal (float - 遥调)
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

        # 逻辑节点引用
        self._lln0 = None
        self._mmxu = None   # 遥测逻辑节点
        self._ggio1 = None  # 遥信/遥控逻辑节点
        self._ggio2 = None  # 遥调逻辑节点

        # 地址 -> MMS 引用路径 的映射
        self._point_refs: Dict[Tuple[int, int], str] = {}  # (address, frame_type) -> ref
        # 地址 -> DataAttribute 对象的映射
        self._point_attrs: Dict[Tuple[int, int], Any] = {}

        # 保持底层 C 对象的 Python 引用，防止被垃圾回收导致崩溃
        self._keep_alive: List[Any] = []

        self._build_base_model()

    def _build_base_model(self):
        """构建基础 IED 模型（只含 LLN0）"""
        self._model = iec61850.IedModel_create(self.model_name)
        self._ld = iec61850.LogicalDevice_create(self.ld_name, self._model)
        self._lln0 = iec61850.LogicalNode_create("LLN0", self._ld)

        # 预创建逻辑节点
        self._mmxu = iec61850.LogicalNode_create("MMXU1", self._ld)
        self._ggio1 = iec61850.LogicalNode_create("GGIO1", self._ld)
        self._ggio2 = iec61850.LogicalNode_create("GGIO2", self._ld)

    def add_point(self, address: int, frame_type: int) -> Optional[str]:
        """添加测点到数据模型

        Args:
            address: 测点地址
            frame_type: 帧类型 (0=遥测, 1=遥信, 2=遥控, 3=遥调)

        Returns:
            MMS 引用路径，用于后续读写
        """
        key = (address, frame_type)
        if key in self._point_refs:
            return self._point_refs[key]  # 已存在

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
            ref = f"{self.ld_name}/MMXU1.{do_name}.mag.f"
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
            ref = f"{self.ld_name}/GGIO1.{do_name}.stVal"
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
            ref = f"{self.ld_name}/GGIO1.{do_name}.ctlVal"
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
            ref = f"{self.ld_name}/GGIO2.{do_name}.ctlVal"
            self._point_attrs[key] = da
            self._keep_alive.extend([do_name, do, da])

        if ref:
            self._point_refs[key] = ref
            log.debug(f"IEC61850 添加测点: address={address}, frame_type={frame_type}, ref={ref}")

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

    def get_point_value(self, address: int, frame_type: int) -> Any:
        """获取测点值

        Args:
            address: 测点地址
            frame_type: 帧类型

        Returns:
            测点值 (float 或 bool)
        """
        if not self._server or not self._is_running:
            return 0

        key = (address, frame_type)
        da = self._point_attrs.get(key)
        if not da:
            return 0

        try:
            if frame_type == 0 or frame_type == 3:  # float
                value = iec61850.IedServer_getFloatAttributeValue(self._server, da)
                return float(value) if value is not None else 0.0
            elif frame_type == 1 or frame_type == 2:  # bool
                value = iec61850.IedServer_getBooleanAttributeValue(self._server, da)
                return bool(value) if value is not None else False
        except Exception as e:
            from .log import log
            log.error(f"IEC61850 读取测点值失败: address={address}, error={e}")
            return 0

    def set_point_value(self, address: int, value: Any, frame_type: int) -> None:
        """设置测点值

        Args:
            address: 测点地址
            value: 要设置的值
            frame_type: 帧类型
        """
        if not self._server or not self._is_running:
            return

        key = (address, frame_type)
        da = self._point_attrs.get(key)
        if not da:
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
            from .log import log
            log.error(f"IEC61850 设置测点值失败: address={address}, value={value}, error={e}")

    def destroy(self):
        """销毁服务器和模型"""
        self.stop()
        if self._model:
            iec61850.IedModel_destroy(self._model)
            self._model = None
