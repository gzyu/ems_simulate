"""
IEC 61850 GOOSE 发布者封装
基于 pyiec61850 (libiec61850 Python bindings) 实现 GOOSE 报文发布

GOOSE (Generic Object Oriented Substation Event) 是 IEC 61850 标准中
用于快速可靠地传输变电站事件的二层组播协议，主要用于跳闸/告警等实时信号传输。
"""

import threading
import time
from typing import Any, Dict, List, Optional

from .log import log

try:
    from pyiec61850 import pyiec61850 as iec61850
    HAS_IEC61850 = True
except ImportError:
    HAS_IEC61850 = False
    log.warning("pyiec61850 未安装，GOOSE Publisher 功能不可用")


# GOOSE 默认组播 MAC 地址前缀 (IEC 61850 规定: 01-0C-CD-01-00-00 ~ 01-0C-CD-01-01-FF)
GOOSE_MULTICAST_MAC_PREFIX = [0x01, 0x0C, 0xCD, 0x01, 0x00]

# GOOSE 默认参数
DEFAULT_TIME_ALLOWED_TO_LIVE = 1000  # ms
DEFAULT_CONF_REV = 1
DEFAULT_SQ_NUM = 0
DEFAULT_ST_NUM = 1


class GooseDataSetEntry:
    """GOOSE 数据集条目"""

    def __init__(self, name: str, value: Any = None, iec_type: str = "boolean"):
        self.name = name
        self.value = value
        self.iec_type = iec_type  # boolean / integer / float / string / bitstring / timestamp


