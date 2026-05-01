"""通道管理 - IEC 61850 相关路由"""

from fastapi import APIRouter, Request

from src.data.service.channel_service import ChannelService
from src.enums.modbus_def import ProtocolType
from src.enums.points.base_point import BasePoint
from src.web.api.schemas import BaseResponse
from src.web.log import log

router = APIRouter(tags=["channel"])


@router.get("/iec61850-structure/{channel_id}", response_model=BaseResponse)
async def get_iec61850_structure(channel_id: int, request: Request):
    """获取 IEC61850 设备的子节点结构树"""
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
            return BaseResponse(code=404, message="设备未找到，请确认设备已创建", data={})

        logical_devices = []
        protocol_handler = getattr(device, 'protocol_handler', None)
        if protocol_handler:
            if hasattr(protocol_handler, '_client') and protocol_handler._client:
                client = protocol_handler._client
                if hasattr(client, 'browse_logical_devices'):
                    logical_devices = client.browse_logical_devices()
            elif hasattr(protocol_handler, '_server') and protocol_handler._server:
                server = protocol_handler._server
                point_refs = getattr(server, '_point_refs', {})
                model_name = getattr(server, 'model_name', '')
                ld_set = set()
                for ref in point_refs.values():
                    ref_str = str(ref)
                    if ref_str.startswith(model_name):
                        rest = ref_str[len(model_name):]
                        ld_part = rest.split('/')[0]
                        if ld_part:
                            ld_set.add(ld_part)
                logical_devices = sorted(ld_set)

        structure = {
            "GOOSE": [], "Reports": [], "SettingGroups": [],
            "Files": [], "DataSets": [], "Data Model": logical_devices,
        }
        return BaseResponse(message="获取 IEC61850 结构成功", data=structure)
    except Exception as e:
        log.error(f"获取 IEC61850 结构失败: {e}")
        return BaseResponse(code=500, message=f"获取 IEC61850 结构失败: {e}", data={})


@router.get("/iec61850-table-data/{channel_id}", response_model=BaseResponse)
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
    """根据 IEC61850 左侧树形节点获取当前表格数据"""
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

        pt_filter = []
        if point_types:
            try:
                pt_filter = [int(t.strip()) for t in point_types.split(",") if t.strip().isdigit()]
            except Exception:
                pt_filter = []
        if not pt_filter:
            pt_filter = [0, 1, 2, 3]

        head_data = device.get_table_head()
        all_table_rows = []

        for slave_id in device.slave_id_list:
            table_data, _ = device.get_table_data(
                slave_id=slave_id, name=point_name,
                page_index=None, page_size=None, point_types=pt_filter,
            )
            all_table_rows.extend(table_data)

        filtered_rows = _filter_iec61850_rows(all_table_rows, category, item)
        total_count = len(filtered_rows)

        start = (page_index - 1) * page_size
        end = start + page_size
        paged_rows = filtered_rows[start:end]

        data_dict = {
            "total": total_count, "head_data": head_data,
            "table_data": paged_rows, "category": category, "item": item,
        }
        return BaseResponse(message="获取 IEC61850 表格数据成功", data=data_dict)
    except Exception as e:
        log.error(f"获取 IEC61850 表格数据失败: {e}")
        return BaseResponse(code=500, message=f"获取 IEC61850 表格数据失败: {e}", data={})


@router.post("/iec61850-read-points/{channel_id}", response_model=BaseResponse)
async def iec61850_read_points(
    channel_id: int,
    request: Request,
    category: str = "",
    item: str = "",
    interval_ms: int = 0,
):
    """根据 IEC61850 左侧树形节点过滤，批量读取对应测点的值"""
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

        filtered_points = _get_iec61850_filtered_points(device, category, item)
        if not filtered_points:
            return BaseResponse(message="无匹配测点", data={"success": 0, "fail": 0})

        from src.enums.point_data import Yc, Yx
        yc_list = [p for p in filtered_points if isinstance(p, Yc)]
        yx_list = [p for p in filtered_points if isinstance(p, Yx)]

        success_count = 0
        fail_count = 0

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

        return BaseResponse(message="IEC61850 读取完成", data={"success": success_count, "fail": fail_count})
    except Exception as e:
        log.error(f"IEC61850 读取测点失败: {e}")
        return BaseResponse(code=500, message=f"IEC61850 读取测点失败: {e}", data={})


def _get_iec61850_filtered_points(device, category: str, item: str) -> list[BasePoint]:
    """根据 IEC61850 树节点的 category 和 item 获取过滤后的测点对象列表"""
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
        result = []
        for point in all_points:
            address = str(point.address) if hasattr(point, 'address') else ""
            if address.startswith(f"{item}/"):
                result.append(point)
        return result

    return all_points


def _filter_iec61850_rows(rows: list[str], category: str, item: str) -> list[str]:
    """根据 IEC61850 树节点的 category 和 item 过滤表格行"""
    if not category:
        return rows

    if category == "Data Model" and item:
        result = []
        for row in rows:
            address = str(row[0]) if row else ""
            if address.startswith(f"{item}/"):
                result.append(row)
        return result

    return rows
