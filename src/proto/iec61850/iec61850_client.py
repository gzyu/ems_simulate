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
    log.warning("pyiec61850 未安装，IEC 61850 功能不可用。请执行: pip install pyiec61850-ng==1.6.1.1")


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
        # 确保 address 是合法的 IEC 61850 节点名称，与服务端保持一致
        safe_addr = str(address).replace('.', '_').replace('/', '_').replace('\\', '_').replace('-', '_')

        if frame_type == 0:  # 遥测
            return f"{self.model_name}{self.ld_name}/MMXU1.MV_{safe_addr}.mag.f"
        elif frame_type == 1:  # 遥信
            return f"{self.model_name}{self.ld_name}/GGIO1.SPS_{safe_addr}.stVal"
        elif frame_type == 2:  # 遥控
            return f"{self.model_name}{self.ld_name}/GGIO1.SPC_{safe_addr}.ctlVal"
        elif frame_type == 3:  # 遥调
            return f"{self.model_name}{self.ld_name}/GGIO2.APC_{safe_addr}.ctlVal"
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
            result = iec61850.IedConnection_connect(
                self._connection, self.ip, self.port
            )
            
            # 处理返回值，可能是 int 或 (None, int)
            error = result
            if isinstance(result, (list, tuple)):
                error = result[1]
                
            if error == iec61850.IED_ERROR_OK:
                self._is_connected = True
                log.info(f"IEC 61850 客户端已连接: {self.ip}:{self.port}")
                
                # 自动发现模型
                self.discover_model()
                
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

    def _get_list_from_linked_list(self, linked_list) -> List[str]:
        """从 LinkedList 中提取字符串列表"""
        items = []
        item = iec61850.LinkedList_getNext(linked_list)
        while item:
            items.append(iec61850.toCharP(item.data))
            item = iec61850.LinkedList_getNext(item)
        iec61850.LinkedList_destroy(linked_list)
        return items

    def discover_model(self) -> List[Dict[str, Any]]:
        """动态发现并映射服务端的数据模型

        Returns:
            发现的测点列表，每个元素为 {"address": int, "frame_type": int, "ref": str}
        """
        if not self._connection or not self._is_connected:
            return []

        log.info("开始 IEC 61850 动态模型发现...")
        start_time = time.time()
        discovered_points: List[Dict[str, Any]] = []

        # 1. 获取逻辑设备列表
        result = iec61850.IedConnection_getLogicalDeviceList(self._connection)
        ld_list = result[0] if isinstance(result, (list, tuple)) else result
        error = result[1] if isinstance(result, (list, tuple)) else 0
        
        if error != iec61850.IED_ERROR_OK:
            log.error(f"发现模型失败: 无法获取逻辑设备列表 (错误码: {error})")
            return []
        
        lds = self._get_list_from_linked_list(ld_list)
        log.info(f"发现逻辑设备: {lds}")
        
        for ld in lds:
            # 2. 获取逻辑节点列表 (使用 getLogicalDeviceDirectory)
            result = iec61850.IedConnection_getLogicalDeviceDirectory(self._connection, ld)
            ln_list = result[0] if isinstance(result, (list, tuple)) else result
            error = result[1] if isinstance(result, (list, tuple)) else 0
            
            if error != iec61850.IED_ERROR_OK:
                log.debug(f"跳过逻辑设备 {ld}: 无法获取目录 (错误码: {error})")
                continue
            
            lns = self._get_list_from_linked_list(ln_list)
            log.info(f"逻辑设备 {ld} 下发现逻辑节点: {lns}")
            for ln in lns:
                ln_ref = f"{ld}/{ln}"
                
                # 3. 获取数据对象列表 (使用 getLogicalNodeDirectory)
                result = iec61850.IedConnection_getLogicalNodeDirectory(self._connection, ln_ref, 0) # 0 = ACSI_CLASS_DATA_OBJECT
                do_list = result[0] if isinstance(result, (list, tuple)) else result
                error = result[1] if isinstance(result, (list, tuple)) else 0
                
                if error != iec61850.IED_ERROR_OK:
                    log.info(f"跳过逻辑节点 {ln_ref}: 无法获取目录 (错误码: {error})")
                    continue
                
                dos = self._get_list_from_linked_list(do_list)
                log.info(f"逻辑节点 {ln_ref} 下发现数据对象: {dos}")
                for do in dos:
                    full_do_ref = f"{ln_ref}.{do}"
                    
                    try:
                        if ln == "MMXU1" and do.startswith("MV_"):
                            addr = do[3:]
                            ref = f"{full_do_ref}.mag.f"
                            self._point_refs[(addr, 0)] = ref
                            discovered_points.append({"address": addr, "frame_type": 0, "ref": ref})
                            log.info(f"映射测点: ({addr}, 0) -> {ref}")
                        elif ln == "GGIO1" and do.startswith("SPS_"):
                            addr = do[4:]
                            ref = f"{full_do_ref}.stVal"
                            self._point_refs[(addr, 1)] = ref
                            discovered_points.append({"address": addr, "frame_type": 1, "ref": ref})
                            log.info(f"映射测点: ({addr}, 1) -> {ref}")
                        elif ln == "GGIO1" and do.startswith("SPC_"):
                            addr = do[4:]
                            ref = f"{full_do_ref}.ctlVal"
                            self._point_refs[(addr, 2)] = ref
                            discovered_points.append({"address": addr, "frame_type": 2, "ref": ref})
                            log.info(f"映射测点: ({addr}, 2) -> {ref}")
                        elif ln == "GGIO2" and do.startswith("APC_"):
                            addr = do[4:]
                            ref = f"{full_do_ref}.ctlVal"
                            self._point_refs[(addr, 3)] = ref
                            discovered_points.append({"address": addr, "frame_type": 3, "ref": ref})
                            log.info(f"映射测点: ({addr}, 3) -> {ref}")
                    except Exception as e:
                        log.error(f"解析测点地址失败: {do}, 错误: {e}")
                        continue

        log.info(f"IEC 61850 动态发现完成, 耗时: {time.time() - start_time:.2f}s, 发现并映射了 {len(discovered_points)} 个测点")
        return discovered_points

    def get_discovered_points(self) -> List[Dict[str, Any]]:
        """获取当前已映射的测点列表

        Returns:
            测点列表，每个元素为 {"address": int, "frame_type": int, "ref": str}
        """
        return [
            {"address": addr, "frame_type": ft, "ref": ref}
            for (addr, ft), ref in self._point_refs.items()
        ]

    def browse_logical_devices(self) -> List[str]:
        """浏览远端 IED 的逻辑设备列表"""
        if not self._connection or not self._is_connected:
            return []

        try:
            [ld_list, error] = iec61850.IedConnection_getLogicalDeviceList(
                self._connection
            )
            if error != iec61850.IED_ERROR_OK:
                return []

            return self._get_list_from_linked_list(ld_list)
        except Exception as e:
            log.error(f"浏览逻辑设备失败: {e}")
            return []
