# src/proxy_handlers/handler_virtual_items.py (新文件)
import logging
import json
from fastapi import Request, Response
from models import AppConfig

from .handler_views import get_vlib_collection_type

logger = logging.getLogger(__name__)

async def handle_get_virtual_item_info(request: Request, full_path: str, config: AppConfig) -> Response | None:
    """
    拦截对虚拟库本身详情的请求 (e.g., /Users/{uid}/Items/{vlib_id})
    并返回一个伪造的、包含正确 ImageTag 的响应。
    """
    # 检查路径格式是否为 /Users/{...}/Items/{...}
    path_parts = full_path.split('/')
    if len(path_parts) != 4 or path_parts[0] != "Users" or path_parts[2] != "Items":
        return None

    vlib_id_from_path = path_parts[3]
    
    # 在配置中查找这个ID对应的虚拟库
    found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == vlib_id_from_path), None)

    # 如果没找到，或者这个虚拟库没有设置 image_tag，则不处理
    if not found_vlib or not found_vlib.image_tag:
        return None

    logger.info(f"✅ VLIB_ITEM_INFO: Intercepting request for virtual library '{found_vlib.name}' info.")

    # 获取 ServerId 以便伪造响应 (从任意一个真实库中获取)
    # 注意: 这需要 config.display_order 至少包含一个真实库ID，如果全是虚拟库可能会出问题
    # 更好的方法是从Emby动态获取，但暂时简化处理
    server_id = "unknown_server_id" # 提供一个默认值

    # 伪造一个看起来像 CollectionFolder 的 JSON 响应
    # CollectionType 与 /Views 磁贴的 stub 保持同一推导值,避免客户端两种入口类型不一致
    fake_item_data = {
        "Name": found_vlib.name,
        "Id": found_vlib.id,
        "ServerId": server_id, # 这里需要一个真实的ServerId
        "Type": "CollectionFolder",
        "CollectionType": get_vlib_collection_type(found_vlib),
        "IsFolder": True,
        "ImageTags": {
            "Primary": found_vlib.image_tag
        },
        "HasPrimaryImage": True,
        # 添加一些客户端可能需要的其他默认字段
        "UserData": {
            "IsFavorite": False,
            "Likes": None,
            "LastPlayedDate": None,
            "Played": False,
            "PlaybackPositionTicks": 0,
            "PlayCount": 0,
            "PlayedPercentage": None
        }
    }
    
    # 返回伪造的 JSON 响应
    return Response(content=json.dumps(fake_item_data), status_code=200, media_type="application/json")