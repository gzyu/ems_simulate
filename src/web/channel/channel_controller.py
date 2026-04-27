"""
通道管理 API 控制器
提供通道的创建、删除、点表导入等接口
"""

import os
import tempfile
from typing import Optional

from fastapi import APIRouter, Request, File, UploadFile, Form
from fastapi.responses import JSONResponse

from src.data.service.channel_service import ChannelService
from src.data.service.yc_service import YcService
from src.data.service.yx_service import YxService
from src.data.service.yk_service import YkService
from src.data.service.yt_service import YtService
from src.tools.excel_point_importer import ExcelPointImporter
from src.web.log import log
from src.config.config import Config
from src.device.factory.general_device_builder import GeneralDeviceBuilder
from src.device.types.general_device import GeneralDevice
from src.device.types.pcs import Pcs
from src.device.types.circuit_breaker import CircuitBreaker
from src.enums.modbus_def import ProtocolType
from src.web.schemas.schemas import (
    BaseResponse, ChannelCreateRequest, ChannelUpdateRequest,
    CreateAndStartDeviceRequest, CopyDeviceRequest
)


# 创建路由对象
channel_router = APIRouter(prefix="/channel", tags=["channel"])


# 协议类型映射
PROTOCOL_OPTIONS = [
    {"value": 0, "label": "Modbus RTU", "conn_types": [0, 3]},
    {"value": 1, "label": "Modbus TCP", "conn_types": [1, 2]},
    {"value": 2, "label": "IEC 104", "conn_types": [1, 2]},
    {"value": 3, "label": "DL/T645-2007", "conn_types": [0, 1, 2, 3]},
    {"value": 4, "label": "IEC 61850", "conn_types": [1, 2]},
]

# 连接类型映射
CONN_TYPE_OPTIONS = [
    {"value": 0, "label": "RTU主站"},
    {"value": 1, "label": "TCP客户端"},
    {"value": 2, "label": "TCP服务端"},
    {"value": 3, "label": "RTU从站"},
]


@channel_router.get("/protocols", response_model=BaseResponse)
async def get_protocols():
    """获取支持的协议列表"""
    try:
        return BaseResponse(
            message="获取协议列表成功",
            data={
                "protocols": PROTOCOL_OPTIONS,
                "conn_types": CONN_TYPE_OPTIONS,
            }
        )
    except Exception as e:
        log.error(f"获取协议列表失败: {e}")
        return BaseResponse(code=500, message=f"获取协议列表失败: {e}", data={})


@channel_router.get("/serial_ports", response_model=BaseResponse)
async def get_serial_ports():
    """获取可用的串口列表"""
    try:
        from src.tools.serial_port_detector import SerialPortDetector
        ports = SerialPortDetector.get_available_ports()
        return BaseResponse(message="获取串口列表成功", data=ports)
    except Exception as e:
        log.error(f"获取串口列表失败: {e}")
        return BaseResponse(code=500, message=f"获取串口列表失败: {e}", data=[])


