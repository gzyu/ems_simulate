"""通道管理 - 点表导入路由

ICD/SCD/CID 文件统一导入:
- MMS 测点 (遥测/遥信/遥控/遥调)
- GOOSE 配置 (Publisher/Subscriber)
"""

import os
import tempfile
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Request, File, UploadFile, Form

from src.data.service.channel_service import ChannelService
from src.tools.excel_point_importer import ExcelPointImporter
from src.enums.modbus_def import ProtocolType
from src.web.api.schemas import BaseResponse
from src.web.api.channel.helpers import reload_device_instance
from src.web.log import log

router = APIRouter(tags=["channel"])


@router.post("/import-points", response_model=BaseResponse)
async def import_points(
    request: Request,
    channel_id: int = Form(...),
    file: UploadFile = File(...),
):
    """导入 Excel 点表"""
    try:
        if not file.filename.endswith(('.xlsx', '.xls')):
            return BaseResponse(code=400, message="请上传 Excel 文件 (.xlsx 或 .xls)")

        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            from src.data.dao.point_dao import PointDao
            deleted_count = PointDao.delete_points_by_channel(channel_id)
            if deleted_count > 0:
                log.info(f"重新导入前已删除 {deleted_count} 个旧测点")

            importer = ExcelPointImporter(channel_id=channel_id)
            yc_count, yx_count, yk_count, yt_count = importer.import_from_excel(tmp_path)

            try:
                device_controller = request.app.state.device_controller
                device = device_controller.get_device_by_id(channel_id)
                if device:
                    if device.protocol_type == ProtocolType.Iec61850Server:
                        was_running = device.is_protocol_running()
                        await reload_device_instance(device_controller, channel_id, is_start=was_running)
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
                    "yc_count": yc_count, "yx_count": yx_count,
                    "yk_count": yk_count, "yt_count": yt_count,
                    "total": yc_count + yx_count + yk_count + yt_count,
                },
            )
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    except Exception as e:
        log.error(f"导入点表失败: {e}")
        return BaseResponse(code=500, message=f"导入点表失败: {e}")


@router.post("/preview-icd", response_model=BaseResponse)
async def preview_icd(
    request: Request,
    file: UploadFile = File(...),
    interface: str = Form("eth0"),
):
    """预览 ICD/SCD/CID 文件（只解析不保存，返回 MMS 测点数量和 GOOSE 配置）"""
    try:
        valid_extensions = ('.icd', '.scd', '.cid', '.xml')
        if not file.filename.lower().endswith(valid_extensions):
            return BaseResponse(code=400, message=f"请上传 ICD 文件 ({', '.join(valid_extensions)})")

        suffix = os.path.splitext(file.filename)[1] or '.icd'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            # ===== 1. MMS 测点预览（只计数不保存） =====
            from src.tools.icd_point_importer import IcdPointImporter
            importer = IcdPointImporter(channel_id=0)  # preview 不需要 channel_id
            yc_count, yx_count, yk_count, yt_count = importer.preview_from_icd(tmp_path)

            # ===== 2. GOOSE 配置预览 =====
            goose_data: Dict[str, Any] = {}
            goose_errors: List[str] = []
            try:
                from src.tools.icd_goose_importer import import_goose_from_icd
                goose_result = import_goose_from_icd(tmp_path, interface=interface)
                goose_data = goose_result
            except Exception as e:
                log.warning(f"预览 ICD GOOSE 配置失败 (不影响 MMS 预览): {e}")
                goose_errors.append(f"GOOSE 解析失败: {e}")

            return BaseResponse(
                message="ICD 文件预览成功",
                data={
                    "yc_count": yc_count, "yx_count": yx_count,
                    "yk_count": yk_count, "yt_count": yt_count,
                    "total": yc_count + yx_count + yk_count + yt_count,
                    "goose": {
                        "summary": goose_data.get("summary", {"gse_control_count": 0, "gse_controls": []}),
                        "publishers": goose_data.get("publishers", []),
                        "subscriptions": goose_data.get("subscriptions", []),
                        "errors": goose_errors,
                    } if goose_data else None,
                },
            )
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    except Exception as e:
        log.error(f"预览 ICD 文件失败: {e}")
        return BaseResponse(code=500, message=f"预览 ICD 文件失败: {e}")


