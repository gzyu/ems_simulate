"""
IEC 61850 GOOSE 订阅者/接收器封装
基于 pyiec61850 (libiec61850 Python bindings) 实现 GOOSE 报文接收

GOOSE Subscriber 用于监听网络上的 GOOSE 组播报文，
当收到报文时通过回调通知上层应用。
"""

import threading
import time
from typing import Any, Callable, Dict, List, Optional

from .log import log

try:
    from pyiec61850 import pyiec61850 as iec61850
    HAS_IEC61850 = True
except ImportError:
    HAS_IEC61850 = False
    log.warning("pyiec61850 未安装，GOOSE Subscriber 功能不可用")


# GOOSE 订阅状态
GOOSE_STATE_INIT = "init"           # 初始化
GOOSE_STATE_CONNECTED = "connected"  # 已收到有效报文
GOOSE_STATE_LOST = "lost"           # 超时未收到报文
GOOSE_STATE_ERROR = "error"         # 解析错误


class GooseSubscription:
    """单个 GOOSE 订阅的信息

    记录订阅者的配置和最新收到的 GOOSE 报文状态。
    """

    def __init__(
        self,
        go_cb_ref: str,
        app_id: Optional[int] = None,
        dst_mac: Optional[List[int]] = None,
        description: str = "",
    ):
        self.go_cb_ref = go_cb_ref
        self.app_id = app_id
        self.dst_mac = dst_mac
        self.description = description

        # 最新接收状态
        self.go_id: str = ""
        self.data_set_ref: str = ""
        self.conf_rev: int = 0
        self.st_num: int = 0
        self.sq_num: int = 0
        self.time_allowed_to_live: int = 0
        self.timestamp: int = 0
        self.state: str = GOOSE_STATE_INIT
        self.last_update: float = 0.0

        # 数据集值
        self.data_values: List[Dict[str, Any]] = []

        # 底层 subscriber 对象 (由 GooseReceiver 管理)
        self._subscriber = None

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "go_cb_ref": self.go_cb_ref,
            "app_id": self.app_id,
            "go_id": self.go_id,
            "data_set_ref": self.data_set_ref,
            "conf_rev": self.conf_rev,
            "st_num": self.st_num,
            "sq_num": self.sq_num,
            "time_allowed_to_live": self.time_allowed_to_live,
            "timestamp": self.timestamp,
            "state": self.state,
            "last_update": self.last_update,
            "description": self.description,
            "dst_mac": ":".join(f"{b:02X}" for b in self.dst_mac) if self.dst_mac else "",
            "data_values": self.data_values,
        }