@channel_router.post("/create", response_model=BaseResponse)
async def create_channel(req: ChannelCreateRequest, request: Request):
    """创建通道/设备"""
    try:
        # 检查通道编码是否已存在
        existing = ChannelService.get_channel_by_code(req.code)
        if existing:
            return BaseResponse(code=400, message=f"设备编码 '{req.code}' 已存在，请使用其他编码")
        
        # 检查端口是否已被其他服务端通道占用（仅对服务端模式检查）
        if req.conn_type == 2:  # TCP 服务端
            all_channels = ChannelService.get_all_channels()
            for ch in all_channels:
                # 仅检查启用的服务端通道
                if ch.get("conn_type") == 2 and ch.get("port") == req.port:
                    return BaseResponse(
                        code=400, 
                        message=f"端口 {req.port} 已被设备 '{ch.get('name')}' 占用，请使用其他端口"
                    )
        
        # 1. 首先创建 Device 记录
        from src.data.service.device_service import DeviceService
        device_id = DeviceService.create_device(
            code=req.code,
            name=req.name,
            device_type=0,  # 默认类型
            group_id=req.group_id,  # 设备组ID
        )
        
        if device_id <= 0:
            return BaseResponse(code=500, message="创建设备记录失败")
        
        # 2. 创建通道，关联到设备
        channel_id = ChannelService.create_channel(
            code=req.code,
            name=req.name,
            device_id=device_id,  # 关联设备ID
            protocol_type=req.protocol_type,
            conn_type=req.conn_type,
            ip=req.ip,
            port=req.port,
            com_port=req.com_port,
            baud_rate=req.baud_rate,
            data_bits=req.data_bits,
            stop_bits=req.stop_bits,
            parity=req.parity,
            rtu_addr=req.rtu_addr if req.protocol_type == 3 else "1",
        )
        
        if channel_id > 0:
            # ==========================================
            # 新增：立即在内存中创建设备对象，无需重启
            # ==========================================
            try:
                device_controller = request.app.state.device_controller
                
                # 1. 准备构建器
                if req.code.upper().find("PCS") != -1:
                    builder = GeneralDeviceBuilder(channel_id=channel_id, device=Pcs())
                elif req.code.upper().find("BREAKER") != -1:
                    builder = GeneralDeviceBuilder(channel_id=channel_id, device=CircuitBreaker())
                else:
                    builder = GeneralDeviceBuilder(channel_id=channel_id, device=GeneralDevice())
                
                # 2. 转换协议类型
                # 注意：ChannelService.get_protocol_type 需要 protocol_type (int) 和 conn_type (int)
                channel_data_mock = {
                    "protocol_type": req.protocol_type,
                    "conn_type": req.conn_type
                }
                protocol_enum = ChannelService.get_protocol_type(channel_data_mock)

                # 3. 配置通信参数
                if req.conn_type in [0, 3]:  # 串口
                    builder.setDeviceSerialConfig(
                        serial_port=req.com_port or "",
                        baudrate=req.baud_rate or 9600,
                        databits=req.data_bits or 8,
                        stopbits=req.stop_bits or 1,
                        parity=req.parity or "E"
                    )
                else: 
                    # 网络设备
                    # TCP客户端 和 IEC104客户端 需要连接到目标IP
                    # ProtocolType.ModbusTcpClient 等是枚举
                    if protocol_enum in [ProtocolType.ModbusTcpClient, ProtocolType.Iec104Client, ProtocolType.Dlt645Client, ProtocolType.Iec61850Client]:
                        builder.setDeviceNetConfig(port=req.port, ip=req.ip)
                    else:
                        # 服务端，监听 0.0.0.0 (Config.DEFAULT_IP)
                        builder.setDeviceNetConfig(port=req.port, ip=Config.DEFAULT_IP)

                # 4. 创建设备实例
                new_device = builder.makeGeneralDevice(
                    device_id=channel_id,
                    device_name=req.name,
                    protocol_type=protocol_enum,
                    is_start=False 
                )
                new_device.name = req.name # 确保名字一致
                
                # 5. 注册到控制器
                device_controller.device_list.append(new_device)
                device_controller.device_map[new_device.name] = new_device
                
                log.info(f"设备 {req.name} (ID: {channel_id}) 已在内存中动态创建")

            except Exception as e:
                log.error(f"内存同步创建设备失败: {e}")
                # 即使内存同步失败，数据库已创建成功，不阻拦返回，但可能需要前端刷新或重启
            return BaseResponse(
                message="创建通道成功", 
                data={
                    "channel_id": channel_id,
                    "device_id": device_id,
                }
            )
        else:
            return BaseResponse(code=500, message="创建通道失败")
            
    except Exception as e:
        log.error(f"创建通道失败: {e}")
        return BaseResponse(code=500, message=f"创建通道失败: {e}")


@channel_router.post("/import_points", response_model=BaseResponse)
async def import_points(
    request: Request,
    channel_id: int = Form(...),
    file: UploadFile = File(...)
):
    """导入 Excel 点表"""
    try:
        if not file.filename.endswith(('.xlsx', '.xls')):
            return BaseResponse(code=400, message="请上传 Excel 文件 (.xlsx 或 .xls)")
        
        # 保存上传的文件到临时目录
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            # 先删除该通道的现有点表（支持重新导入）
            from src.data.dao.point_dao import PointDao
            deleted_count = PointDao.delete_points_by_channel(channel_id)
            if deleted_count > 0:
                log.info(f"重新导入前已删除 {deleted_count} 个旧测点")
            
            # 使用导入器导入点表
            importer = ExcelPointImporter(channel_id=channel_id)
            yc_count, yx_count, yk_count, yt_count = importer.import_from_excel(tmp_path)
            
            # 4. 同步更新内存中的设备点表
            try:
                device_controller = request.app.state.device_controller
                device = device_controller.get_device_by_id(channel_id)
                if device:
                    # IEC 61850 服务端的模型在 IedServer_create 时就固定了，
                    # 后续 add_points 无法生效，必须重建设备实例
                    if device.protocol_type == ProtocolType.Iec61850Server:
                        was_running = device.is_protocol_running()
                        await _reload_device_instance(device_controller, channel_id, is_start=was_running)
                        log.info(f"IEC 61850 服务端设备 {device.name} (ID: {channel_id}) 已重建以加载新点表")
                    else:
                        device.importDataPointFromChannel(channel_id, device.protocol_type)
                        log.info(f"已同步更新设备 {device.name} (ID: {channel_id}) 的内存点表")
                else:
                    log.warning(f"导入点表后未找到内存设备 (ID: {channel_id})，需要手动加载或重启")
            except Exception as e:
                log.error(f"同步内存点表失败: {e}")

            return BaseResponse(
                message="导入点表成功",
                data={
                    "yc_count": yc_count,
                    "yx_count": yx_count,
                    "yk_count": yk_count,
                    "yt_count": yt_count,
                    "total": yc_count + yx_count + yk_count + yt_count
                }
            )
        finally:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
                
    except Exception as e:
        log.error(f"导入点表失败: {e}")
        return BaseResponse(code=500, message=f"导入点表失败: {e}")


