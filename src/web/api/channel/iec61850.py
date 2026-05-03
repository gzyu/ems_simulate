"""通道管理 - IEC 61850 相关路由"""

from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.data.service.channel_service import ChannelService
from src.enums.modbus_def import ProtocolType
from src.enums.points.base_point import BasePoint
from src.web.api.schemas import BaseResponse
from src.web.log import log

router = APIRouter(tags=["channel"])


# ===== IEC61850 POST 请求模型 =====

class Iec61850ReadPointsRequest(BaseModel):
    category: str = Field("", description="IED/LD 分类过滤")
    item: str = Field("", description="LN 实例过滤")
    interval_ms: int = Field(0, description="读取间隔(ms)")


class Iec61850ReadPointRequest(BaseModel):
    point_code: str = Field(..., description="测点编码")


class Iec61850WritePointRequest(BaseModel):
    point_code: str = Field(..., description="测点编码")
    point_value: Union[float, str] = Field(0, description="写入值")


# ===== IEC 61850 树形数据常量 =====

# 已知结构体 DA 的 BDA 子节点
KNOWN_STRUCT_DA_BDAS: Dict[str, List[str]] = {
    "q": ["validity", "source", "operatorBlocked", "test"],
    "t": ["seconds", "fraction"],
    "origin": ["orCat", "orIdent"],
}

# 每个 DO 应包含的标准 DA 列表 (后端自动补全)
STANDARD_DAS_FOR_DO: List[Dict[str, Any]] = [
    {"name": "q", "fc": "MX", "is_struct": True},
    {"name": "t", "fc": "MX", "is_struct": True},
    {"name": "dU", "fc": "DC", "is_struct": False},
]

# DA 名称 → 默认 FC 映射
DA_NAME_FC_MAP: Dict[str, str] = {
    "mag": "MX", "instMag": "MX", "cVal": "MX", "f": "MX",
    "stVal": "ST", "ctlVal": "CO", "setVal": "CO",
    "q": "MX", "t": "MX", "dU": "DC",
    "origin": "OR", "subVal": "SV", "blkEna": "BL", "SBO": "CO",
    "Oper": "CO", "Cancel": "CO",
}


def _parse_iec61850_address(address: str) -> Optional[Dict[str, str]]:
    """解析 IEC 61850 地址, 提取 LD/LN/DO/DA 层级
    
    示例: "LD0/LLN0.Mod.mag.f" → {ld: "LD0", ln: "LLN0", do_name: "Mod", da_path: "mag.f"}
    """
    if not address or "/" not in address:
        return None
    slash_idx = address.index("/")
    rest = address[slash_idx + 1:]
    dot_idx = rest.find(".")
    if dot_idx < 0:
        return None  # 无 DO/DA 结构
    ld = address[:slash_idx]
    ln = rest[:dot_idx]
    da_part = rest[dot_idx + 1:]
    first_dot = da_part.find(".")
    if first_dot >= 0:
        do_name = da_part[:first_dot]
        da_path = da_part[first_dot + 1:]
    else:
        do_name = da_part
        da_path = ""
    return {"ld": ld, "ln": ln, "do_name": do_name, "da_path": da_path}


def _infer_fc_from_da(da_path: str, fallback_fc: str = "MX") -> str:
    """从 DA 路径推断 FC"""
    if not da_path:
        return fallback_fc
    top_da = da_path.split(".")[0]
    return DA_NAME_FC_MAP.get(top_da, fallback_fc)


