"""通道管理 - 点表导入路由"""

import os
import tempfile

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


@router.post("/import-icd", response_model=BaseResponse)
async def import_icd(
    request: Request,
    channel_id: int = Form(...),
    file: UploadFile = File(...),
):
    """导入 IEC 61850 ICD/SCD/CID 文件"""
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
            from src.data.dao.point_dao import PointDao
            deleted_count = PointDao.delete_points_by_channel(channel_id)
            if deleted_count > 0:
                log.info(f"重新导入前已删除 {deleted_count} 个旧测点")

            from src.tools.icd_point_importer import IcdPointImporter
            importer = IcdPointImporter(channel_id=channel_id)
            yc_count, yx_count, yk_count, yt_count = importer.import_from_icd(tmp_path)

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

            return BaseResponse(
                message="导入ICD文件成功",
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
        log.error(f"导入ICD文件失败: {e}")
        return BaseResponse(code=500, message=f"导入ICD文件失败: {e}")