@channel_router.post("/import_icd", response_model=BaseResponse)
async def import_icd(
    request: Request,
    channel_id: int = Form(...),
    file: UploadFile = File(...)
):
    """导入 IEC 61850 ICD/SCD/CID 文件"""
    try:
        # 验证文件类型
        valid_extensions = ('.icd', '.scd', '.cid', '.xml')
        if not file.filename.lower().endswith(valid_extensions):
            return BaseResponse(
                code=400, 
                message=f"请上传 ICD 文件 ({', '.join(valid_extensions)})"
            )
        
        # 保存上传的文件到临时目录
        suffix = os.path.splitext(file.filename)[1] or '.icd'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name
        
        try:
            # 先删除该通道的现有点表（支持重新导入）
            from src.data.dao.point_dao import PointDao
            deleted_count = PointDao.delete_points_by_channel(channel_id)
            if deleted_count > 0:
                log.info(f"重新导入前已删除 {deleted_count} 个旧测点")
            
            # 使用 ICD 导入器解析文件
            from src.tools.icd_point_importer import IcdPointImporter
            importer = IcdPointImporter(channel_id=channel_id)
            yc_count, yx_count, yk_count, yt_count = importer.import_from_icd(tmp_path)
            
            # 同步更新内存中的设备点表
            try:
                device_controller = request.app.state.device_controller
                device = device_controller.get_device_by_id(channel_id)
                if device:
                    # IEC 61850 服务端的模型在 IedServer_create 时就固定了，
                    # 后续 add_points 无法生效，必须重建设备实例
                    if device.protocol_type == ProtocolType.Iec61850Server:
                        was_running = device.is_protocol_running()
                        await _reload_device_instance(device_controller, channel_id, is_start=was_running)
                        log.info(f"IEC 61850 服务端设备 {device.name} (ID: {channel_id}) 已重建以加载新点表")
                    else:
                        device.importDataPointFromChannel(channel_id, device.protocol_type)
                        log.info(f"已同步更新设备 {device.name} (ID: {channel_id}) 的内存点表")
                else:
                    log.warning(f"导入ICD后未找到内存设备 (ID: {channel_id})，需要手动加载或重启")
            except Exception as e:
                log.error(f"同步内存点表失败: {e}")

            return BaseResponse(
                message="导入ICD文件成功",
                data={
                    "yc_count": yc_count,
                    "yx_count": yx_count,
                    "yk_count": yk_count,
                    "yt_count": yt_count,
                    "total": yc_count + yx_count + yk_count + yt_count
                }
            )
        finally:
            # 清理临时文件
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
                
    except Exception as e:
        log.error(f"导入ICD文件失败: {e}")
        return BaseResponse(code=500, message=f"导入ICD文件失败: {e}")


@channel_router.post("/create_and_start", response_model=BaseResponse)
async def create_and_start_device(req: CreateAndStartDeviceRequest, request: Request):
    """创建通道并启动设备"""
    try:
        # 获取通道信息
        channel = ChannelService.get_channel_by_id(req.channel_id)
        if not channel:
            return BaseResponse(code=404, message="通道不存在")
        
        channel_code = channel["code"]
        channel_name = channel["name"]
        ip = channel.get("ip", Config.DEFAULT_IP)
        port = channel.get("port", Config.DEFAULT_PORT)
        
        # 获取协议类型枚举
        channel_protocol_type = ChannelService.get_protocol_type(channel)
        
        # 根据设备名称选择设备类型
        if channel_code.upper().find("PCS") != -1:
            general_device_builder = GeneralDeviceBuilder(
                channel_id=req.channel_id, device=Pcs()
            )
        elif channel_code.upper().find("BREAKER") != -1:
            general_device_builder = GeneralDeviceBuilder(
                channel_id=req.channel_id, device=CircuitBreaker()
            )
        else:
            general_device_builder = GeneralDeviceBuilder(
                channel_id=req.channel_id, device=GeneralDevice()
            )
        
        # 设置网络/串口配置
        conn_type = channel.get("conn_type", 1)
        if conn_type in [0, 3]:  # 串口连接（主站或0，从站或3）
            general_device_builder.setDeviceSerialConfig(
                serial_port=channel.get("com_port", ""),
                baudrate=channel.get("baud_rate", 9600),
                databits=channel.get("data_bits", 8),
                stopbits=channel.get("stop_bits", 1),
                parity=channel.get("parity", "E")
            )
        elif (
            channel_protocol_type == ProtocolType.Iec104Client
            or channel_protocol_type == ProtocolType.ModbusTcpClient
            or channel_protocol_type == ProtocolType.Iec61850Client
        ):
            general_device_builder.setDeviceNetConfig(port=port, ip=ip)
        else:
            # 服务端默认监听配置
            general_device_builder.setDeviceNetConfig(port=port, ip=Config.DEFAULT_IP)
        
        # 创建设备
        general_device = general_device_builder.makeGeneralDevice(
            device_id=req.channel_id,
            device_name=channel_name,
            protocol_type=channel_protocol_type,
            is_start=True,
        )
        general_device.name = channel_name
        
        # 仅客户端设备启动数据更新线程（从远程服务器读取数据）
        # 服务端设备不需要，因为数据由用户手动设置或远程客户端写入
        is_client = channel_protocol_type in [
            ProtocolType.ModbusTcpClient,
            ProtocolType.Iec104Client,
            ProtocolType.Dlt645Client,
            ProtocolType.Iec61850Client,
        ]
        if is_client:
            general_device.data_update_thread.start()
        
        # 添加到设备控制器
        device_controller = request.app.state.device_controller
        device_controller.device_list.append(general_device)
        device_controller.device_map[general_device.name] = general_device
        
        log.info(f"设备 {channel_name} 创建并启动成功")
        
        return BaseResponse(message="设备创建并启动成功", data={"device_name": channel_name})
        
    except Exception as e:
        log.error(f"创建并启动设备失败: {e}")
        return BaseResponse(code=500, message=f"创建并启动设备失败: {e}")


