# src/proxy_handlers/handler_views.py

import json
import logging
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig
import asyncio
from pathlib import Path

# 【新增】导入后台生成处理器和任务锁
from . import handler_autogen
from .handler_images import cover_file_exists
from emby_net import emby_proxy_of

logger = logging.getLogger(__name__)

# 【新增】定义封面存储路径
COVERS_DIR = Path("/app/config/images")

# 转发上游时的请求头白名单(与其它 handler 保持一致)
_HEADERS_WHITELIST = [
    'accept', 'accept-language', 'user-agent',
    'x-emby-authorization', 'x-emby-client', 'x-emby-device-name',
    'x-emby-device-id', 'x-emby-client-version', 'x-emby-language',
    'x-emby-token'
]


def _forward_headers(request: Request) -> dict:
    return {k: v for k, v in request.headers.items() if k.lower() in _HEADERS_WHITELIST}


def _extract_user_id(params, full_path: str):
    user_id = params.get("UserId")
    if not user_id:
        path_parts = full_path.split('/')
        if 'Users' in path_parts:
            try:
                user_id_index = path_parts.index('Users') + 1
                if user_id_index < len(path_parts):
                    user_id = path_parts[user_id_index]
            except (ValueError, IndexError):
                pass
    return user_id


def get_vlib_collection_type(vlib) -> str:
    """虚拟库对客户端呈现的 CollectionType(所有 stub / 详情伪造统一取此值)。

    显式配置优先:movies/tvshows 原样,mixed 映射为 Emby 的通用混合类型 folder;
    未配置时按资源类型自动推导——'全库'(all)本身就是真实全库,按混合呈现,
    其余资源类型沿用历史行为(电视剧),兼容存量配置。
    """
    configured = getattr(vlib, "collection_type", None)
    if configured == "movies":
        return "movies"
    if configured == "mixed":
        return "folder"
    if configured == "tvshows":
        return "tvshows"
    return "folder" if vlib.resource_type == "all" else "tvshows"


def get_vlib_include_item_types(vlib) -> str:
    """虚拟库内容查询的 IncludeItemTypes,与展示类型保持同一语义。"""
    collection_type = get_vlib_collection_type(vlib)
    if collection_type == "movies":
        return "Movie"
    if collection_type == "tvshows":
        return "Series"
    return "Movie,Series,Video"


def build_vlib_view_stub(vlib, server_id: str, user_id=None, api_key=None) -> dict:
    """构建虚拟库在首页视图/磁贴中的占位条目。

    封面已存在时注入真实 ImageTag;否则注入占位 Tag 并触发后台自动生成。
    """
    vlib_data = {
        "Name": vlib.name, "ServerId": server_id, "Id": vlib.id,
        "Type": "CollectionFolder",
        "CollectionType": get_vlib_collection_type(vlib),
        "IsFolder": True,
        "ImageTags": {}
    }

    if cover_file_exists(vlib.id):
        # 图片已存在, 注入真实的 ImageTag
        if vlib.image_tag:
            vlib_data["ImageTags"]["Primary"] = vlib.image_tag
            vlib_data["HasPrimaryImage"] = True
    else:
        # 图片不存在, 触发后台生成并注入假Tag
        logger.info(f"主页视图：发现虚拟库 '{vlib.name}' ({vlib.id}) 缺少封面。")

        # 检查任务是否已在运行，防止重复触发
        if vlib.id not in handler_autogen.GENERATION_IN_PROGRESS:
            if user_id and api_key:
                asyncio.create_task(handler_autogen.generate_poster_in_background(vlib.id, user_id, api_key))
            else:
                logger.warning(f"无法为库 {vlib.id} 触发后台任务，因为缺少 UserId 或 ApiKey。")

        vlib_data["ImageTags"]["Primary"] = "generating_placeholder"
        vlib_data["HasPrimaryImage"] = True

    return vlib_data


def apply_full_layout(original_data: dict, config: AppConfig, params, full_path: str) -> dict:
    """全量布局控制:按 display_order 重建媒体库列表并注入虚拟库。

    供 /Views 与 Emby 4.10+ 的 /Sections/smalllibrarytiles/Items 共用。
    直接修改并返回 original_data。
    """
    all_available_libs = {item["Id"]: item for item in original_data.get("Items", [])}

    server_id = next((item.get("ServerId") for item in all_available_libs.values() if item.get("ServerId")), "unknown")

    user_id = _extract_user_id(params, full_path)
    api_key = params.get("X-Emby-Token") or config.emby_api_key

    for vlib in config.virtual_libraries:
        all_available_libs[vlib.id] = build_vlib_view_stub(vlib, server_id, user_id, api_key)

    original_data["Items"] = [all_available_libs[lib_id] for lib_id in config.display_order if lib_id in all_available_libs]
    original_data["TotalRecordCount"] = len(original_data["Items"])
    return original_data


def apply_legacy_layout(content_json: dict, config: AppConfig) -> dict:
    """旧版布局:按 hide 过滤真实库后,把虚拟库追加到列表末尾。"""
    if content_json.get("Items"):
        if config.hide:
            content_json["Items"] = [item for item in content_json["Items"] if item.get("CollectionType") not in config.hide]

        sorted_virtual_libraries = sorted(config.virtual_libraries, key=lambda vlib: getattr(vlib, 'order', 0))
        server_id = content_json["Items"][0].get("ServerId") if content_json.get("Items") else "unknown"

        for vlib in sorted_virtual_libraries:
            if not any(item.get("Id") == vlib.id for item in content_json["Items"]):
                content_json["Items"].append({
                    "Name": vlib.name, "ServerId": server_id, "Id": vlib.id,
                    "Type": "CollectionFolder", "CollectionType": get_vlib_collection_type(vlib),
                    "IsFolder": True, "ImageTags": {}
                })
    return content_json


async def handle_view_injection(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig
) -> Response | None:
    if "Users" not in full_path or "/Views" not in full_path or method != "GET":
        return None

    if not config.display_order:
        # 旧版逻辑可以保持原样，或者也进行相应修改，但我们主要关注新版
        return await legacy_handle_view_injection(request, full_path, method, real_emby_url, session, config)

    logger.info(f"Full layout control enabled. Intercepting views for path: {full_path}")

    target_url = f"{real_emby_url}/{full_path}"
    params = request.query_params

    async with session.get(target_url, params=params, headers=_forward_headers(request), proxy=emby_proxy_of(request)) as resp:
        if resp.status != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
            return None

        original_data = await resp.json()
        original_data = apply_full_layout(original_data, config, params, full_path)

        final_content = json.dumps(original_data).encode('utf-8')
        return Response(content=final_content, status_code=200, media_type="application/json")


async def legacy_handle_view_injection(request: Request, full_path: str, method: str, real_emby_url: str, session: ClientSession, config: AppConfig):
    target_url = f"{real_emby_url}/{full_path}"
    params = request.query_params

    async with session.get(target_url, params=params, headers=_forward_headers(request), proxy=emby_proxy_of(request)) as resp:
        if resp.status == 200 and "application/json" in resp.headers.get("Content-Type", ""):
            content_json = await resp.json()
            if content_json.get("Items"):
                content_json = apply_legacy_layout(content_json, config)
                final_content = json.dumps(content_json).encode('utf-8')
                return Response(content=final_content, status_code=200, media_type="application/json")
    return None
