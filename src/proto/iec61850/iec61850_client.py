"""
IEC 61850 MMS 客户端封装
基于 pyiec61850 (libiec61850 Python bindings) 实现 MMS 客户端

支持两种地址格式:
  1. 简单地址 (如 "1", "2"): 使用固定的 MMXU1/GGIO1/GGIO2 逻辑节点结构构建引用
  2. 完整引用路径 (如 "MEAS/M0GGIO1.AnIn1.mag.f"): 直接使用原始引用路径
"""

import time
from typing import Any, Dict, List, Optional, Tuple, Union
from .log import log
from .iec61850_defs import (
    YC_LN_CLASSES, YX_LN_CLASSES, YK_LN_CLASSES, YT_LN_CLASSES,
    ALL_LN_CLASSES, SKIP_SYSTEM_DOS, SIGNAL_DOS,
)

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
        rest_parts = rest.split('.', 2)
        if len(rest_parts) < 2:
            return None
        ln_name = rest_parts[0]
        do_name = rest_parts[1]
        da_path = rest_parts[2] if len(rest_parts) > 2 else ""
        return (ld_inst, ln_name, do_name, da_path)
    except Exception:
        return None


# DA 路径 -> frame_type 映射 (用于模型发现时推断测点类型)
_DA_PATH_TO_FRAME_TYPE = {
    "mag.f": 0,       # 遥测
    "cVal.mag.f": 0,  # 遥测 (CMV)
    "instMag.f": 0,   # 遥测 (SAV)
    "stVal": 1,       # 遥信
    "ctlVal": 2,      # 遥控 (布尔型)
    "Oper.ctlVal": 2, # 遥控
    "setVal": 3,      # 遥调
}

# DA 第一层名称 -> (完整 DA 路径, frame_type) 映射
# 用于从服务器模型发现 DA 结构时，根据 getDataDirectory 返回的 DA 名称
# 推断测点类型和完整 DA 路径，避免猜测导致的测点遗漏
_DA_PATTERNS = {
    # 遥测 (YC) - 测量值类 DA
    "mag": ("mag.f", 0),          # MV/SAV CDC: 浮点测量值
    "cVal": ("cVal.mag.f", 0),    # CMV CDC: 复数测量值
    "instMag": ("instMag.f", 0),  # SAV CDC: 瞬时测量值
    "mxVal": ("mxVal.f", 0),      # 某些实现的测量值
    "fCVal": ("fCVal.mag.f", 0),  # 复数浮点测量值
    # 遥信 (YX) - 状态值类 DA
    "stVal": ("stVal", 1),        # SPS/ACT/ACD/SEC CDC: 状态值
    # 遥控 (YK) - 控制值类 DA
    "ctlVal": ("ctlVal", 2),      # SPC/DPC CDC: 控制值
    "Oper": ("Oper.ctlVal", 2),   # SPC/DPC CDC: 安全操作控制
    # 遥调 (YT) - 设定值类 DA
    "setVal": ("setVal", 3),      # APC/BSC/ISC CDC: 设定值
    "wVal": ("wVal.f", 3),        # 某些实现的设定值
}