@channel_router.post("/restart/{channel_id}", response_model=BaseResponse)
async def restart_device(channel_id: int, request: Request):
    """重启设备（用于配置更新后）"""
    try:
        device_controller = request.app.state.device_controller
        new_device = await _reload_device_instance(device_controller, channel_id, is_start=True)
        device_name = new_device.name
        
        return BaseResponse(message=f"设备 {device_name} 重启成功", data={"device_name": device_name})
        
    except Exception as e:
        log.error(f"重启设备失败: {e}")
        return BaseResponse(code=500, message=f"重启设备失败: {e}")


@channel_router.post("/reload_config/{channel_id}", response_model=BaseResponse)
async def reload_device_config(channel_id: int, request: Request):
    """重新加载设备配置（不自动启动服务）"""
    try:
        device_controller = request.app.state.device_controller
        new_device = await _reload_device_instance(device_controller, channel_id, is_start=False)
        device_name = new_device.name
        
        return BaseResponse(message=f"设备 {device_name} 配置已重新加载", data={"device_name": device_name})
        
    except Exception as e:
        log.error(f"重新加载设备配置失败: {e}")
        return BaseResponse(code=500, message=f"重新加载设备配置失败: {e}")


@channel_router.delete("/{channel_id}", response_model=BaseResponse)
async def delete_channel(channel_id: int, request: Request):
    """删除通道"""
    try:
        # 先从设备控制器中移除设备（使用 ID 查找，确保健壮性）
        device_controller = request.app.state.device_controller
        await device_controller.remove_device_by_id(channel_id)
        
        # 删除通道记录
        success = ChannelService.delete_channel(channel_id)
        
        if success:
            return BaseResponse(message="删除通道成功", data=True)
        else:
            return BaseResponse(code=404, message="通道不存在", data=False)
            
    except Exception as e:
        log.error(f"删除通道失败: {e}")
        return BaseResponse(code=500, message=f"删除通道失败: {e}", data=False)


@channel_router.get("/list", response_model=BaseResponse)
async def get_channel_list():
    """获取所有通道列表"""
    try:
        channels = ChannelService.get_all_channels()
        return BaseResponse(message="获取通道列表成功", data=channels)
    except Exception as e:
        log.error(f"获取通道列表失败: {e}")
        return BaseResponse(code=500, message=f"获取通道列表失败: {e}", data=[])


@channel_router.get("/iec61850_structure/{channel_id}", response_model=BaseResponse)
async def get_iec61850_structure(channel_id: int, request: Request):
    """获取 IEC61850 设备的子节点结构树 (GOOSE/Reports/SettingGroups/Files/DataSets/Data Model)"""
    try:
        channel = ChannelService.get_channel_by_id(channel_id)
        if not channel:
            return BaseResponse(code=404, message="通道不存在", data={})

        protocol_type = channel.get("protocol_type", -1)
        if protocol_type != 4:  # ProtocolType.Iec61850
            return BaseResponse(code=400, message="该通道不是 IEC61850 协议", data={})

        device_controller = request.app.state.device_controller
        device = device_controller.get_device_by_channel_id(channel_id)

        if not device:
            return BaseResponse(code=404, message="设备未找到，请确认设备已创建", data={})

        logical_devices = []

        # 通过 protocol_handler 访问底层客户端/服务端
        protocol_handler = getattr(device, 'protocol_handler', None)
        if protocol_handler:
            # IEC 61850 客户端: 通过 _client 属性浏览远端逻辑设备
            if hasattr(protocol_handler, '_client') and protocol_handler._client:
                client = protocol_handler._client
                if hasattr(client, 'browse_logical_devices'):
                    logical_devices = client.browse_logical_devices()
            # IEC 61850 服务端: 从内部模型获取逻辑设备列表
            elif hasattr(protocol_handler, '_server') and protocol_handler._server:
                server = protocol_handler._server
                ld_map = getattr(server, '_ld_map', {})
                ld_name = getattr(server, 'ld_name', '')
                # 合并 ld_map 中的 key 和默认 ld_name
                ld_set = set(ld_map.keys())
                if ld_name:
                    ld_set.add(ld_name)
                logical_devices = sorted(ld_set) if ld_set else [ld_name or "GenericLD"]

        structure = {
            "GOOSE": [],
            "Reports": [],
            "SettingGroups": [],
            "Files": [],
            "DataSets": [],
            "Data Model": logical_devices
        }

        return BaseResponse(message="获取 IEC61850 结构成功", data=structure)
    except Exception as e:
        log.error(f"获取 IEC61850 结构失败: {e}")
        return BaseResponse(code=500, message=f"获取 IEC61850 结构失败: {e}", data={})