class GooseReceiver:
    """IEC 61850 GOOSE 接收器

    功能:
    - 在指定网络接口上监听 GOOSE 组播报文
    - 管理多个 GOOSE 订阅
    - 收到报文时触发回调通知
    - 支持订阅状态实时监控

    典型用法:
        receiver = GooseReceiver("eth0")
        receiver.add_subscription("LD0/LLN0$GO$gcb1", app_id=0x0001)
        receiver.set_callback(my_callback)
        receiver.start()
        ...
        receiver.stop()
    """

    def __init__(self, interface: str = "eth0"):
        if not HAS_IEC61850:
            raise RuntimeError("pyiec61850 未安装，无法创建 GOOSE Receiver")

        self.interface = interface
        self._receiver = None
        self._subscriptions: Dict[str, GooseSubscription] = {}  # go_cb_ref -> subscription
        self._is_running = False
        self._callback: Optional[Callable] = None
        self._lock = threading.Lock()

        # 状态监控线程
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_stop = threading.Event()

    def add_subscription(
        self,
        go_cb_ref: str,
        app_id: Optional[int] = None,
        dst_mac: Optional[List[int]] = None,
        description: str = "",
    ) -> GooseSubscription:
        """添加 GOOSE 订阅

        Args:
            go_cb_ref: GOOSE 控制块引用 (MMS 格式, 如 "LD0/LLN0$GO$gcb1")
            app_id: APPID 过滤 (可选)
            dst_mac: 目标 MAC 地址过滤 (可选)
            description: 描述

        Returns:
            GooseSubscription 实例
        """
        with self._lock:
            if go_cb_ref in self._subscriptions:
                return self._subscriptions[go_cb_ref]

            sub = GooseSubscription(go_cb_ref, app_id, dst_mac, description)
            self._subscriptions[go_cb_ref] = sub
            return sub

    def remove_subscription(self, go_cb_ref: str) -> bool:
        """移除 GOOSE 订阅"""
        with self._lock:
            if go_cb_ref in self._subscriptions:
                del self._subscriptions[go_cb_ref]
                return True
            return False

    def get_subscriptions(self) -> List[Dict[str, Any]]:
        """获取所有订阅信息"""
        with self._lock:
            return [sub.to_dict() for sub in self._subscriptions.values()]

    def get_subscription(self, go_cb_ref: str) -> Optional[Dict[str, Any]]:
        """获取指定订阅信息"""
        with self._lock:
            sub = self._subscriptions.get(go_cb_ref)
            return sub.to_dict() if sub else None

    def set_callback(self, callback: Callable[[Dict[str, Any]], None]) -> None:
        """设置 GOOSE 报文接收回调

        回调参数为 dict，包含:
            go_cb_ref, go_id, app_id, st_num, sq_num, conf_rev,
            timestamp, data_values, state
        """
        self._callback = callback

    def _on_goose_message(self, subscriber, parameter=None):
        """GOOSE 报文接收回调 (底层 C 回调的 Python 包装)"""
        try:
            # 从 subscriber 对象获取报文信息
            go_cb_ref = ""
            if hasattr(iec61850, 'GooseSubscriber_getGoCbRef'):
                go_cb_ref = iec61850.GooseSubscriber_getGoCbRef(subscriber) or ""

            with self._lock:
                sub = self._subscriptions.get(go_cb_ref)
                if not sub:
                    return

                # 读取报文字段
                if hasattr(iec61850, 'GooseSubscriber_getGoId'):
                    sub.go_id = iec61850.GooseSubscriber_getGoId(subscriber) or ""
                if hasattr(iec61850, 'GooseSubscriber_getDataSet'):
                    sub.data_set_ref = iec61850.GooseSubscriber_getDataSet(subscriber) or ""
                if hasattr(iec61850, 'GooseSubscriber_getConfRev'):
                    sub.conf_rev = iec61850.GooseSubscriber_getConfRev(subscriber)
                if hasattr(iec61850, 'GooseSubscriber_getStNum'):
                    sub.st_num = iec61850.GooseSubscriber_getStNum(subscriber)
                if hasattr(iec61850, 'GooseSubscriber_getSqNum'):
                    sub.sq_num = iec61850.GooseSubscriber_getSqNum(subscriber)
                if hasattr(iec61850, 'GooseSubscriber_getTimeAllowedToLive'):
                    sub.time_allowed_to_live = iec61850.GooseSubscriber_getTimeAllowedToLive(subscriber)
                if hasattr(iec61850, 'GooseSubscriber_getTimestamp'):
                    sub.timestamp = iec61850.GooseSubscriber_getTimestamp(subscriber)

                # 检查有效性
                is_valid = True
                if hasattr(iec61850, 'GooseSubscriber_isValid'):
                    is_valid = iec61850.GooseSubscriber_isValid(subscriber)

                sub.state = GOOSE_STATE_CONNECTED if is_valid else GOOSE_STATE_ERROR
                sub.last_update = time.time()

                # 解析数据集值
                sub.data_values = self._parse_data_set_values(subscriber)

            # 触发上层回调
            if self._callback:
                try:
                    self._callback(sub.to_dict())
                except Exception as e:
                    log.error(f"GOOSE 回调执行失败: {e}")

        except Exception as e:
            log.error(f"GOOSE 报文处理异常: {e}")

    def _parse_data_set_values(self, subscriber) -> List[Dict[str, Any]]:
        """解析 GOOSE 数据集值"""
        values = []
        try:
            if not hasattr(iec61850, 'GooseSubscriber_getDataSetValues'):
                return values

            dataset = iec61850.GooseSubscriber_getDataSetValues(subscriber)
            if not dataset:
                return values

            # 遍历数组元素
            array_size = iec61850.MmsValue_getArraySize(dataset) if hasattr(iec61850, 'MmsValue_getArraySize') else 0
            for i in range(array_size):
                element = iec61850.MmsValue_getElement(dataset, i) if hasattr(iec61850, 'MmsValue_getElement') else None
                if not element:
                    continue

                entry = {"index": i, "type": "unknown", "value": None}

                mms_type = iec61850.MmsValue_getType(element) if hasattr(iec61850, 'MmsValue_getType') else -1

                # MMS 类型常量
                MMS_BOOLEAN = 0
                MMS_INTEGER = 2
                MMS_UNSIGNED = 3
                MMS_FLOAT = 4
                MMS_VISIBLE_STRING = 10
                MMS_UTC_TIME = 17
                MMS_BIT_STRING = 1

                if mms_type == MMS_BOOLEAN:
                    entry["type"] = "boolean"
                    entry["value"] = bool(iec61850.MmsValue_getBoolean(element))
                elif mms_type == MMS_INTEGER:
                    entry["type"] = "integer"
                    entry["value"] = int(iec61850.MmsValue_toInt32(element))
                elif mms_type == MMS_UNSIGNED:
                    entry["type"] = "unsigned"
                    entry["value"] = int(iec61850.MmsValue_toUint32(element))
                elif mms_type == MMS_FLOAT:
                    entry["type"] = "float"
                    entry["value"] = float(iec61850.MmsValue_toFloat(element))
                elif mms_type == MMS_VISIBLE_STRING:
                    entry["type"] = "string"
                    entry["value"] = str(iec61850.MmsValue_toString(element) or "")
                elif mms_type == MMS_UTC_TIME:
                    entry["type"] = "timestamp"
                    entry["value"] = int(iec61850.MmsValue_getUtcTimeInMs(element))
                elif mms_type == MMS_BIT_STRING:
                    entry["type"] = "bitstring"
                    entry["value"] = int(iec61850.MmsValue_getBitStringAsInteger(element))

                values.append(entry)
        except Exception as e:
            log.error(f"解析 GOOSE 数据集失败: {e}")

        return values

    def start(self) -> bool:
        """启动 GOOSE Receiver"""
        if self._is_running:
            return True

        try:
            self._receiver = iec61850.GooseReceiver_create()
            if not self._receiver:
                raise RuntimeError("GooseReceiver_create 失败")

            iec61850.GooseReceiver_setInterfaceId(self._receiver, self.interface)

            # 添加所有订阅者
            with self._lock:
                for go_cb_ref, sub in self._subscriptions.items():
                    # 创建底层 subscriber
                    subscriber = iec61850.GooseSubscriber_create(go_cb_ref, None)
                    if not subscriber:
                        log.warning(f"GooseSubscriber_create 失败: {go_cb_ref}")
                        continue

                    # 设置过滤
                    if sub.app_id is not None and hasattr(iec61850, 'GooseSubscriber_setAppId'):
                        iec61850.GooseSubscriber_setAppId(subscriber, sub.app_id)
                    if sub.dst_mac and hasattr(iec61850, 'GooseSubscriber_setDstMac'):
                        iec61850.GooseSubscriber_setDstMac(subscriber, sub.dst_mac)

                    # 设置回调
                    if hasattr(iec61850, 'GooseSubscriber_setListener'):
                        iec61850.GooseSubscriber_setListener(
                            subscriber, self._on_goose_message, None
                        )

                    iec61850.GooseReceiver_addSubscriber(self._receiver, subscriber)
                    sub._subscriber = subscriber

            # 启动接收线程
            iec61850.GooseReceiver_start(self._receiver)
            self._is_running = True

            # 启动状态监控线程
            self._monitor_stop.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True
            )
            self._monitor_thread.start()

            log.info(f"GOOSE Receiver 已启动: interface={self.interface}, 订阅数={len(self._subscriptions)}")
            return True
        except Exception as e:
            log.error(f"GOOSE Receiver 启动失败: {e}")
            self._is_running = False
            return False

    def stop(self) -> None:
        """停止 GOOSE Receiver"""
        self._is_running = False
        self._monitor_stop.set()

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2.0)

        if self._receiver:
            try:
                if hasattr(iec61850, 'GooseReceiver_stop'):
                    iec61850.GooseReceiver_stop(self._receiver)
                iec61850.GooseReceiver_destroy(self._receiver)
            except Exception as e:
                log.error(f"GOOSE Receiver 停止异常: {e}")
            self._receiver = None

        # 清理 subscriber 引用
        with self._lock:
            for sub in self._subscriptions.values():
                sub._subscriber = None
                sub.state = GOOSE_STATE_INIT

        log.info("GOOSE Receiver 已停止")

    def _monitor_loop(self):
        """状态监控循环 - 检测超时订阅"""
        while not self._monitor_stop.is_set():
            try:
                now = time.time()
                with self._lock:
                    for sub in self._subscriptions.values():
                        if sub.last_update > 0 and sub.time_allowed_to_live > 0:
                            elapsed = (now - sub.last_update) * 1000  # ms
                            if elapsed > sub.time_allowed_to_live:
                                if sub.state != GOOSE_STATE_LOST:
                                    sub.state = GOOSE_STATE_LOST
                                    log.warning(f"GOOSE 订阅超时: {sub.go_cb_ref}")
            except Exception as e:
                log.error(f"GOOSE 状态监控异常: {e}")

            self._monitor_stop.wait(1.0)

    @property
    def is_running(self) -> bool:
        return self._is_running

    def get_status(self) -> Dict[str, Any]:
        """获取 Receiver 状态"""
        return {
            "interface": self.interface,
            "is_running": self._is_running,
            "subscription_count": len(self._subscriptions),
            "subscriptions": self.get_subscriptions(),
        }
