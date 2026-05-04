"""
IEC 61850 GOOSE 报文捕获引擎

直接在网络接口上监听 GOOSE 组播报文 (EtherType 0x88B8)，
捕获并解析原始报文内容，用于网络诊断和调试。

支持:
- 跨平台抓包 (Windows/Linux)
- 原始 GOOSE 报文解析 (Ethernet + GOOSE 头 + ASN.1 PDU)
- 环形缓冲区存储
- 启动/停止控制
- 按 APPID/GoCBRef 过滤

注意: 抓包功能需要管理员/root 权限。
"""

import platform
import socket
import struct
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .log import log

# ===== 常量定义 =====

# GOOSE EtherType
ETHER_TYPE_GOOSE = 0x88B8
ETHER_TYPE_VLAN = 0x8100

# GOOSE PDU 标签 (ASN.1 BER-TLV)
TAG_GOOSE_PDU = 0xA1         # GOOSE PDU (context-tagged, constructed)
TAG_GOCB_REF = 0x80          # [0] IMPLICIT VisibleString
TAG_TIME_ALLOWED_TO_LIVE = 0x81  # [1] IMPLICIT Integer (INT32)
TAG_DATASET = 0x82           # [2] IMPLICIT VisibleString
TAG_GO_ID = 0x83             # [3] IMPLICIT VisibleString
TAG_ST_NUM = 0x84            # [4] IMPLICIT Integer (INT32)
TAG_SQ_NUM = 0x85            # [5] IMPLICIT Integer (INT32)
TAG_SIMULATION = 0x86        # [6] IMPLICIT Boolean
TAG_CONF_REV = 0x87          # [7] IMPLICIT Integer (INT32)
TAG_NDS_COM = 0x88           # [8] IMPLICIT Boolean
TAG_NUM_DAT_SET_ENTRIES = 0x89  # [9] IMPLICIT Integer (INT32)
TAG_ALL_DATA = 0x8A          # [10] IMPLICIT SEQUENCE OF Data

# MMS 数据类型 (BER-TLV within ALL_DATA)
MMS_BOOLEAN = 0x09           # BOOLEAN
MMS_INTEGER = 0x02           # INTEGER
MMS_BIT_STRING = 0x03        # BIT STRING
MMS_OCTET_STRING = 0x04     # OCTET STRING
MMS_VISIBLE_STRING = 0x1A   # VisibleString
MMS_UTC_TIME = 0x11          # UTCTime
MMS_FLOAT = 0x07            # FLOAT (自定义扩展)
MMS_UNSIGNED = 0x06         # UNSIGNED (自定义扩展)


class GooseCapturedPacket:
    """单条捕获的 GOOSE 报文记录"""

    def __init__(self, raw_data: bytes, src_mac: str, dst_mac: str):
        self.raw_data = raw_data
        self.src_mac = src_mac
        self.dst_mac = dst_mac
        self.timestamp = time.time()
        self.length = len(raw_data)

        # 解析后的 GOOSE 字段
        self.app_id: int = 0
        self.go_cb_ref: str = ""
        self.go_id: str = ""
        self.data_set_ref: str = ""
        self.st_num: int = 0
        self.sq_num: int = 0
        self.time_allowed_to_live: int = 0
        self.conf_rev: int = 0
        self.simulation: bool = False
        self.nds_com: bool = False
        self.num_dat_set_entries: int = 0
        self.data_values: List[Dict[str, Any]] = []
        self.vlan_id: int = 0
        self.vlan_prio: int = 0
        self.has_vlan: bool = False

    @property
    def formatted_time(self) -> str:
        dt = datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "src_mac": self.src_mac,
            "dst_mac": self.dst_mac,
            "timestamp": self.timestamp,
            "time": self.formatted_time,
            "length": self.length,
            "app_id": self.app_id,
            "app_id_hex": f"0x{self.app_id:04X}",
            "go_cb_ref": self.go_cb_ref,
            "go_id": self.go_id,
            "data_set_ref": self.data_set_ref,
            "st_num": self.st_num,
            "sq_num": self.sq_num,
            "time_allowed_to_live": self.time_allowed_to_live,
            "conf_rev": self.conf_rev,
            "simulation": self.simulation,
            "nds_com": self.nds_com,
            "num_dat_set_entries": self.num_dat_set_entries,
            "vlan_id": self.vlan_id,
            "vlan_prio": self.vlan_prio,
            "has_vlan": self.has_vlan,
            "data_values": self.data_values,
            "hex_data": self.raw_data.hex(),
            "hex_string": self._format_hex(),
        }

    def _format_hex(self) -> str:
        """格式化十六进制显示 (每16字节一行)"""
        lines = []
        for i in range(0, len(self.raw_data), 16):
            chunk = self.raw_data[i:i + 16]
            hex_part = " ".join(f"{b:02x}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{i:04x}  {hex_part:<48}  {ascii_part}")
        return "\n".join(lines)