@channel_router.get("/iec61850_table_data/{channel_id}", response_model=BaseResponse)
async def get_iec61850_table_data(
    channel_id: int,
    request: Request,
    category: str = "",
    item: str = "",
    point_name: str | None = None,
    page_index: int = 1,
    page_size: int = 10,
    point_types: str = "",
):
    """根据 IEC61850 左侧树形节点获取当前表格数据

    Args:
        channel_id: 通道ID
        category: 树节点分类 (GOOSE/Reports/SettingGroups/Files/DataSets/Data Model)
        item: 分类下的具体项 (如逻辑设备名 "GenericLD")
        point_name: 测点名称搜索
        page_index: 页码
        page_size: 每页大小
        point_types: 测点类型列表，逗号分隔 (如 "0,1,2,3")
    """
    try:
        channel = ChannelService.get_channel_by_id(channel_id)
        if not channel:
            return BaseResponse(code=404, message="通道不存在", data={})

        protocol_type = channel.get("protocol_type", -1)
        if protocol_type != 4:
            return BaseResponse(code=400, message="该通道不是 IEC61850 协议", data={})

        device_controller = request.app.state.device_controller
        device = device_controller.get_device_by_channel_id(channel_id)

        if not device:
            return BaseResponse(code=404, message="设备未找到", data={})

        # 解析 point_types
        pt_filter = []
        if point_types:
            try:
                pt_filter = [int(t.strip()) for t in point_types.split(",") if t.strip().isdigit()]
            except Exception:
                pt_filter = []
        if not pt_filter:
            pt_filter = [0, 1, 2, 3]

        from src.enums.modbus_def import ProtocolType

        # 根据分类和项过滤测点
        # IEC 61850 测点的 address 格式:
        #   完整引用: "MEAS/M0GGIO1.AnIn1.mag.f"  -> LD部分为 "MEAS"
        #   简单模式: "GenericLD/MMXU1.MV_1.mag.f" -> LD部分为 "GenericLD"
        # 通过树节点选择来过滤

        # 获取所有从机的测点并按 category/item 过滤
        head_data = device.get_table_head()
        all_table_rows = []
        total_count = 0

        for slave_id in device.slave_id_list:
            # 获取该从机的全部测点表格数据（不分页）
            table_data, _ = device.get_table_data(
                slave_id=slave_id,
                name=point_name,
                page_index=None,
                page_size=None,
                point_types=pt_filter,
            )
            all_table_rows.extend(table_data)

        # 根据 category + item 过滤行
        # 表头: [地址, 16进制地址, 位, 功能码, 解析码, 测点名称, 测点编码, 寄存器值, 真实值, 乘法系数, 加法系数, 帧类型, 状态]
        # 地址列 (index 0) 存储的是测点的 address 属性，IEC61850 下为完整引用路径
        filtered_rows = _filter_iec61850_rows(all_table_rows, category, item)

        total_count = len(filtered_rows)

        # 分页
        start = (page_index - 1) * page_size
        end = start + page_size
        paged_rows = filtered_rows[start:end]

        data_dict = {
            "total": total_count,
            "head_data": head_data,
            "table_data": paged_rows,
            "category": category,
            "item": item,
        }
        return BaseResponse(message="获取 IEC61850 表格数据成功", data=data_dict)

    except Exception as e:
        log.error(f"获取 IEC61850 表格数据失败: {e}")
        return BaseResponse(code=500, message=f"获取 IEC61850 表格数据失败: {e}", data={})


@channel_router.post("/iec61850_read_points/{channel_id}", response_model=BaseResponse)
async def iec61850_read_points(
    channel_id: int,
    request: Request,
    category: str = "",
    item: str = "",
    interval_ms: int = 0,
):
    """根据 IEC61850 左侧树形节点过滤，批量读取对应测点的值

    Args:
        channel_id: 通道ID
        category: 树节点分类 (GOOSE/Reports/SettingGroups/Files/DataSets/Data Model)
        item: 分类下的具体项 (如逻辑设备名 "GenericLD")
        interval_ms: 逐点读取间隔(毫秒)
    """
    try:
        channel = ChannelService.get_channel_by_id(channel_id)
        if not channel:
            return BaseResponse(code=404, message="通道不存在", data={})

        protocol_type = channel.get("protocol_type", -1)
        if protocol_type != 4:
            return BaseResponse(code=400, message="该通道不是 IEC61850 协议", data={})

        device_controller = request.app.state.device_controller
        device = device_controller.get_device_by_channel_id(channel_id)

        if not device:
            return BaseResponse(code=404, message="设备未找到", data={})

        # 获取所有从机的测点，按 category/item 过滤
        filtered_points = _get_iec61850_filtered_points(device, category, item)

        if not filtered_points:
            return BaseResponse(message="无匹配测点", data={"success": 0, "fail": 0})

        # 按类型分组：遥测(Yc)和遥信(Yx)可读，遥控(Yk)和遥调(Yt)可写
        from src.enums.point_data import Yc, Yx
        yc_list = [p for p in filtered_points if isinstance(p, Yc)]
        yx_list = [p for p in filtered_points if isinstance(p, Yx)]

        success_count = 0
        fail_count = 0

        # 逐点读取（IEC61850 不支持 Modbus 那样的寄存器批量读取）
        for point in yc_list + yx_list:
            try:
                import asyncio
                if interval_ms > 0:
                    await asyncio.sleep(interval_ms / 1000.0)

                if hasattr(device.protocol_handler, 'read_value_async'):
                    value = await device.protocol_handler.read_value_async(point)
                else:
                    value = device.protocol_handler.read_value(point)

                if value is not None:
                    from src.enums.points.change_tracker import ChangeSource, track_change
                    source = ChangeSource.CLIENT_READ if hasattr(device.protocol_handler, '_client') else ChangeSource.INTERNAL
                    with track_change(source, f"IEC61850批量读取 {point.code}"):
                        point.value = value
                    point.is_valid = True
                    success_count += 1
                else:
                    point.is_valid = False
                    fail_count += 1
            except Exception as e:
                device.log.error(f"读取测点 {point.code} 失败: {e}")
                point.is_valid = False
                fail_count += 1

        return BaseResponse(
            message="IEC61850 读取完成",
            data={"success": success_count, "fail": fail_count},
        )

    except Exception as e:
        log.error(f"IEC61850 读取测点失败: {e}")
        return BaseResponse(code=500, message=f"IEC61850 读取测点失败: {e}", data={})