@router.post("/import-icd", response_model=BaseResponse)
async def import_icd(
    request: Request,
    channel_id: int = Form(...),
    file: UploadFile = File(...),
    interface: str = Form("eth0"),
    auto_create_goose: bool = Form(False),
):
    """导入 IEC 61850 ICD/SCD/CID 文件

    同时解析:
    - MMS 测点 (遥测/遥信/遥控/遥调) → 写入数据库
    - GOOSE 配置 (GSEControl/DataSet/GSE) → 返回给前端，可选自动创建 Publisher
    """
    try:
        valid_extensions = ('.icd', '.scd', '.cid', '.xml')
        if not file.filename.lower().endswith(valid_extensions):
            return BaseResponse(code=400, message=f"请上传 ICD 文件 ({', '.join(valid_extensions)})")

        suffix = os.path.splitext(file.filename)[1] or '.icd'
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        try:
            # ===== 1. MMS 测点导入 =====
            # IcdPointImporter.import_from_icd() 内部会先清除旧测点，此处无需重复删除
            from src.tools.icd_point_importer import IcdPointImporter
            importer = IcdPointImporter(channel_id=channel_id)
            yc_count, yx_count, yk_count, yt_count = importer.import_from_icd(tmp_path)

            # 从 ICD 文件中提取 IED 名称，更新通道配置
            try:
                ied_name = importer.get_ied_name()
                if ied_name:
                    ChannelService.update_channel(channel_id, model_name=ied_name)
                    log.info(f"已从 ICD 文件提取 IED 名称: {ied_name} -> 通道 {channel_id}")
            except Exception as e:
                log.warning(f"提取 IED 名称失败 (不影响测点导入): {e}")

            try:
                device_controller = request.app.state.device_controller
                device = device_controller.get_device_by_id(channel_id)
                if device:
                    if device.protocol_type == ProtocolType.Iec61850Server:
                        was_running = device.is_protocol_running()
                        await reload_device_instance(device_controller, channel_id, is_start=was_running)
                        log.info(f"IEC 61850 服务端设备 {device.name} (ID: {channel_id}) 已重建以加载新点表")
                    else:
                        device.importDataPointFromChannel(channel_id, device.protocol_type)
                        log.info(f"已同步更新设备 {device.name} (ID: {channel_id}) 的内存点表")
                else:
                    log.warning(f"导入ICD后未找到内存设备 (ID: {channel_id})，需要手动加载或重启")
            except Exception as e:
                log.error(f"同步内存点表失败: {e}")

            # ===== 2. GOOSE 配置解析 =====
            goose_data: Dict[str, Any] = {}
            goose_errors: List[str] = []
            created_goose_count = 0

            # 先清除旧的 GOOSE 持久化记录
            try:
                from src.data.dao.goose_publisher_dao import GoosePublisherDao
                old_count = GoosePublisherDao.delete_by_channel(channel_id)
                if old_count > 0:
                    log.info(f"重新导入前已删除 {old_count} 个旧 GOOSE Publisher 持久化记录")
            except Exception as e:
                log.warning(f"清除旧 GOOSE 持久化记录失败: {e}")

            # 获取 IEC 61850 服务器（用于在 MMS 数据模型中注册 GSEControlBlock）
            iec61850_server = None
            try:
                _device = device_controller.get_device_by_id(channel_id)
                if _device and hasattr(_device, 'protocol_handler') and _device.protocol_handler:
                    _handler = _device.protocol_handler
                    if hasattr(_handler, 'server'):
                        iec61850_server = _handler.server
                        log.info("已获取 IEC61850Server，将在 MMS 模型中注册 GSEControlBlock")
            except Exception as e:
                log.warning(f"获取 IEC61850Server 失败: {e}")

            try:
                from src.tools.icd_goose_importer import import_goose_from_icd
                goose_result = import_goose_from_icd(tmp_path, interface=interface)
                goose_data = goose_result

                # 可选：自动创建 GOOSE Publisher
                if auto_create_goose and goose_result.get("publishers"):
                    from src.proto.iec61850.goose_manager import GooseManager
                    manager: Optional[GooseManager] = getattr(
                        request.app.state, "goose_manager", None
                    )
                    if manager:
                        for pub_config in goose_result["publishers"]:
                            try:
                                pub_result = manager.create_publisher(
                                    interface=pub_config["interface"],
                                    go_cb_ref=pub_config["go_cb_ref"],
                                    go_id=pub_config["go_id"],
                                    data_set_ref=pub_config["data_set_ref"],
                                    app_id=pub_config["app_id"],
                                    conf_rev=pub_config["conf_rev"],
                                    time_allowed_to_live=pub_config["time_allowed_to_live"],
                                    dst_mac=pub_config.get("dst_mac"),
                                    vlan_id=pub_config.get("vlan_id", 0),
                                    vlan_prio=pub_config.get("vlan_prio", 4),
                                    simulation=pub_config.get("simulation", True),
                                    entries=pub_config.get("entries", []),
                                    server=iec61850_server,
                                    channel_id=channel_id,  # 持久化到数据库
                                )
                                if pub_result:
                                    created_goose_count += 1
                                else:
                                    goose_errors.append(
                                        f"创建 Publisher 失败: {pub_config['go_cb_ref']}"
                                    )
                            except Exception as e:
                                goose_errors.append(
                                    f"创建 Publisher 异常 ({pub_config['go_cb_ref']}): {e}"
                                )
                    else:
                        goose_errors.append("GOOSE 管理器未初始化，无法自动创建 Publisher")
            except Exception as e:
                log.warning(f"解析 ICD GOOSE 配置失败 (不影响 MMS 导入): {e}")
                goose_errors.append(f"GOOSE 解析失败: {e}")

            return BaseResponse(
                message="导入ICD文件成功",
                data={
                    # MMS 测点
                    "yc_count": yc_count, "yx_count": yx_count,
                    "yk_count": yk_count, "yt_count": yt_count,
                    "total": yc_count + yx_count + yk_count + yt_count,
                    # GOOSE 配置
                    "goose": {
                        "summary": goose_data.get("summary", {"gse_control_count": 0, "gse_controls": []}),
                        "publishers": goose_data.get("publishers", []),
                        "subscriptions": goose_data.get("subscriptions", []),
                        "created_count": created_goose_count,
                        "errors": goose_errors,
                    } if goose_data else None,
                },
            )
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    except Exception as e:
        log.error(f"导入ICD文件失败: {e}")
        return BaseResponse(code=500, message=f"导入ICD文件失败: {e}")