class GooseCapture:
    """GOOSE 报文捕获器

    在指定网络接口上监听 GOOSE 组播报文并捕获解析。

    用法:
        capture = GooseCapture("eth0")
        capture.start()
        packets = capture.get_packets()
        capture.stop()
    """

    def __init__(self, interface: str = "", max_packets: int = 500):
        self.interface = interface
        self._max_packets = max_packets
        self._packets: deque = deque(maxlen=max_packets)
        self._lock = threading.Lock()
        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
        self._packet_count = 0

        # 可选的 APPID 过滤 (None = 不过滤)
        self._filter_app_id: Optional[int] = None
        # 可选的 GoCBRef 过滤 (空字符串 = 不过滤)
        self._filter_go_cb_ref: str = ""

        # 接收回调 (可选)
        self._callback: Optional[Callable[[GooseCapturedPacket], None]] = None

    # ===== 过滤设置 =====

    def set_app_id_filter(self, app_id: Optional[int]) -> None:
        """设置 APPID 过滤 (None 表示不过滤)"""
        self._filter_app_id = app_id

    def set_go_cb_ref_filter(self, go_cb_ref: str) -> None:
        """设置 GoCBRef 过滤 (空字符串表示不过滤)"""
        self._filter_go_cb_ref = go_cb_ref

    def set_callback(self, callback: Optional[Callable[[GooseCapturedPacket], None]]) -> None:
        """设置捕获回调 (传入 None 清除回调)"""
        self._callback = callback

    # ===== 捕获控制 =====

    def start(self) -> bool:
        """启动 GOOSE 报文捕获

        Returns:
            True 表示启动成功, False 表示启动失败
        """
        if self._is_running:
            log.warning("GOOSE 捕获已在运行中")
            return True

        self._stop_event.clear()
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
        )
        self._capture_thread.start()

        # 等待线程启动
        self._stop_event.wait(0.5)
        if not self._is_running:
            # 捕获线程可能启动失败
            if self._capture_thread.is_alive():
                self._stop_event.set()
                self._capture_thread.join(timeout=2.0)
            return False

        log.info(f"GOOSE 报文捕获已启动: interface={self.interface or 'any'}")
        return True

    def stop(self) -> None:
        """停止 GOOSE 报文捕获 (阻塞版本，会等待线程退出)"""
        self._stop_event.set()
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=3.0)
        self._is_running = False
        log.info("GOOSE 报文捕获已停止")

    def signal_stop(self) -> None:
        """信号停止捕获 (非阻塞，仅设置停止标记，不等待线程)

        适用于 async 环境，避免 thread.join() 阻塞事件循环。
        捕获线程是 daemon 线程，会在下一轮循环中自行退出。
        """
        self._stop_event.set()
        self._is_running = False

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ===== 数据访问 =====

    def get_packets(self, count: int = 0, filter_app_id: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取捕获的报文列表"""
        with self._lock:
            packets = list(self._packets)

        if filter_app_id is not None:
            packets = [p for p in packets if p.app_id == filter_app_id]

        if count > 0:
            packets = packets[-count:]

        return [p.to_dict() for p in packets]

    def get_statistics(self) -> Dict[str, Any]:
        """获取捕获统计信息"""
        with self._lock:
            total = len(self._packets)
            app_ids: Dict[int, int] = {}
            go_cb_refs: Dict[str, int] = {}
            for pkt in self._packets:
                if pkt.app_id:
                    app_ids[pkt.app_id] = app_ids.get(pkt.app_id, 0) + 1
                if pkt.go_cb_ref:
                    go_cb_refs[pkt.go_cb_ref] = go_cb_refs.get(pkt.go_cb_ref, 0) + 1

        return {
            "is_running": self._is_running,
            "total_captured": self._packet_count,
            "buffer_size": total,
            "max_buffer_size": self._max_packets,
            "interface": self.interface,
            "app_ids": [{"app_id": k, "app_id_hex": f"0x{k:04X}", "count": v} for k, v in sorted(app_ids.items())],
            "go_cb_refs": [{"go_cb_ref": k, "count": v} for k, v in sorted(go_cb_refs.items(), key=lambda x: -x[1])],
        }

    def clear(self) -> None:
        """清空已捕获的报文"""
        with self._lock:
            self._packets.clear()
            self._packet_count = 0

    # ===== 捕获核心 =====

    def _capture_loop(self) -> None:
        """捕获主循环"""
        sock = None
        try:
            sock = self._create_socket()
            if sock is None:
                log.error("创建捕获套接字失败")
                return

            self._is_running = True
            self._stop_event.set()  # 通知 start() 线程启动成功

            while not self._stop_event.is_set():
                try:
                    # 设置超时以便能响应停止信号
                    if hasattr(sock, 'settimeout'):
                        sock.settimeout(1.0)
                    raw_data = sock.recv(65535)
                    self._process_packet(raw_data)
                except socket.timeout:
                    continue
                except (OSError, socket.error) as e:
                    if not self._stop_event.is_set():
                        log.warning(f"捕获套接字异常: {e}")
                    break

        except Exception as e:
            log.error(f"捕获循环异常: {e}")
        finally:
            self._is_running = False
            self._stop_event.set()
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass

    def _create_socket(self) -> Optional[socket.socket]:
        """创建捕获套接字 (跨平台)"""
        system = platform.system().lower()

        if system in ("linux", "darwin"):
            return self._create_linux_socket()
        elif system == "windows":
            return self._create_windows_socket()
        else:
            log.error(f"不支持的操作系统: {system}")
            return None

    def _create_linux_socket(self) -> Optional[socket.socket]:
        """Linux: 使用 AF_PACKET 原始套接字"""
        try:
            # AF_PACKET, SOCK_RAW, htons(ETH_P_ALL)
            sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
            if self.interface:
                sock.bind((self.interface, 0))
            log.info(f"Linux 原始套接字创建成功: interface={self.interface or 'all'}")
            return sock
        except PermissionError:
            log.error("需要 root 权限才能创建原始套接字")
            return None
        except Exception as e:
            log.error(f"创建 Linux 套接字失败: {e}")
            return None

    def _create_windows_socket(self) -> Optional[socket.socket]:
        """Windows: 使用原始 IP 套接字 + promiscuous 模式"""
        try:
            # Windows 需要管理员权限
            sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_IP)

            # 获取本机 IP
            host_ip = self._get_windows_host_ip()
            if not host_ip:
                log.warning("无法获取本机 IP，使用 0.0.0.0")
                host_ip = "0.0.0.0"

            sock.bind((host_ip, 0))

            # 启用 promiscuous 模式 (仅 Windows)
            try:
                import win32file
                # SIO_RCVALL = 0x98000001
                sock.ioctl(0x98000001, 1)
                log.info(f"Windows 原始套接字创建成功: IP={host_ip}")
            except ImportError:
                log.warning("pywin32 未安装，无法设置混杂模式，可能无法捕获 GOOSE 组播报文")
                # 尝试通过 socket 本身设置
                try:
                    sock.ioctl(socket.SIO_RCVALL, socket.RCVALL_ON)
                except (AttributeError, OSError):
                    log.warning("无法启用混杂模式，捕获可能受限")
            except Exception as e:
                log.warning(f"设置混杂模式失败: {e}")

            return sock
        except PermissionError:
            log.error("需要管理员权限才能创建原始套接字 (请以管理员身份运行)")
            return None
        except Exception as e:
            log.error(f"创建 Windows 套接字失败: {e}")
            return None

    def _get_windows_host_ip(self) -> Optional[str]:
        """获取本机 IP 地址"""
        try:
            hostname = socket.gethostname()
            return socket.gethostbyname(hostname)
        except Exception:
            return None

    def _process_packet(self, raw_data: bytes) -> None:
        """处理捕获的原始数据包"""
        if len(raw_data) < 14:  # 最小以太网帧头
            return

        # 解析以太网帧头
        dst_mac = ":".join(f"{b:02X}" for b in raw_data[0:6])
        src_mac = ":".join(f"{b:02X}" for b in raw_data[6:12])
        eth_type = (raw_data[12] << 8) | raw_data[13]

        # 检查是否是 VLAN 标签
        offset = 14  # 以太网帧头长度
        vlan_id = 0
        vlan_prio = 0
        has_vlan = False

        if eth_type == ETHER_TYPE_VLAN and len(raw_data) >= 18:
            has_vlan = True
            vlan_info = (raw_data[14] << 8) | raw_data[15]
            vlan_prio = (vlan_info >> 13) & 0x07
            vlan_id = vlan_info & 0x0FFF
            eth_type = (raw_data[16] << 8) | raw_data[17]
            offset = 18

        # 检查是否是 GOOSE 报文
        if eth_type != ETHER_TYPE_GOOSE:
            return

        # 创建报文记录
        packet = GooseCapturedPacket(raw_data, src_mac, dst_mac)

        # 解析 GOOSE 头
        if len(raw_data) < offset + 8:  # 至少需要 APPID(2) + Length(2) + Reserved(4)
            return

        packet.app_id = (raw_data[offset] << 8) | raw_data[offset + 1]
        goose_length = (raw_data[offset + 2] << 8) | raw_data[offset + 3]
        packet.has_vlan = has_vlan
        packet.vlan_id = vlan_id
        packet.vlan_prio = vlan_prio

        # APPID 过滤
        if self._filter_app_id is not None and packet.app_id != self._filter_app_id:
            return

        # 解析 GOOSE PDU (从 offset + 8 开始)
        pdu_start = offset + 8
        self._parse_goose_pdu(packet, raw_data, pdu_start)

        # GoCBRef 过滤
        if self._filter_go_cb_ref and self._filter_go_cb_ref not in packet.go_cb_ref:
            return

        # 存储
        with self._lock:
            self._packets.append(packet)
            self._packet_count += 1

        # 回调
        if self._callback:
            try:
                self._callback(packet)
            except Exception as e:
                log.error(f"捕获回调异常: {e}")

    def _parse_goose_pdu(self, packet: GooseCapturedPacket, data: bytes, offset: int) -> None:
        """解析 GOOSE PDU (ASN.1 BER-TLV)"""
        if offset >= len(data):
            return

        # GOOSE PDU 应该以 0xA1 开头
        if data[offset] != TAG_GOOSE_PDU:
            log.debug(f"GOOSE PDU 起始标签不符合预期: 0x{data[offset]:02X}")
            return

        offset += 1
        # 读取长度
        pdu_length, offset = self._read_ber_length(data, offset)
        if pdu_length is None or pdu_length < 0:
            return

        pdu_end = offset + pdu_length
        if pdu_end > len(data):
            pdu_end = len(data)

        # 解析 PDU 中的各个字段
        while offset < pdu_end:
            tag = data[offset]
            offset += 1

            field_length, offset = self._read_ber_length(data, offset)
            if field_length is None:
                break

            field_end = offset + field_length
            if field_end > len(data):
                break

            if tag == TAG_GOCB_REF:
                packet.go_cb_ref = data[offset:field_end].decode("utf-8", errors="replace")
            elif tag == TAG_TIME_ALLOWED_TO_LIVE:
                packet.time_allowed_to_live = self._parse_ber_integer(data, offset, field_end)
            elif tag == TAG_DATASET:
                packet.data_set_ref = data[offset:field_end].decode("utf-8", errors="replace")
            elif tag == TAG_GO_ID:
                packet.go_id = data[offset:field_end].decode("utf-8", errors="replace")
            elif tag == TAG_ST_NUM:
                packet.st_num = self._parse_ber_integer(data, offset, field_end)
            elif tag == TAG_SQ_NUM:
                packet.sq_num = self._parse_ber_integer(data, offset, field_end)
            elif tag == TAG_SIMULATION:
                packet.simulation = data[offset] != 0
            elif tag == TAG_CONF_REV:
                packet.conf_rev = self._parse_ber_integer(data, offset, field_end)
            elif tag == TAG_NDS_COM:
                packet.nds_com = data[offset] != 0
            elif tag == TAG_NUM_DAT_SET_ENTRIES:
                packet.num_dat_set_entries = self._parse_ber_integer(data, offset, field_end)
            elif tag == TAG_ALL_DATA:
                packet.data_values = self._parse_ber_all_data(data, offset, field_end)

            offset = field_end

    def _read_ber_length(self, data: bytes, offset: int) -> Tuple[Optional[int], int]:
        """读取 BER-TLV 长度字段"""
        if offset >= len(data):
            return None, offset

        first = data[offset]
        offset += 1

        if first & 0x80:
            # 长格式
            num_bytes = first & 0x7F
            if num_bytes == 0:
                return None, offset  # 不定长，暂不支持
            if offset + num_bytes > len(data):
                return None, offset
            length = 0
            for _ in range(num_bytes):
                length = (length << 8) | data[offset]
                offset += 1
            return length, offset
        else:
            # 短格式
            return first, offset

    def _parse_ber_integer(self, data: bytes, start: int, end: int) -> int:
        """解析 BER 编码的整数"""
        value = 0
        for i in range(start, end):
            value = (value << 8) | data[i]
        return value

    def _parse_ber_all_data(self, data: bytes, start: int, end: int) -> List[Dict[str, Any]]:
        """解析 ALL_DATA (SEQUENCE OF Data)"""
        values = []
        offset = start

        while offset < end:
            # MMS 类型标签
            if offset >= end:
                break

            mms_tag = data[offset]
            offset += 1

            mms_length, offset = self._read_ber_length(data, offset)
            if mms_length is None:
                break

            field_end = offset + mms_length
            if field_end > end:
                break

            entry: Dict[str, Any] = {"type": "unknown", "value": None}

            if mms_tag == MMS_BOOLEAN:
                entry["type"] = "boolean"
                entry["value"] = bool(data[offset])
            elif mms_tag == MMS_INTEGER:
                entry["type"] = "integer"
                entry["value"] = self._parse_ber_integer(data, offset, field_end)
            elif mms_tag == MMS_BIT_STRING:
                entry["type"] = "bitstring"
                # BIT STRING 第一个字节是未使用位数
                unused_bits = data[offset] if mms_length > 0 else 0
                val = 0
                for i in range(offset + 1, field_end):
                    val = (val << 8) | data[i]
                entry["value"] = val
            elif mms_tag == MMS_OCTET_STRING:
                entry["type"] = "octet_string"
                entry["value"] = data[offset:field_end].hex()
            elif mms_tag == MMS_VISIBLE_STRING:
                entry["type"] = "string"
                entry["value"] = data[offset:field_end].decode("utf-8", errors="replace")
            elif mms_tag == MMS_UTC_TIME:
                entry["type"] = "timestamp"
                entry["value"] = self._parse_ber_integer(data, offset, field_end)
            elif mms_tag == MMS_FLOAT:
                entry["type"] = "float"
                if mms_length >= 4:
                    import struct as _struct
                    entry["value"] = round(_struct.unpack(">f", data[offset:offset + 4])[0], 6)
                else:
                    entry["value"] = 0.0
            elif mms_tag == MMS_UNSIGNED:
                entry["type"] = "unsigned"
                entry["value"] = self._parse_ber_integer(data, offset, field_end)
            else:
                entry["type"] = f"unknown(0x{mms_tag:02X})"
                entry["value"] = data[offset:field_end].hex()

            values.append(entry)
            offset = field_end

        return values

    def get_status(self) -> Dict[str, Any]:
        """获取捕获器状态"""
        return {
            "interface": self.interface,
            "is_running": self._is_running,
            "max_packets": self._max_packets,
            "packet_count": self._packet_count,
            "filter_app_id": self._filter_app_id,
            "filter_go_cb_ref": self._filter_go_cb_ref,
        }


GOOSE_CAPTURE_AVAILABLE = True