def _get_iec61850_filtered_points(device, category: str, item: str) -> list:
    """根据 IEC61850 树节点的 category 和 item 获取过滤后的测点对象列表

    Returns:
        List[BasePoint]: 过滤后的测点列表
    """
    from src.enums.point_data import Yc, Yx, Yk, Yt

    all_points = []
    pm = device.point_manager
    for slave_id in device.slave_id_list:
        yc_list = pm.yc_dict.get(slave_id, [])
        yx_list = pm.yx_dict.get(slave_id, [])
        yk_list = pm.yk_dict.get(slave_id, [])
        yt_list = pm.yt_dict.get(slave_id, [])
        all_points.extend(yc_list + yx_list + yk_list + yt_list)

    if not category:
        return all_points

    if category == "Data Model" and item:
        # 按逻辑设备名过滤: address 属性以 "LD名/" 开头
        result = []
        for point in all_points:
            address = str(point.address) if hasattr(point, 'address') else ""
            if address.startswith(f"{item}/"):
                result.append(point)
        return result

    # 其他 category 暂时不过滤
    return all_points


def _filter_iec61850_rows(
    rows: list, category: str, item: str
) -> list:
    """根据 IEC61850 树节点的 category 和 item 过滤表格行

    过滤逻辑:
    - category 为空: 返回所有行（不筛选）
    - category="Data Model" + item: 按逻辑设备过滤 (address 以 "{item}/" 开头)
    - category="Data Model" 无 item: 返回所有 Data Model 下的行
    - 其他 category: 暂无专用过滤，返回所有行（可扩展）
    """
    if not category:
        return rows

    if category == "Data Model" and item:
        # 按逻辑设备名过滤: address 列 (index 0) 以 "LD名/" 开头
        # 完整引用路径模式: "MEAS/M0GGIO1.AnIn1.mag.f" -> LD = "MEAS"
        # 简单地址模式: "GenericLD/MMXU1.MV_1.mag.f" -> LD = "GenericLD"
        result = []
        for row in rows:
            address = str(row[0]) if row else ""
            if address.startswith(f"{item}/"):
                result.append(row)
        return result

    # 其他 category 暂时不过滤，返回全部
    # 未来可扩展：GOOSE/Reports/SettingGroups/Files/DataSets 各自对应特定 LN 类
    return rows


@channel_router.get("/{channel_id}", response_model=BaseResponse)
async def get_channel_by_id(channel_id: int):
    """获取单个通道详情"""
    try:
        channel = ChannelService.get_channel_by_id(channel_id)
        if channel:
            return BaseResponse(message="获取通道详情成功", data=channel)
        else:
            return BaseResponse(code=404, message="通道不存在")
    except Exception as e:
        log.error(f"获取通道详情失败: {e}")
        return BaseResponse(code=500, message=f"获取通道详情失败: {e}")


@channel_router.put("/{channel_id}", response_model=BaseResponse)
async def update_channel(channel_id: int, req: ChannelUpdateRequest, request: Request):
    """更新通道配置"""
    try:
        # 检查通道是否存在
        existing = ChannelService.get_channel_by_id(channel_id)
        if not existing:
            return BaseResponse(code=404, message="通道不存在")
        
        # 确定最终的 protocol_type
        protocol_to_use = req.protocol_type if req.protocol_type is not None else existing.get("protocol_type", 1)
        
        # 更新通道
        success = ChannelService.update_channel(
            channel_id=channel_id,
            name=req.name,
            protocol_type=req.protocol_type,
            conn_type=req.conn_type,
            ip=req.ip,
            port=req.port,
            com_port=req.com_port,
            baud_rate=req.baud_rate,
            data_bits=req.data_bits, stop_bits=req.stop_bits,
            parity=req.parity,
            rtu_addr=req.rtu_addr if protocol_to_use == 3 else "1",
        )
        
        if success:
            # ==========================================
            # 新增：更新成功后立即重载设备配置（不自动启动）
            # ==========================================
            try:
                device_controller = request.app.state.device_controller
                await _reload_device_instance(device_controller, channel_id, is_start=False)

            except Exception as e:
                log.error(f"更新配置后重载设备失败: {e}")
                # 不阻拦返回成功

            return BaseResponse(message="更新通道成功", data=True)
        else:
            return BaseResponse(code=500, message="更新通道失败", data=False)
            
    except Exception as e:
        log.error(f"更新通道失败: {e}")
        return BaseResponse(code=500, message=f"更新通道失败: {e}", data=False)


