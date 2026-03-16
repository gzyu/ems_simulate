"""
IEC 61850 MMS 客户端封装
基于 pyiec61850 (libiec61850 Python bindings) 实现 MMS 客户端
"""

import asyncio
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
FC_MX = None
FC_ST = None
FC_CO = None

if HAS_IEC61850:
    FC_MX = iec61850.IEC61850_FC_MX
    FC_ST = iec61850.IEC61850_FC_ST
    FC_CO = iec61850.IEC61850_FC_CO


class IEC61850Client:
    """IEC 61850 MMS 客户端

    使用 pyiec61850 通过 MMS 协议连接到 IEC 61850 服务器，
    读写远端 IED 的数据属性。
    """

    def __init__(
        self,
        ip: str = "127.0.0.1",
        port: int = 102,
        model_name: str = "EMS",
        ld_name: str = "GenericLD",
    ):
        if not HAS_IEC61850:
            raise RuntimeError("pyiec61850 未安装，无法创建 IEC 61850 客户端")

        self.ip = ip
        self.port = port
        self.model_name = model_name
        self.ld_name = ld_name

        self._connection = None
        self._is_connected = False

        # 地址 -> MMS 引用路径的映射
        self._point_refs: Dict[Tuple[int, int], str] = {}

    def _build_ref(self, address: int, frame_type: int) -> str:
        """根据地址和帧类型构建 MMS 引用路径"""
        if frame_type == 0:  # 遥测
            return f"{self.model_name}{self.ld_name}/MMXU1.MV{address}.mag.f"
        elif frame_type == 1:  # 遥信
            return f"{self.model_name}{self.ld_name}/GGIO1.SPS{address}.stVal"
        elif frame_type == 2:  # 遥控
            return f"{self.model_name}{self.ld_name}/GGIO1.SPC{address}.ctlVal"
        elif frame_type == 3:  # 遥调
            return f"{self.model_name}{self.ld_name}/GGIO2.APC{address}.ctlVal"
        return ""

    def add_point(self, address: int, frame_type: int) -> str:
        """注册测点引用路径

        Args:
            address: 测点地址
            frame_type: 帧类型

        Returns:
            MMS 引用路径
        """
        key = (address, frame_type)
        if key not in self._point_refs:
            self._point_refs[key] = self._build_ref(address, frame_type)
        return self._point_refs[key]

    async def connect(self) -> bool:
        """连接到 IEC 61850 服务器"""
        try:
            self._connection = iec61850.IedConnection_create()
            error = iec61850.IedConnection_connect(
                self._connection, self.ip, self.port
            )
            if error == iec61850.IED_ERROR_OK:
                self._is_connected = True
                log.info(f"IEC 61850 客户端已连接: {self.ip}:{self.port}")
                return True
            else:
                log.error(f"IEC 61850 连接失败, 错误码: {error}")
                self._is_connected = False
                return False
        except Exception as e:
            log.error(f"IEC 61850 连接异常: {e}")
            self._is_connected = False
            return False

    def disconnect(self):
        """断开连接"""
        if self._connection:
            try:
                iec61850.IedConnection_close(self._connection)
            except Exception:
                pass
            try:
                iec61850.IedConnection_destroy(self._connection)
            except Exception:
                pass
            self._connection = None
            self._is_connected = False
            log.info("IEC 61850 客户端已断开")

    @property
    def is_connected(self) -> bool:
        return self._is_connected

    def read_point(self, address: int, frame_type: int) -> Any:
        """读取测点值

        Args:
            address: 测点地址
            frame_type: 帧类型

        Returns:
            测点值 (float 或 bool)，失败返回 None
        """
        if not self._connection or not self._is_connected:
            return None

        key = (address, frame_type)
        ref = self._point_refs.get(key)
        if not ref:
            ref = self._build_ref(address, frame_type)

        try:
            if frame_type == 0 or frame_type == 3:  # float
                fc = FC_MX if frame_type == 0 else FC_CO
                [value, error] = iec61850.IedConnection_readFloatValue(
                    self._connection, ref, fc
                )
                if error == iec61850.IED_ERROR_OK:
                    return float(value)
                else:
                    log.error(f"读取浮点值失败: ref={ref}, error={error}")
                    return None

            elif frame_type == 1 or frame_type == 2:  # bool
                fc = FC_ST if frame_type == 1 else FC_CO
                [value, error] = iec61850.IedConnection_readBooleanValue(
                    self._connection, ref, fc
                )
                if error == iec61850.IED_ERROR_OK:
                    return bool(value)
                else:
                    log.error(f"读取布尔值失败: ref={ref}, error={error}")
                    return None

        except Exception as e:
            log.error(f"IEC61850 客户端读取异常: address={address}, error={e}")
            # 连接可能已断开
            self._is_connected = False
            return None

    def write_point(self, address: int, value: Any, frame_type: int) -> bool:
        """写入测点值

        Args:
            address: 测点地址
            value: 要写入的值
            frame_type: 帧类型

        Returns:
            是否写入成功
        """
        if not self._connection or not self._is_connected:
            return False

        key = (address, frame_type)
        ref = self._point_refs.get(key)
        if not ref:
            ref = self._build_ref(address, frame_type)

        try:
            if frame_type == 0 or frame_type == 3:  # float
                fc = FC_MX if frame_type == 0 else FC_CO
                error = iec61850.IedConnection_writeFloatValue(
                    self._connection, ref, fc, float(value)
                )
                return error == iec61850.IED_ERROR_OK

            elif frame_type == 1 or frame_type == 2:  # bool
                fc = FC_ST if frame_type == 1 else FC_CO
                error = iec61850.IedConnection_writeBooleanValue(
                    self._connection, ref, fc, bool(value)
                )
                return error == iec61850.IED_ERROR_OK

        except Exception as e:
            log.error(f"IEC61850 客户端写入异常: address={address}, error={e}")
            self._is_connected = False
            return False

        return False

    def browse_logical_devices(self) -> List[str]:
        """浏览远端 IED 的逻辑设备列表"""
        if not self._connection or not self._is_connected:
            return []

        try:
            [device_list, error] = iec61850.IedConnection_getLogicalDeviceList(
                self._connection
            )
            if error != iec61850.IED_ERROR_OK:
                return []

            devices = []
            device = iec61850.LinkedList_getNext(device_list)
            while device:
                devices.append(iec61850.toCharP(device.data))
                device = iec61850.LinkedList_getNext(device)
            iec61850.LinkedList_destroy(device_list)
            return devices
        except Exception as e:
            log.error(f"浏览逻辑设备失败: {e}")
            return []