def _build_iec61850_tree(
    all_points: List[BasePoint],
    category: str = "",
    item: str = "",
    point_name: str | None = None,
    point_types: List[int] | None = None,
) -> Dict[str, Any]:
    """将扁平测点列表构建为 IEC 61850 树形结构
    
    返回结构:
    {
        "items": [
            {
                "do_name": "Mod",
                "do_ref": "LD0/LLN0.Mod",
                "du_name": "模式",           # dU 描述 (如有)
                "fc": "CO",                  # DO 主 FC
                "frame_type": 2,             # 帧类型 (0=遥测, 1=遥信, 2=遥控, 3=遥调)
                "children": [                # DA 列表
                    {
                        "da_name": "ctlVal",
                        "da_path": "ctlVal",
                        "fc": "CO",
                        "is_struct": false,
                        "point_code": "xxx",
                        "point_name": "控制值",
                        "value": "1",
                        "status": "成功",
                        "children": []       # BDA 列表 (如有)
                    },
                    {
                        "da_name": "q",
                        "da_path": "q",
                        "fc": "MX",
                        "is_struct": true,
                        "point_code": "yyy",
                        "point_name": "品质",
                        "value": "0",
                        "status": "成功",
                        "children": [         # BDA 子节点
                            {"bda_name": "validity", "bda_path": "q.validity", "fc": "MX"},
                            {"bda_name": "source", "bda_path": "q.source", "fc": "MX"},
                            ...
                        ]
                    },
                    ...
                ]
            }
        ],
        "total": 5
    }
    """
    from src.enums.point_data import Yc, Yx, Yk, Yt
    from src.device.core.data.data_exporter import DataExporter

    if point_types is None:
        point_types = [0, 1, 2, 3]

    # 1. 收集所有测点, 构建 DO 分组
    do_map: Dict[str, Dict[str, Any]] = {}  # do_ref → {do_info, children_map}

    for point in all_points:
        # 帧类型过滤
        ft = point.frame_type
        if ft not in point_types:
            continue

        # 名称过滤
        if point_name and point_name not in str(point.name):
            continue

        address = str(point.address) if hasattr(point, "address") else ""
        parsed = _parse_iec61850_address(address)
        if not parsed:
            continue

        # category/item 过滤
        if category == "Data Model" and item:
            if not (address.startswith(f"{item}/") or address.startswith(f"{item}.")):
                continue

        ld = parsed["ld"]
        ln = parsed["ln"]
        do_name = parsed["do_name"]
        da_path = parsed["da_path"]
        do_ref = f"{ld}/{ln}.{do_name}"

        # 获取点属性
        point_fc = getattr(point, "fc", "") or ""
        is_valid = getattr(point, "is_valid", None)
        status = "成功" if is_valid is True else ("失败" if is_valid is False else "未知")

        # 获取真实值 (始终返回值，不依赖 is_valid 过滤)
        if point_fc == "DC":
            value = str(point.name)
        elif isinstance(point, (Yc, Yt)):
            value = str(point.real_value) if point.real_value is not None else ""
        elif isinstance(point, (Yx, Yk)):
            value = str(int(point.value)) if point.value is not None else ""
        else:
            value = ""

        # 初始化 DO 分组
        if do_ref not in do_map:
            do_map[do_ref] = {
                "do_name": do_name,
                "do_ref": do_ref,
                "ld": ld,
                "ln": ln,
                "du_name": "",
                "fc": point_fc or _infer_fc_from_da(da_path),
                "frame_type": ft,
                "da_map": {},          # da_path → da_info (实际从后端返回的 DA)
                "da_top_names": set(), # 顶级 DA 名称集合
            }

        do_info = do_map[do_ref]

        # 记录顶级 DA 名称
        if da_path:
            top_da = da_path.split(".")[0]
            do_info["da_top_names"].add(top_da)

        # 判断是 BDA 还是 DA
        if "." in da_path:
            top_da = da_path.split(".")[0]
            bda_name = da_path[len(top_da) + 1:]

            # 确保父 DA 存在于 da_map
            if top_da not in do_info["da_map"]:
                parent_fc = point_fc or _infer_fc_from_da(top_da)
                do_info["da_map"][top_da] = {
                    "da_name": top_da,
                    "da_path": top_da,
                    "fc": parent_fc,
                    "is_struct": True,
                    "point_code": "",
                    "point_name": top_da,
                    "value": "",
                    "status": "",
                    "children": [],
                }

            # 添加 BDA
            parent_da = do_info["da_map"][top_da]
            # 检查是否已有同名 BDA
            existing_bda_names = {b.get("bda_name") for b in parent_da["children"]}
            if bda_name not in existing_bda_names:
                parent_da["children"].append({
                    "bda_name": bda_name,
                    "bda_path": da_path,
                    "fc": point_fc or parent_da["fc"],
                    "point_code": str(point.code),
                    "value": value,
                    "status": status,
                })
        else:
            # 顶级 DA
            if da_path not in do_info["da_map"]:
                is_struct = da_path in KNOWN_STRUCT_DA_BDAS
                do_info["da_map"][da_path] = {
                    "da_name": da_path,
                    "da_path": da_path,
                    "fc": point_fc or _infer_fc_from_da(da_path),
                    "is_struct": is_struct,
                    "point_code": str(point.code),
                    "point_name": str(point.name),
                    "value": value,
                    "status": status,
                    "children": [],
                }
            else:
                # 更新已有 DA 的信息 (如 mag.f 创建了 mag 虚拟行后, 真正的 mag 行又来了)
                existing = do_info["da_map"][da_path]
                if not existing["point_code"]:
                    existing["point_code"] = str(point.code)
                    existing["point_name"] = str(point.name)
                    existing["value"] = value
                    existing["status"] = status
                existing["fc"] = point_fc or existing["fc"]

            # 如果是 dU, 记录描述到 DO
            if da_path == "dU" and value and value not in ("0", "0.0"):
                do_info["du_name"] = value

    # 2. 为每个 DO 补充标准 DA (q, t, dU) 和已知 BDA
    for do_ref, do_info in do_map.items():
        da_map = do_info["da_map"]
        top_names = do_info["da_top_names"]
        main_fc = do_info["fc"]

        # q/t 的 FC 根据主值类型推断 (遥测=MX, 遥信=ST)
        qt_fc = "ST" if main_fc == "ST" else "MX"

        for std_da in STANDARD_DAS_FOR_DO:
            da_name = std_da["name"]
            if da_name in top_names or da_name in da_map:
                continue  # 已存在

            fc = qt_fc if da_name in ("q", "t") else std_da["fc"]
            is_struct = std_da["is_struct"]
            bda_list = KNOWN_STRUCT_DA_BDAS.get(da_name, [])

            da_map[da_name] = {
                "da_name": da_name,
                "da_path": da_name,
                "fc": fc,
                "is_struct": is_struct,
                "point_code": "",
                "point_name": da_name,
                "value": "",
                "status": "",
                "children": [
                    {
                        "bda_name": bda,
                        "bda_path": f"{da_name}.{bda}",
                        "fc": fc,
                        "point_code": "",
                        "value": "",
                        "status": "",
                    }
                    for bda in bda_list
                ],
            }

        # 对已有结构体 DA 补充缺失的 BDA
        for da_name, bda_names in KNOWN_STRUCT_DA_BDAS.items():
            if da_name not in da_map:
                continue
            da_entry = da_map[da_name]
            existing_bda_names = {b.get("bda_name") for b in da_entry.get("children", [])}
            for bda_name in bda_names:
                if bda_name not in existing_bda_names:
                    da_entry["children"].append({
                        "bda_name": bda_name,
                        "bda_path": f"{da_name}.{bda_name}",
                        "fc": da_entry["fc"],
                        "point_code": "",
                        "value": "",
                        "status": "",
                    })

    # 3. 组装最终树形列表 (按 do_ref 排序)
    items = []
    for do_ref in sorted(do_map.keys()):
        do_info = do_map[do_ref]
        # DA 排序: 主值 DA 在前, q/t/dU 在后
        da_list = list(do_info["da_map"].values())
        # 按 DA 名称排序, 主值优先
        priority_order = {"mag": 0, "instMag": 0, "cVal": 0, "stVal": 0, "ctlVal": 0, "setVal": 0,
                          "Oper": 1, "Cancel": 1, "origin": 2, "q": 3, "t": 4, "dU": 5}
        da_list.sort(key=lambda d: priority_order.get(d["da_name"], 2))

        # 聚合 DA 子节点的状态到 DO 根节点
        da_statuses = [d["status"] for d in da_list if d.get("status")]
        # 也聚合 BDA 子节点的状态
        for da in da_list:
            for bda in da.get("children", []):
                if bda.get("status"):
                    da_statuses.append(bda["status"])

        if any(s == "失败" for s in da_statuses):
            do_status = "失败"
        elif all(s == "成功" for s in da_statuses) and da_statuses:
            do_status = "成功"
        else:
            do_status = "未知"

        items.append({
            "do_name": do_info["do_name"],
            "do_ref": do_info["do_ref"],
            "ld": do_info["ld"],
            "ln": do_info["ln"],
            "du_name": do_info["du_name"],
            "fc": do_info["fc"],
            "frame_type": do_info["frame_type"],
            "status": do_status,
            "children": da_list,
        })

    return {"items": items, "total": len(items)}