async def _reload_device_instance(device_controller, channel_id: int, is_start: bool = True):
    """
    内部辅助函数：重载/重启设备实例
    """
    # 1. 停止并移除旧设备
    await device_controller.remove_device_by_id(channel_id)
    
    # 2. 获取最新配置
    channel = ChannelService.get_channel_by_id(channel_id)
    if not channel:
        raise ValueError(f"通道 {channel_id} 不存在")
        
    device_name = channel["name"]
    channel_code = channel["code"]
    channel_protocol_type = ChannelService.get_protocol_type(channel)
    port = channel.get("port", Config.DEFAULT_PORT)
    ip = channel.get("ip", Config.DEFAULT_IP)
    
    # 3. 构建器
    # 引入必要的类 (避免循环导入，这里局部引入即可，或顶部引入)
    from src.device.factory.general_device_builder import GeneralDeviceBuilder
    from src.device.types.general_device import GeneralDevice
    from src.device.types.pcs import Pcs
    from src.device.types.circuit_breaker import CircuitBreaker
    
    if channel_code.upper().find("PCS") != -1:
        builder = GeneralDeviceBuilder(channel_id=channel_id, device=Pcs())
    elif channel_code.upper().find("BREAKER") != -1:
        builder = GeneralDeviceBuilder(channel_id=channel_id, device=CircuitBreaker())
    else:
        builder = GeneralDeviceBuilder(channel_id=channel_id, device=GeneralDevice())
    
    # 4. 通信配置
    conn_type = channel.get("conn_type", 1)
    
    log.info(f"Preparing to reload device {device_name}. Protocol: {channel_protocol_type}, ConnType: {conn_type}, IP: {ip}, Port: {port}")
    
    if conn_type in [0, 3]:  # 串口
        builder.setDeviceSerialConfig(
            serial_port=channel.get("com_port", ""),
            baudrate=channel.get("baud_rate", 9600),
            databits=channel.get("data_bits", 8),
            stopbits=channel.get("stop_bits", 1),
            parity=channel.get("parity", "E")
        )
    elif (
        channel_protocol_type == ProtocolType.Iec104Client
        or channel_protocol_type == ProtocolType.ModbusTcpClient
        or channel_protocol_type == ProtocolType.Dlt645Client
        or channel_protocol_type == ProtocolType.Iec61850Client
    ):
        log.info(f"Setting client net config: IP={ip}, Port={port}")
        builder.setDeviceNetConfig(port=port, ip=ip)
    else:
        log.info(f"Setting server net config: IP={Config.DEFAULT_IP}, Port={port}")
        builder.setDeviceNetConfig(port=port, ip=Config.DEFAULT_IP)
    
    # 5. 创建设备
    new_device = builder.makeGeneralDevice(
        device_id=channel_id,
        device_name=device_name,
        protocol_type=channel_protocol_type,
        is_start=is_start, 
    )
    new_device.name = device_name
    
    # 仅客户端设备启动数据更新线程
    is_client = channel_protocol_type in [
        ProtocolType.ModbusTcpClient,
        ProtocolType.Iec104Client,
        ProtocolType.Dlt645Client,
        ProtocolType.Iec61850Client,
    ]
    if is_start and is_client:
        new_device.data_update_thread.start()
    
    device_controller.device_list.append(new_device)
    device_controller.device_map[new_device.name] = new_device
    
    log.info(f"设备 {device_name} 实例已更新 (启动状态: {is_start})")

    return new_device


def _increment_ip(ip: str, offset: int) -> str:
    """递增IP地址的最后一个段"""
    if offset <= 0:
        return ip
    try:
        parts = ip.split('.')
        if len(parts) != 4:
            return ip
        last_octet = int(parts[3])
        new_last_octet = last_octet + offset
        if new_last_octet > 255:
            new_last_octet = new_last_octet % 256
        parts[3] = str(new_last_octet)
        return '.'.join(parts)
    except Exception:
        return ip