class GoosePublisher:
    """IEC 61850 GOOSE 发布者

    功能:
    - 创建 GOOSE 控制块 (GoCB) 并发布 GOOSE 报文
    - 支持数据集动态添加/修改
    - 支持 stNum/sqNum 自动管理
    - 支持定时重发 (TAL)

    典型用法:
        publisher = GoosePublisher("eth0", go_cb_ref="LD0/LLN0$GO$gcb1")
        publisher.set_go_id("gcb1")
        publisher.set_data_set_ref("LD0/LLN0$dsGOOSE1")
        publisher.add_entry(GooseDataSetEntry("stVal", True, "boolean"))
        publisher.start()
        publisher.publish()  # 数据变化时调用
        ...
        publisher.stop()
    """

    def __init__(
        self,
        interface: str = "eth0",
        go_cb_ref: str = "",
        go_id: str = "",
        data_set_ref: str = "",
        app_id: int = 0x0001,
        conf_rev: int = DEFAULT_CONF_REV,
        time_allowed_to_live: int = DEFAULT_TIME_ALLOWED_TO_LIVE,
        dst_mac: Optional[List[int]] = None,
        vlan_id: int = 0,
        vlan_prio: int = 4,
        simulation: bool = True,
    ):
        if not HAS_IEC61850:
            raise RuntimeError("pyiec61850 未安装，无法创建 GOOSE Publisher")

        self.interface = interface
        self.go_cb_ref = go_cb_ref
        self.go_id = go_id
        self.data_set_ref = data_set_ref
        self.app_id = app_id
        self.conf_rev = conf_rev
        self.time_allowed_to_live = time_allowed_to_live
        self.simulation = simulation
        self.vlan_id = vlan_id
        self.vlan_prio = vlan_prio

        # 目标 MAC 地址 (组播)
        if dst_mac:
            self.dst_mac = dst_mac
        else:
            # 根据 APPID 生成默认组播 MAC
            self.dst_mac = GOOSE_MULTICAST_MAC_PREFIX + [
                (app_id >> 8) & 0xFF,
                app_id & 0xFF,
            ]

        # 状态序号管理
        self._st_num = DEFAULT_ST_NUM
        self._sq_num = DEFAULT_SQ_NUM

        # 数据集条目
        self._entries: List[GooseDataSetEntry] = []

        # 底层对象
        self._publisher = None
        self._comm_params = None
        self._is_running = False
        self._is_created = False

        # 定时重发线程
        self._retransmit_thread: Optional[threading.Thread] = None
        self._retransmit_stop = threading.Event()
        self._retransmit_interval = time_allowed_to_live / 2000.0  # TAL/2

        # 线程锁
        self._lock = threading.Lock()

    def _create_comm_parameters(self):
        """创建 CommParameters 对象"""
        if self._comm_params:
            return

        # libiec61850 Go API: GoosePublisher_create(CommParameters* parameters, const char* interfaceId)
        # 需要先创建 CommParameters 对象，否则 SWIG 绑定会报类型错误
        try:
            self._comm_params = iec61850.CommParameters()
        except AttributeError:
            try:
                self._comm_params = iec61850.CommParameters_create()
            except AttributeError:
                # 兜底: 尝试传递 None (部分绑定支持)
                self._comm_params = None

    def _call_iec(self, name, *args):
        """安全调用 iec61850 函数，自动大小写兜底。返回 (called, result)"""
        func = getattr(iec61850, name, None)
        if func is not None:
            return True, func(*args)
        # 尝试大小写变体
        alt_names = [name + "d", name[:-1], name.replace("Id", "ID"), name.replace("ID", "Id"),
                     name.replace("id", "Id"), name.replace("Id", "id")]
        for alt in alt_names:
            if alt != name:
                func = getattr(iec61850, alt, None)
                if func:
                    return True, func(*args)
        return False, None

    def _create_publisher(self):
        """创建底层 GOOSE Publisher 对象"""
        if self._is_created:
            return

        self._create_comm_parameters()
        self._publisher = iec61850.GoosePublisher_create(self._comm_params, self.interface)
        if not self._publisher:
            raise RuntimeError(f"GOOSE Publisher 创建失败, interface={self.interface}")

        # 设置 GOOSE 控制块属性
        if self.go_cb_ref:
            self._call_iec("GoosePublisher_setGoCbRef", self._publisher, self.go_cb_ref)
        if self.go_id:
            self._call_iec("GoosePublisher_setGoID", self._publisher, self.go_id)
        if self.data_set_ref:
            self._call_iec("GoosePublisher_setDataSetRef", self._publisher, self.data_set_ref)

        self._call_iec("GoosePublisher_setConfRev", self._publisher, self.conf_rev)
        self._call_iec("GoosePublisher_setTimeAllowedToLive", self._publisher, self.time_allowed_to_live)
        self._call_iec("GoosePublisher_setStNum", self._publisher, self._st_num)
        self._call_iec("GoosePublisher_setSqNum", self._publisher, self._sq_num)
        self._call_iec("GoosePublisher_setSimulation", self._publisher, self.simulation)

        # 设置目标 MAC
        if len(self.dst_mac) == 6:
            self._call_iec("GoosePublisher_setDstMac", self._publisher, self.dst_mac)

        # 设置 APPID
        ok, _ = self._call_iec("GoosePublisher_setAppid", self._publisher, self.app_id)
        if not ok:
            self._call_iec("GoosePublisher_setAppId", self._publisher, self.app_id)

        # 设置 VLAN
        if self.vlan_id > 0:
            ok, _ = self._call_iec("GoosePublisher_setVlanTag", self._publisher, self.vlan_id, self.vlan_prio)
            if not ok:
                self._call_iec("GoosePublisher_setVlanId", self._publisher, self.vlan_id)
                self._call_iec("GoosePublisher_setVlanPriority", self._publisher, self.vlan_prio)

        self._is_created = True

    def _entry_to_mms_value(self, entry: GooseDataSetEntry) -> Any:
        """将 GooseDataSetEntry 转换为 MmsValue"""
        if entry.iec_type == "boolean":
            return iec61850.MmsValue_newBoolean(bool(entry.value))
        elif entry.iec_type == "integer":
            return iec61850.MmsValue_newIntegerFromInt32(int(entry.value or 0))
        elif entry.iec_type == "float":
            return iec61850.MmsValue_newFloat(float(entry.value or 0.0))
        elif entry.iec_type == "string":
            return iec61850.MmsValue_newVisibleString(str(entry.value or ""))
        elif entry.iec_type == "bitstring":
            return iec61850.MmsValue_newBitString(4)  # 4字节位串
        elif entry.iec_type == "timestamp":
            return iec61850.MmsValue_newUtcTimeByMsTime(int(entry.value or 0))
        else:
            return iec61850.MmsValue_newBoolean(False)

    def add_entry(self, entry: GooseDataSetEntry) -> None:
        """添加数据集条目"""
        with self._lock:
            # 去重检查：不允许同名条目
            for existing in self._entries:
                if existing.name == entry.name:
                    raise ValueError(f"数据集条目名称已存在: {entry.name}")
            self._entries.append(entry)
            self._is_created = False  # 需要重建

    def remove_entry(self, index: int) -> None:
        """移除数据集条目"""
        with self._lock:
            if 0 <= index < len(self._entries):
                self._entries.pop(index)
                self._is_created = False

    def update_entry(self, index: int, value: Any) -> bool:
        """更新数据集条目值，返回 True 表示值有变化

        当值变化时:
        - stNum 自动递增，sqNum 重置
        - 如果 Publisher 正在运行，立即发送 GOOSE 报文（IEC 61850 要求）
        """
        changed = False
        with self._lock:
            if 0 <= index < len(self._entries):
                old_value = self._entries[index].value
                self._entries[index].value = value
                if old_value != value:
                    self._increment_st_num()
                    changed = True

        if changed and self._is_running:
            try:
                self.publish()
            except Exception as e:
                log.error(f"更新条目值后立即发布 GOOSE 失败: {e}")

        return changed

    def get_entries(self) -> List[Dict[str, Any]]:
        """获取所有数据集条目"""
        return [
            {
                "index": i,
                "name": e.name,
                "value": e.value,
                "iec_type": e.iec_type,
            }
            for i, e in enumerate(self._entries)
        ]

    def _increment_st_num(self):
        """状态变化时递增 stNum，重置 sqNum"""
        self._st_num += 1
        self._sq_num = 0

    def _increment_sq_num(self):
        """重发时递增 sqNum"""
        self._sq_num += 1

    def start(self) -> bool:
        """启动 GOOSE Publisher (含定时重发)"""
        if self._is_running:
            return True

        try:
            self._create_publisher()
            self._is_running = True

            # 启动定时重发线程
            self._retransmit_stop.clear()
            self._retransmit_thread = threading.Thread(
                target=self._retransmit_loop, daemon=True
            )
            self._retransmit_thread.start()

            log.info(f"GOOSE Publisher 已启动: goCbRef={self.go_cb_ref}, interface={self.interface}")
            return True
        except Exception as e:
            log.error(f"GOOSE Publisher 启动失败: {e}")
            self._is_running = False
            return False

    def stop(self) -> None:
        """停止 GOOSE Publisher"""
        self._is_running = False
        self._retransmit_stop.set()

        if self._retransmit_thread and self._retransmit_thread.is_alive():
            self._retransmit_thread.join(timeout=2.0)

        self._destroy_publisher()
        log.info(f"GOOSE Publisher 已停止: goCbRef={self.go_cb_ref}")

    def _destroy_publisher(self):
        """销毁底层 Publisher"""
        if self._publisher:
            iec61850.GoosePublisher_destroy(self._publisher)
            self._publisher = None
        self._is_created = False

    def _retransmit_loop(self):
        """定时重发循环 (按照 IEC 61850 规范，GOOSE 应周期性重发)"""
        while not self._retransmit_stop.is_set():
            try:
                self.publish()
                # 重发后递增 sqNum
                with self._lock:
                    self._sq_num += 1
            except Exception as e:
                log.error(f"GOOSE 重发失败: {e}")

            self._retransmit_stop.wait(self._retransmit_interval)

    def publish(self) -> bool:
        """立即发布 GOOSE 报文"""
        if not self._is_running or not self._publisher:
            return False

        with self._lock:
            try:
                # 重建 Publisher (如果有变化)
                if not self._is_created:
                    self._destroy_publisher()
                    self._create_publisher()

                # 创建 LinkedList 并填充 MMS 值
                data_set_values = iec61850.LinkedList_create()
                if not data_set_values:
                    log.error("GOOSE 发布失败: 无法创建值列表")
                    return False

                try:
                    for entry in self._entries:
                        mms_val = self._entry_to_mms_value(entry)
                        if mms_val:
                            iec61850.LinkedList_add(data_set_values, mms_val)

                    # 更新序号
                    iec61850.GoosePublisher_setStNum(self._publisher, self._st_num)
                    iec61850.GoosePublisher_setSqNum(self._publisher, self._sq_num)

                    # 发布 (带数据集值)
                    result = iec61850.GoosePublisher_publish(self._publisher, data_set_values)
                finally:
                    # 清理 LinkedList
                    try:
                        iec61850.LinkedList_destroyDeep(data_set_values, iec61850.MmsValue_delete)
                    except Exception:
                        try:
                            iec61850.LinkedList_destroy(data_set_values)
                        except Exception:
                            pass

                if result == 0:
                    log.debug(f"GOOSE 发布成功: stNum={self._st_num}, sqNum={self._sq_num}")
                    return True
                else:
                    log.warning(f"GOOSE 发布失败: goCbRef={self.go_cb_ref}, result={result}")
                    return False
            except Exception as e:
                log.error(f"GOOSE 发布异常: {e}")
                return False

    @property
    def is_running(self) -> bool:
        return self._is_running

    @property
    def st_num(self) -> int:
        return self._st_num

    @property
    def sq_num(self) -> int:
        return self._sq_num

    def get_status(self) -> Dict[str, Any]:
        """获取 Publisher 状态信息"""
        return {
            "go_cb_ref": self.go_cb_ref,
            "go_id": self.go_id,
            "data_set_ref": self.data_set_ref,
            "app_id": self.app_id,
            "conf_rev": self.conf_rev,
            "st_num": self._st_num,
            "sq_num": self._sq_num,
            "time_allowed_to_live": self.time_allowed_to_live,
            "interface": self.interface,
            "simulation": self.simulation,
            "is_running": self._is_running,
            "dst_mac": ":".join(f"{b:02X}" for b in self.dst_mac),
            "vlan_id": self.vlan_id,
            "vlan_prio": self.vlan_prio,
            "entry_count": len(self._entries),
        }
