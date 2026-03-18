"""
IEC 61850 协议处理器
支持 IEC 61850 MMS 服务端和客户端
"""

from typing import Any, Dict, List, Optional

from src.device.protocol.base_handler import ServerHandler, ClientHandler
from src.enums.points.base_point import BasePoint
from src.enums.point_data import Yc, Yx, Yt, Yk


class IEC61850ServerHandler(ServerHandler):
    """IEC 61850 服务端处理器"""

    def __init__(self, log=None):
        super().__init__()
        self._server = None
        self._log = log

    def initialize(self, config: Dict[str, Any]) -> None:
        """初始化 IEC 61850 服务器

        Args:
            config: 配置字典，包含:
                - ip: 监听 IP（默认 0.0.0.0）
                - port: 监听端口（默认 102）
                - model_name: IED 模型名称
                - ied_name: IED 名称
                - ld_name: 逻辑设备名称
        """
        from src.proto.iec61850.iec61850_server import IEC61850Server

        self._config = config
        ip = config.get("ip", "0.0.0.0")
        port = config.get("port", 102)
        model_name = config.get("model_name", "EMS")
        ied_name = config.get("ied_name", "EMSDevice")
        ld_name = config.get("ld_name", "GenericLD")

        self._server = IEC61850Server(
            ip=ip,
            port=port,
            model_name=model_name,
            ied_name=ied_name,
            ld_name=ld_name,
        )

    async def start(self) -> bool:
        """启动 IEC 61850 服务器"""
        try:
            if self._server:
                self._server.start()
                self._is_running = self._server.is_running
                return self._is_running
            return False
        except Exception as e:
            if self._log:
                self._log.error(f"启动 IEC 61850 服务器失败: {e}")
            return False

    async def stop(self) -> bool:
        """停止 IEC 61850 服务器"""
        try:
            if self._server:
                self._server.stop()
                self._is_running = False
                return True
            return False
        except Exception as e:
            if self._log:
                self._log.error(f"停止 IEC 61850 服务器失败: {e}")
            return False

    def read_value(self, point: BasePoint) -> Any:
        """读取测点值"""
        if self._server:
            return self._server.get_point_value(
                address=point.address, frame_type=point.frame_type
            )
        return 0

    def write_value(self, point: BasePoint, value: Any) -> bool:
        """写入测点值"""
        if self._server:
            self._server.set_point_value(
                address=point.address,
                value=value,
                frame_type=point.frame_type,
            )
            return True
        return False

    async def read_value_async(self, point: BasePoint) -> Any:
        """异步读取测点值"""
        return self.read_value(point)

    async def write_value_async(self, point: BasePoint, value: Any) -> bool:
        """异步写入测点值"""
        return self.write_value(point, value)

    def add_points(self, points: List[BasePoint]) -> None:
        """添加测点到 IEC 61850 服务器"""
        if not self._server:
            return

        for point in points:
            self._server.add_point(
                address=point.address,
                frame_type=point.frame_type,
            )

    def get_value_by_address(
        self, func_code: int, slave_id: int, address: int
    ) -> Any:
        """根据地址获取值"""
        if self._server:
            return self._server.get_point_value(address=address, frame_type=0)
        return 0

    def set_value_by_address(
        self, func_code: int, slave_id: int, address: int, value: Any
    ) -> None:
        """根据地址设置值"""
        if self._server:
            self._server.set_point_value(address=address, value=value, frame_type=0)

    @property
    def server(self):
        """获取底层服务器对象"""
        return self._server

    def get_captured_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取捕获的报文列表"""
        # IEC 61850 MMS 目前不支持报文捕获
        return []

    def clear_captured_messages(self) -> None:
        """清空捕获的报文"""
        pass

    def get_avg_time(self) -> dict:
        """获取平均收发时间"""
        return {}


class IEC61850ClientHandler(ClientHandler):
    """IEC 61850 客户端处理器"""

    def __init__(self, log=None):
        super().__init__()
        self._client = None
        self._log = log
        self._on_points_discovered = None  # 测点发现回调

    def set_on_points_discovered(self, callback):
        """设置测点发现回调

        Args:
            callback: 回调函数，签名为 callback(discovered_points: List[Dict])
                      每个 dict 包含 {"address": int, "frame_type": int, "ref": str}
        """
        self._on_points_discovered = callback

    def initialize(self, config: Dict[str, Any]) -> None:
        """初始化 IEC 61850 客户端

        Args:
            config: 配置字典，包含:
                - ip: 服务器 IP
                - port: 服务器端口（默认 102）
                - model_name: IED 模型名称
                - ld_name: 逻辑设备名称
        """
        from src.proto.iec61850.iec61850_client import IEC61850Client

        self._config = config
        ip = config.get("ip", "127.0.0.1")
        port = config.get("port", 102)
        model_name = config.get("model_name", "EMS")
        ld_name = config.get("ld_name", "GenericLD")

        self._client = IEC61850Client(
            ip=ip,
            port=port,
            model_name=model_name,
            ld_name=ld_name,
        )

    async def start(self) -> bool:
        """启动客户端（连接服务器）"""
        return await self.connect()

    async def stop(self) -> bool:
        """停止客户端（断开连接）"""
        self.disconnect()
        return True

    async def connect(self) -> bool:
        """连接到 IEC 61850 服务器"""
        try:
            if self._client:
                is_connected = await self._client.connect()
                self._is_running = is_connected

                # 连接成功后，通知上层发现的测点
                if is_connected and self._on_points_discovered:
                    discovered = self._client.get_discovered_points()
                    if discovered:
                        try:
                            self._on_points_discovered(discovered)
                        except Exception as e:
                            if self._log:
                                self._log.error(f"处理发现的测点时出错: {e}")

                return is_connected
            return False
        except Exception as e:
            if self._log:
                self._log.error(f"连接 IEC 61850 服务器失败: {e}")
            return False

    def disconnect(self) -> None:
        """断开连接"""
        if self._client:
            self._client.disconnect()
            self._is_running = False

    @property
    def is_running(self) -> bool:
        """检测客户端的真实连接状态"""
        if not self._is_running:
            return False
        if not self._client:
            return False
        return self._client.is_connected

    def read_value(self, point: BasePoint) -> Any:
        """读取测点值"""
        if not self._client or not self.is_running:
            if self._log:
                self._log.error("IEC 61850 客户端未连接")
            return None

        real_val = self._client.read_point(
            address=point.address, frame_type=point.frame_type
        )
        if real_val is None:
            if self._log:
                self._log.error("IEC 61850 客户端读取测点值失败")
            return None

        # 遥测点需根据系数反向换算
        if isinstance(point, Yc):
            try:
                return int((real_val - point.add_coe) / point.mul_coe)
            except (ZeroDivisionError, TypeError):
                if self._log:
                    self._log.error("IEC 61850 客户端系数计算失败")
                return None
        return real_val

    def write_value(self, point: BasePoint, value: Any) -> bool:
        """写入测点值（发送命令）"""
        if not self._client or not self.is_running:
            return False

        real_to_send = value

        try:
            if isinstance(point, (Yc, Yt)):
                real_to_send = value * point.mul_coe + point.add_coe
                return self._client.write_point(
                    address=point.address,
                    value=float(real_to_send),
                    frame_type=point.frame_type,
                )
            elif isinstance(point, (Yx, Yk)):
                return self._client.write_point(
                    address=point.address,
                    value=bool(real_to_send),
                    frame_type=point.frame_type,
                )
        except Exception as e:
            if self._log:
                self._log.error(f"IEC 61850 客户端写入失败: {e}")
            return False

        return False

    async def read_value_async(self, point: BasePoint) -> Any:
        """异步读取测点值"""
        return self.read_value(point)

    async def write_value_async(self, point: BasePoint, value: Any) -> bool:
        """异步写入测点值"""
        return self.write_value(point, value)

    def add_points(self, points: List[BasePoint]) -> None:
        """注册测点到 IEC 61850 客户端"""
        if not self._client:
            return

        for point in points:
            self._client.add_point(
                address=point.address,
                frame_type=point.frame_type,
            )

    @property
    def client(self):
        """获取底层客户端对象"""
        return self._client

    def get_captured_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        """获取捕获的报文列表"""
        return []

    def clear_captured_messages(self) -> None:
        """清空捕获的报文"""
        pass

    def get_avg_time(self) -> dict:
        """获取平均收发时间"""
        return {}