@channel_router.post("/copy", response_model=BaseResponse)
async def copy_device(req: CopyDeviceRequest, request: Request):
    """复制设备（包括点表）"""
    try:
        from src.data.service.device_service import DeviceService
        from src.data.dao.point_dao import PointDao

        source_channel = ChannelService.get_channel_by_id(req.channel_id)
        if not source_channel:
            return BaseResponse(code=404, message="源通道不存在")

        source_device_id = source_channel.get("device_id")
        source_device = DeviceService.get_device_by_id(source_device_id) if source_device_id else None
        source_group_id = source_device.get("group_id") if source_device else None

        source_points = PointDao.get_points_by_channel(req.channel_id)

        source_ip = source_channel.get("ip", Config.DEFAULT_IP)
        source_port = source_channel.get("port", Config.DEFAULT_PORT)

        prefix = req.prefix or ""
        suffix = req.suffix or ""

        copied_channels = []

        for i in range(1, req.count + 1):
            ip_offset = req.ip_start_offset + i - 1
            new_ip = _increment_ip(source_ip, ip_offset)

            new_port = source_port + req.port_offset * i if req.port_offset > 0 else source_port

            new_code = f"{prefix}{source_channel['code']}{suffix}{i}"
            new_name = f"{prefix}{source_channel['name']}{suffix}{i}"

            existing = ChannelService.get_channel_by_code(new_code)
            if existing:
                log.warning(f"通道编码 {new_code} 已存在，跳过")
                continue

            new_device_id = DeviceService.create_device(
                code=new_code,
                name=new_name,
                device_type=0,
                group_id=source_group_id,
            )

            if new_device_id <= 0:
                log.error(f"创建设备记录失败: {new_code}")
                continue

            new_channel_id = ChannelService.create_channel(
                code=new_code,
                name=new_name,
                device_id=new_device_id,
                protocol_type=source_channel.get("protocol_type", 1),
                conn_type=source_channel.get("conn_type", 2),
                ip=new_ip,
                port=new_port,
                com_port=source_channel.get("com_port"),
                baud_rate=source_channel.get("baud_rate", 9600),
                data_bits=source_channel.get("data_bits", 8),
                stop_bits=source_channel.get("stop_bits", 1),
                parity=source_channel.get("parity", "N"),
                rtu_addr=source_channel.get("rtu_addr", "1"),
            )

            if new_channel_id <= 0:
                log.error(f"创建通道失败: {new_code}")
                continue

            point_suffix = f"{suffix}{i}"

            for point in source_points:
                point_copy = {
                    "code": f"{prefix}{point['code']}{point_suffix}",
                    "name": point["name"],
                    "rtu_addr": point.get("rtu_addr", 1),
                    "reg_addr": point.get("reg_addr", "0"),
                    "func_code": point.get("func_code", 3),
                    "decode_code": point.get("decode_code", "0x41"),
                }

                frame_type = point.get("frame_type", 0)
                if frame_type in [0, 3]:
                    point_copy["mul_coe"] = point.get("mul_coe", 1.0)
                    point_copy["add_coe"] = point.get("add_coe", 0.0)
                    point_copy["max_limit"] = point.get("max_limit")
                    point_copy["min_limit"] = point.get("min_limit")
                if frame_type in [1, 2]:
                    point_copy["bit"] = point.get("bit")

                try:
                    PointDao.create_point(new_channel_id, frame_type, point_copy)
                except Exception as e:
                    log.error(f"复制测点失败: {point.get('code')} -> {point_copy['code']}: {e}")

            try:
                device_controller = request.app.state.device_controller

                if new_code.upper().find("PCS") != -1:
                    builder = GeneralDeviceBuilder(channel_id=new_channel_id, device=Pcs())
                elif new_code.upper().find("BREAKER") != -1:
                    builder = GeneralDeviceBuilder(channel_id=new_channel_id, device=CircuitBreaker())
                else:
                    builder = GeneralDeviceBuilder(channel_id=new_channel_id, device=GeneralDevice())

                channel_protocol_type = ChannelService.get_protocol_type(source_channel)
                conn_type = source_channel.get("conn_type", 1)

                if conn_type in [0, 3]:
                    builder.setDeviceSerialConfig(
                        serial_port=source_channel.get("com_port", ""),
                        baudrate=source_channel.get("baud_rate", 9600),
                        databits=source_channel.get("data_bits", 8),
                        stopbits=source_channel.get("stop_bits", 1),
                        parity=source_channel.get("parity", "E")
                    )
                elif channel_protocol_type in [ProtocolType.Iec104Client, ProtocolType.ModbusTcpClient,
                                               ProtocolType.Dlt645Client, ProtocolType.Iec61850Client]:
                    builder.setDeviceNetConfig(port=new_port, ip=new_ip)
                else:
                    builder.setDeviceNetConfig(port=new_port, ip=Config.DEFAULT_IP)

                new_device = builder.makeGeneralDevice(
                    device_id=new_channel_id,
                    device_name=new_name,
                    protocol_type=channel_protocol_type,
                    is_start=False,
                )
                new_device.name = new_name

                device_controller.device_list.append(new_device)
                device_controller.device_map[new_device.name] = new_device

                log.info(f"复制设备 {new_name} (ID: {new_channel_id}) 已在内存中创建")

            except Exception as e:
                log.error(f"内存同步复制设备失败: {e}")

            copied_channels.append({
                "channel_id": new_channel_id,
                "device_id": new_device_id,
                "name": new_name,
                "code": new_code,
                "ip": new_ip,
                "port": new_port,
            })

        return BaseResponse(
            message=f"成功复制 {len(copied_channels)} 个设备",
            data={
                "copied_count": len(copied_channels),
                "devices": copied_channels,
            }
        )

    except Exception as e:
        log.error(f"复制设备失败: {e}")
        return BaseResponse(code=500, message=f"复制设备失败: {e}")