@router.get("/iec61850-tree-data/{channel_id}", response_model=BaseResponse)
async def get_iec61850_tree_data(
    channel_id: int,
    request: Request,
    category: str = "",
    item: str = "",
    point_name: str | None = None,
    point_types: str = "",
    page_index: int = 1,
    page_size: int = 10,
):
    """获取 IEC61850 树形表格数据 (按 DO→DA→BDA 层级返回，支持 DO 级分页)"""
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

        # 获取所有测点对象
        all_points = _get_iec61850_filtered_points(device, "", "")

        # 构建树形结构
        tree_data = _build_iec61850_tree(
            all_points,
            category=category,
            item=item,
            point_name=point_name,
            point_types=pt_filter,
        )

        # DO 级分页
        all_items = tree_data["items"]
        total = tree_data["total"]
        start = (page_index - 1) * page_size
        end = start + page_size
        paged_items = all_items[start:end]

        return BaseResponse(message="获取 IEC61850 树形数据成功", data={
            "items": paged_items,
            "total": total,
        })
    except Exception as e:
        log.error(f"获取 IEC61850 树形数据失败: {e}")
        return BaseResponse(code=500, message=f"获取 IEC61850 树形数据失败: {e}", data={})


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
                # 获取每个 LD 下的 LN 列表
                data_model = []
                for ld in logical_devices:
                    lns = client.browse_logical_nodes(ld) if hasattr(client, 'browse_logical_nodes') else []
                    data_model.append({"name": ld, "children": lns})
            elif hasattr(protocol_handler, '_server') and protocol_handler._server:
                server = protocol_handler._server
                if hasattr(server, 'browse_logical_devices'):
                    logical_devices = server.browse_logical_devices()
                # 获取每个 LD 下的 LN 列表
                data_model = []
                for ld in logical_devices:
                    lns = server.browse_logical_nodes(ld) if hasattr(server, 'browse_logical_nodes') else []
                    data_model.append({"name": ld, "children": lns})

        structure = {
            "GOOSE": [], "Reports": [], "SettingGroups": [],
            "Files": [], "DataSets": [], "Data Model": data_model,
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
    body: Iec61850ReadPointsRequest,
):
    """根据 IEC61850 左侧树形节点过滤，批量读取对应测点的值

    优化策略: 使用 IEC61850ClientHandler.read_points_batch 按 iec_type 分组读取,
    减少类型判断开销, 连接断开时快速失败。
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

        filtered_points = _get_iec61850_filtered_points(device, body.category, body.item)
        if not filtered_points:
            return BaseResponse(message="无匹配测点", data={"success": 0, "fail": 0})

        from src.enums.point_data import Yc, Yx
        from src.enums.points.change_tracker import ChangeSource, track_change
        from src.device.protocol.iec61850_handler import IEC61850ClientHandler

        yc_list = [p for p in filtered_points if isinstance(p, Yc)]
        yx_list = [p for p in filtered_points if isinstance(p, Yx)]

        # 判断是否为 IEC61850 客户端且支持批量读取
        protocol_handler = device.protocol_handler
        is_iec61850_client = isinstance(protocol_handler, IEC61850ClientHandler)
        has_batch = is_iec61850_client and hasattr(protocol_handler, 'read_points_batch')

        all_points = yc_list + yx_list
        success_count = 0
        fail_count = 0

        source = ChangeSource.CLIENT_READ if has_batch else ChangeSource.INTERNAL

        if has_batch:
            # 批量读取模式: 一次性读取所有测点
            batch_results = protocol_handler.read_points_batch(all_points)

            for point in all_points:
                value = batch_results.get(point.code)
                if value is not None:
                    with track_change(source, f"IEC61850批量读取 {point.code}"):
                        point.value = value
                    point.is_valid = True
                    success_count += 1
                else:
                    point.is_valid = False
                    fail_count += 1
        else:
            # 回退模式: 逐点读取 (服务端或旧版客户端)
            for point in all_points:
                try:
                    import asyncio
                    if body.interval_ms > 0:
                        await asyncio.sleep(body.interval_ms / 1000.0)

                    if hasattr(protocol_handler, 'read_value_async'):
                        value = await protocol_handler.read_value_async(point)
                    else:
                        value = protocol_handler.read_value(point)

                    if value is not None:
                        source = ChangeSource.CLIENT_READ if hasattr(protocol_handler, '_client') else ChangeSource.INTERNAL
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
            # LD 级别过滤: item="GenericLD" → address 以 "GenericLD/" 开头
            # LN 级别过滤: item="GenericLD/MMXU1" → address 以 "GenericLD/MMXU1." 或 "GenericLD/MMXU1/" 开头
            if address.startswith(f"{item}/") or address.startswith(f"{item}."):
                result.append(point)
        return result

    return all_points


@router.get("/iec61850-do-children/{channel_id}", response_model=BaseResponse)
async def get_iec61850_do_children(
    channel_id: int,
    request: Request,
    ld: str = "",
    ln: str = "",
):
    """获取 IEC61850 指定 LN 下的数据对象 (DO) 列表"""
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

        do_items = []
        protocol_handler = getattr(device, 'protocol_handler', None)
        if protocol_handler:
            if hasattr(protocol_handler, '_client') and protocol_handler._client:
                client = protocol_handler._client
                if hasattr(client, 'browse_data_objects'):
                    do_items = client.browse_data_objects(ld, ln)
            elif hasattr(protocol_handler, '_server') and protocol_handler._server:
                server = protocol_handler._server
                if hasattr(server, 'browse_data_objects'):
                    do_items = server.browse_data_objects(ld, ln)

        return BaseResponse(message="获取 DO 列表成功", data={"items": do_items})
    except Exception as e:
        log.error(f"获取 IEC61850 DO 列表失败: {e}")
        return BaseResponse(code=500, message=f"获取 DO 列表失败: {e}", data={})


@router.get("/iec61850-da-children/{channel_id}", response_model=BaseResponse)
async def get_iec61850_da_children(
    channel_id: int,
    request: Request,
    ld: str = "",
    ln: str = "",
    do_name: str = "",
):
    """获取 IEC61850 指定 DO 下的数据属性 (DA) 列表"""
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

        da_items = []
        protocol_handler = getattr(device, 'protocol_handler', None)
        if protocol_handler:
            if hasattr(protocol_handler, '_client') and protocol_handler._client:
                client = protocol_handler._client
                if hasattr(client, 'browse_data_attributes'):
                    da_items = client.browse_data_attributes(ld, ln, do_name)
            elif hasattr(protocol_handler, '_server') and protocol_handler._server:
                server = protocol_handler._server
                if hasattr(server, 'browse_data_attributes'):
                    da_items = server.browse_data_attributes(ld, ln, do_name)

        return BaseResponse(message="获取 DA 列表成功", data={"items": da_items})
    except Exception as e:
        log.error(f"获取 IEC61850 DA 列表失败: {e}")
        return BaseResponse(code=500, message=f"获取 DA 列表失败: {e}", data={})


def _filter_iec61850_rows(rows: list[str], category: str, item: str) -> list[str]:
    """根据 IEC61850 树节点的 category 和 item 过滤表格行"""
    if not category:
        return rows

    if category == "Data Model" and item:
        result = []
        for row in rows:
            address = str(row[0]) if row else ""
            # LD 级别过滤: item="GenericLD" → address 以 "GenericLD/" 开头
            # LN 级别过滤: item="GenericLD/MMXU1" → address 以 "GenericLD/MMXU1." 或 "GenericLD/MMXU1/" 开头
            if address.startswith(f"{item}/") or address.startswith(f"{item}."):
                result.append(row)
        return result

    return rows


@router.post("/iec61850-read-point/{channel_id}", response_model=BaseResponse)
async def iec61850_read_single_point(
    channel_id: int,
    request: Request,
    body: Iec61850ReadPointRequest,
):
    """IEC61850 单点读取 - 通过 channel_id 定位设备，读取指定测点的值"""
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

        if not body.point_code:
            return BaseResponse(code=400, message="测点编码不能为空", data={})

        value = await device.read_single_point_async(body.point_code)
        if value is not None:
            return BaseResponse(message="读取成功", data={"value": value, "point_code": body.point_code})
        else:
            return BaseResponse(code=400, message="读取失败，请检查连接状态", data={"value": None, "point_code": body.point_code})
    except Exception as e:
        log.error(f"IEC61850 单点读取失败: {e}")
        return BaseResponse(code=500, message=f"IEC61850 单点读取失败: {e}", data={})


@router.post("/iec61850-write-point/{channel_id}", response_model=BaseResponse)
async def iec61850_write_single_point(
    channel_id: int,
    request: Request,
    body: Iec61850WritePointRequest,
):
    """IEC61850 单点写入 - 通过 channel_id 定位设备，写入指定测点的值

    服务端设备: 设置本地数据模型的值 (仿真设置)
    客户端设备: 向远程服务器发送控制命令 (仅限遥控/遥调)
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

        if not body.point_code:
            return BaseResponse(code=400, message="测点编码不能为空", data={})

        success = await device.edit_point_data_async(body.point_code, body.point_value)
        if success:
            return BaseResponse(message="写入成功", data={"point_code": body.point_code, "value": body.point_value})
        else:
            return BaseResponse(code=400, message="写入失败", data={"point_code": body.point_code})
    except ValueError as e:
        return BaseResponse(code=400, message=str(e), data={"point_code": body.point_code})
    except Exception as e:
        log.error(f"IEC61850 单点写入失败: {e}")
        return BaseResponse(code=500, message=f"IEC61850 单点写入失败: {e}", data={})