class IEC61850Client:
    """IEC 61850 MMS 客户端

    使用 pyiec61850 通过 MMS 协议连接到 IEC 61850 服务器，
    读写远端 IED 的数据属性。

    支持两种地址格式:
    1. 简单地址 (int/str 不含 '/'): 使用固定结构构建 MMS 引用
    2. 完整引用路径 (str 含 '/'): 直接使用原始引用路径构建 MMS 引用
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
        self._point_refs: Dict[Tuple[Union[int, str], int], str] = {}

    def _build_ref(self, address, frame_type: int) -> str:
        """根据地址和帧类型构建 MMS 引用路径

        对于完整引用路径 (含 '/'): 拼接 model_name 前缀构建完整 MMS 引用
        对于简单地址: 使用 MMXU1/GGIO1/GGIO2 固定结构
        """
        if _is_full_ref(address):
            # 完整引用路径: "MEAS/M0GGIO1.AnIn1.mag.f"
            # MMS 引用: "EMSMEAS/M0GGIO1.AnIn1.mag.f"
            parsed = _parse_ref(address)
            if parsed:
                ld_inst = parsed[0]
                rest = address.split('/', 1)[1]
                return f"{self.model_name}{ld_inst}/{rest}"
            # 解析失败，回退到简单模式
            log.warning(f"IEC61850 客户端无法解析引用路径 {address}，回退到简单模式")

        # 简单地址模式: 使用固定结构
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

    def add_point(self, address, frame_type: int) -> str:
        """注册测点引用路径

        Args:
            address: 测点地址 (简单地址或完整引用路径)
            frame_type: 帧类型

        Returns:
            MMS 引用路径
        """
        key = (address, frame_type)
        if key not in self._point_refs:
            self._point_refs[key] = self._build_ref(address, frame_type)
        return self._point_refs[key]

    def connect(self) -> bool:
        """连接到 IEC 61850 服务器（同步阻塞方法，应在线程中调用）

        注意: IedConnection_connect 是 C 扩展同步阻塞调用，会持有 GIL，
        因此不能在事件循环中直接调用，也不能用 run_in_executor（同样受 GIL 阻塞）。
        应通过 threading.Thread(daemon=True) 在独立线程中调用。
        """
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
                # 连接失败时清理资源
                self._cleanup_connection()
                return False
        except Exception as e:
            log.error(f"IEC 61850 连接异常: {e}")
            self._is_connected = False
            self._cleanup_connection()
            return False

    def _cleanup_connection(self):
        """清理连接资源"""
        if self._connection:
            try:
                iec61850.IedConnection_destroy(self._connection)
            except Exception:
                pass
            self._connection = None

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

    def read_point(self, address, frame_type: int) -> Any:
        """读取测点值

        Args:
            address: 测点地址 (简单地址或完整引用路径)
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

    def write_point(self, address, value: Any, frame_type: int) -> bool:
        """写入测点值

        Args:
            address: 测点地址 (简单地址或完整引用路径)
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
        """从 LinkedList 中提取字符串列表

        LinkedList 结构: linked_list 本身是头节点, LinkedList_getNext 返回下一个节点。
        之前的实现从 getNext 开始遍历, 导致遗漏了链表的第一个元素。
        正确做法: 先读取头节点数据, 再遍历后续节点。
        """
        if linked_list is None:
            return []
        items = []
        # 先读取头节点的数据
        try:
            head_data = iec61850.toCharP(linked_list.data)
            if head_data:
                items.append(head_data)
        except Exception:
            pass
        # 再遍历后续节点
        item = iec61850.LinkedList_getNext(linked_list)
        while item:
            try:
                items.append(iec61850.toCharP(item.data))
            except Exception:
                pass
            item = iec61850.LinkedList_getNext(item)
        iec61850.LinkedList_destroy(linked_list)
        return items

    def _extract_ln_class(self, ln_name: str) -> Optional[str]:
        """从可能带前缀的逻辑节点名中提取 lnClass

        IEC 61850 LN 名称格式: {prefix}{lnClass}{inst}
        例如: METMMXU1 → prefix=MET, lnClass=MMXU, inst=1
              TRIPPTRC1 → prefix=TRIP, lnClass=PTRC, inst=1
        """
        alpha = ''.join(c for c in ln_name if c.isalpha())
        # 直接匹配
        if alpha in ALL_LN_CLASSES:
            return alpha
        # 从后往前尝试匹配, 去除前缀部分
        for i in range(1, len(alpha)):
            suffix = alpha[i:]
            if suffix in ALL_LN_CLASSES:
                return suffix
        return None

    def _infer_frame_type_from_do(self, ln_name: str, do_name: str) -> Optional[int]:
        """根据逻辑节点名和数据对象名推断 frame_type

        优先级:
        1. DO 名称前缀约定 (MV_, SPS_, SPC_, APC_) — 简单地址模式
        2. 跳过 ENC 类型系统 DO (Mod, Beh, Health, NamPlt) — 不可布尔读取
        3. LN class 推断 — 支持带前缀的 LN 名 (如 METMMXU1→MMXU)
        4. 信号 DO 名称 (Op, Tr, Str 等) — 映射为遥信
        5. DO 名称模式推断 — 根据常见 DO 命名规则
        """
        # 1. 前缀约定 (简单地址模式创建的 DO)
        if do_name.startswith("MV_"):
            return 0
        elif do_name.startswith("SPS_"):
            return 1
        elif do_name.startswith("SPC_"):
            return 2
        elif do_name.startswith("APC_"):
            return 3

        # 2. 排除系统逻辑节点
        ln_upper = ln_name.upper()
        if ln_upper in ("LLN0", "LPHD0"):
            return None

        # 3. 跳过 ENC 类型系统 DO (CDC 类型为枚举, 不可用 readBooleanValue 读取)
        if do_name in SKIP_SYSTEM_DOS:
            return None

        # 4. 信号 DO (CDC 类型为 ACT/SPS, stVal 是布尔值)
        if do_name in SIGNAL_DOS:
            return 1  # 遥信

        # 4. LN class 推断 (支持带前缀的 LN 名)
        ln_class = self._extract_ln_class(ln_name)
        if ln_class:
            if ln_class in YC_LN_CLASSES:
                return 0  # 遥测
            elif ln_class in YX_LN_CLASSES:
                return 1  # 遥信
            elif ln_class in YK_LN_CLASSES:
                return 2  # 遥控
            elif ln_class in YT_LN_CLASSES:
                return 3  # 遥调

        # 5. DO 名称模式推断 (基于 IEC 61850 CDC 命名约定)
        # 测量类 DO (CDC: MV, WYE, DEL, SEQ, HMV 等)
        if do_name.startswith(("TotW", "TotV", "TotA", "TotF", "TotPF", "TotQ")):
            return 0  # 遥测
        if do_name.startswith(("A", "V", "W", "Hz", "PF", "PhV", "PPV", "Amp", "Vol")):
            return 0  # 遥测
        # 状态类 DO (CDC: SPS, ACT, ACD, SEC, BCR)
        if do_name.startswith(("St", "Ind", "Blk", "Sw")):
            return 1  # 遥信
        # 控制类 DO (CDC: SPC, DPC, ENC)
        if do_name.startswith(("Ctl", "Pos")):
            return 2  # 遥控
        # 设点类 DO (CDC: APC, BSC, ISC)
        if do_name.startswith(("Spt", "ValW", "Csx")):
            return 3  # 遥调

        return None

    def _infer_da_path(self, frame_type: int) -> str:
        """根据 frame_type 推断数据属性路径"""
        if frame_type == 0:
            return "mag.f"
        elif frame_type == 1:
            return "stVal"
        elif frame_type == 2:
            return "ctlVal"
        elif frame_type == 3:
            return "ctlVal"
        return ""

    def _discover_da_paths(self, do_ref: str) -> List[Tuple[str, int]]:
        """通过查询服务器模型发现 DO 下的 DA 路径

        使用 IedConnection_getDataDirectory 获取 DO 的直接子 DA 列表,
        然后根据 DA 名称匹配 _DA_PATTERNS, 确定正确的 DA 路径和测点类型.
        这比 _infer_da_path 更可靠, 因为它是基于服务端实际数据模型,
        而不是猜测。例如 GGIO.AnIn1 的实际 DA 是 mag (遥测),
        而不是 _infer_da_path 基于 GGIO∈YX 猜测的 stVal.

        Args:
            do_ref: 数据对象的完整 MMS 引用 (如 "EMSGenericLD/MMXU1.TotW")

        Returns:
            (da_path, frame_type) 元组列表, 空列表表示发现失败
        """
        try:
            result = iec61850.IedConnection_getDataDirectory(self._connection, do_ref)
            da_list = result[0] if isinstance(result, (list, tuple)) else result
            error = result[1] if isinstance(result, (list, tuple)) else 0

            if error != iec61850.IED_ERROR_OK or da_list is None:
                return []

            das = self._get_list_from_linked_list(da_list)

            found = []
            for da_name in das:
                if da_name in _DA_PATTERNS:
                    da_path, frame_type = _DA_PATTERNS[da_name]
                    found.append((da_path, frame_type))

            return found
        except Exception as e:
            log.debug(f"查询 DA 目录失败: {do_ref}, 错误: {e}")
            return []

    def _extract_code_from_address(self, address: str) -> str:
        """从 address 中提取短编码

        简单地址模式 (MV_/SPS_/SPC_/APC_ 前缀):
            "GenericLD/MMXU1.MV_1.mag.f" -> "1"
        ICD 动态模型模式:
            "MEAS/M0GGIO1.AnIn1.mag.f" -> "M0GGIO1.AnIn1"
        """
        parsed = _parse_ref(address)
        if parsed:
            ld_inst, ln_name, do_name, da_path = parsed
            # 简单地址模式: DO 名带前缀
            if do_name.startswith(("MV_", "SPS_", "SPC_", "APC_")):
                # 提取前缀后的部分
                for prefix in ("MV_", "SPS_", "SPC_", "APC_"):
                    if do_name.startswith(prefix):
                        return do_name[len(prefix):]
            # ICD 模式: LN.DO
            return f"{ln_name}.{do_name}"
        return address

    def discover_model(self) -> List[Dict[str, Any]]:
        """动态发现并映射服务端的数据模型

        支持两种模型结构:
        1. 简单地址模式: 识别 MV_/SPS_/SPC_/APC_ 前缀的 DO
        2. 动态模型模式 (ICD 导入): 识别没有前缀的 DO，根据 LN 推断 frame_type

        Returns:
            发现的测点列表，每个元素为 {"address": str, "frame_type": int, "ref": str, "code": str}
            address 使用 MMS 引用格式 (包含 model_name 前缀)，如 "EMSGenericLD/MMXU1.MV_1.mag.f"
            code 为短编码: 简单地址模式为原始地址(如 "1")，ICD 模式为 "LN.DO"(如 "M0GGIO1.AnIn1")
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
                # 跳过系统逻辑节点
                if ln == "LLN0" or ln == "LPHD0":
                    continue

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
                
                if not dos:
                    log.warning(f"逻辑节点 {ln_ref} 下没有发现数据对象 (DO 列表为空)")
                    continue
                    
                for do in dos:
                    full_do_ref = f"{ln_ref}.{do}"
                    
                    try:
                        # 简单地址模式: DO 名带前缀 (MV_, SPS_, SPC_, APC_)
                        # 前缀已经明确了测点类型和 DA 路径, 无需查询服务端
                        if do.startswith("MV_"):
                            addr = do[3:]
                            da_path = "mag.f"
                            frame_type = 0
                            ref = f"{full_do_ref}.{da_path}"
                            address = f"{ld}/{ln}.{do}.{da_path}"
                            code = addr
                            self._point_refs[(address, frame_type)] = ref
                            discovered_points.append({"address": address, "frame_type": frame_type, "ref": ref, "code": code})
                            log.info(f"映射测点: ({address}, {frame_type}) -> {ref}")
                        elif do.startswith("SPS_"):
                            addr = do[4:]
                            da_path = "stVal"
                            frame_type = 1
                            ref = f"{full_do_ref}.{da_path}"
                            address = f"{ld}/{ln}.{do}.{da_path}"
                            code = addr
                            self._point_refs[(address, frame_type)] = ref
                            discovered_points.append({"address": address, "frame_type": frame_type, "ref": ref, "code": code})
                            log.info(f"映射测点: ({address}, {frame_type}) -> {ref}")
                        elif do.startswith("SPC_"):
                            addr = do[4:]
                            da_path = "ctlVal"
                            frame_type = 2
                            ref = f"{full_do_ref}.{da_path}"
                            address = f"{ld}/{ln}.{do}.{da_path}"
                            code = addr
                            self._point_refs[(address, frame_type)] = ref
                            discovered_points.append({"address": address, "frame_type": frame_type, "ref": ref, "code": code})
                            log.info(f"映射测点: ({address}, {frame_type}) -> {ref}")
                        elif do.startswith("APC_"):
                            addr = do[4:]
                            da_path = "ctlVal"
                            frame_type = 3
                            ref = f"{full_do_ref}.{da_path}"
                            address = f"{ld}/{ln}.{do}.{da_path}"
                            code = addr
                            self._point_refs[(address, frame_type)] = ref
                            discovered_points.append({"address": address, "frame_type": frame_type, "ref": ref, "code": code})
                            log.info(f"映射测点: ({address}, {frame_type}) -> {ref}")
                        else:
                            # 动态模型模式 (ICD 导入): DO 没有前缀
                            # 优先通过查询服务端实际数据模型发现 DA 结构,
                            # 避免因猜测错误导致测点遗漏 (如 GGIO.AnIn1 实际是 mag.f 遥测,
                            # 但推断模式会基于 GGIO∈YX 错误地使用 stVal)
                            da_paths = self._discover_da_paths(full_do_ref)

                            if da_paths:
                                # 从服务端模型发现了具体的 DA 路径
                                for da_path, frame_type in da_paths:
                                    ref = f"{full_do_ref}.{da_path}"
                                    address = f"{ld}/{ln}.{do}.{da_path}"
                                    code = f"{ln}.{do}"
                                    self._point_refs[(address, frame_type)] = ref
                                    discovered_points.append({"address": address, "frame_type": frame_type, "ref": ref, "code": code})
                                    log.info(f"映射测点: ({address}, {frame_type}) -> {ref}")
                            else:
                                # getDataDirectory 不可用或失败, 回退到推断模式
                                frame_type = self._infer_frame_type_from_do(ln, do)
                                if frame_type is None:
                                    log.debug(f"跳过数据对象 {full_do_ref}: 无法推断测点类型")
                                    continue

                                da_path = self._infer_da_path(frame_type)
                                ref = f"{full_do_ref}.{da_path}"
                                address = f"{ld}/{ln}.{do}.{da_path}"
                                code = f"{ln}.{do}"
                                self._point_refs[(address, frame_type)] = ref
                                discovered_points.append({"address": address, "frame_type": frame_type, "ref": ref, "code": code})
                                log.info(f"映射测点: ({address}, {frame_type}) -> {ref}")
                    except Exception as e:
                        log.error(f"解析测点地址失败: {do}, 错误: {e}")
                        continue

        log.info(f"IEC 61850 动态发现完成, 耗时: {time.time() - start_time:.2f}s, 发现并映射了 {len(discovered_points)} 个测点")
        return discovered_points

    def get_discovered_points(self) -> List[Dict[str, Any]]:
        """获取当前已映射的测点列表

        Returns:
            测点列表，每个元素为 {"address": str, "frame_type": int, "ref": str, "code": str}
        """
        result = []
        for (addr, ft), ref in self._point_refs.items():
            # 从 address 中提取短编码
            code = self._extract_code_from_address(addr)
            result.append({"address": addr, "frame_type": ft, "ref": ref, "code": code})
        return result

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
