# src/proxy_handlers/handler_home_sections.py (Emby 4.10+ 首页分区适配)
"""Emby 4.10 起,Web/客户端首页改为"服务端分区"模型,不再只用 /Views:
  GET /Users/{id}/HomeSections                首页分区布局列表
  GET /Users/{id}/Sections/{key}/Items        各分区的内容
"我的媒体"磁贴来自 smalllibrarytiles 分区,每个库的"最新"横排来自
latestmedia_{库Id} 分区。代理若不拦截这些接口,新版首页将永远只显示
真实媒体库——这正是 Emby 4.10 升级后虚拟库"从首页消失"的原因。

除注入虚拟库外,还必须按布局规则过滤真实库分区,否则被隐藏的真实库
仍会以"最新{库名}"横排出现在首页:display_order 模式下,不在布局里的
真实库分区一律剔除;旧版模式按 hide 的 CollectionType 剔除。

本处理器只拦截 GET,且只处理三类请求:
  1. /Users/{id}/HomeSections                     -> 过滤隐藏库分区 + 为可见虚拟库补 latestmedia 分区(按 display_order 排序)
  2. /Users/{id}/Sections/{smalllibrarytiles|librarybuttons}/Items -> 按布局规则重建库磁贴(与 /Views 同一套规则)
  3. /Users/{id}/Sections/latestmedia_{虚拟库Id}/Items -> 返回该虚拟库的最新项目
其余全局分区(resume/nextup/onnow 等)与可见真实库分区原样透传。
"""

import json
import logging
from pathlib import Path
from typing import Optional

from fastapi import Request, Response
from aiohttp import ClientSession

from models import AppConfig, VirtualLibrary

from . import handler_merger, handler_views
from emby_net import emby_proxy_of
from .handler_views import apply_full_layout, apply_legacy_layout
from ._filter_translator import translate_rules
from .handler_items import _apply_post_filter

logger = logging.getLogger(__name__)

COVERS_DIR = Path("/app/config/images")
DEFAULT_LATEST_LIMIT = 12


def _get_user_id(params, full_path: str):
    return handler_views._extract_user_id(params, full_path)


def _forward_headers(request: Request) -> dict:
    return handler_views._forward_headers(request)


def _is_home_sections(full_path: str) -> bool:
    return "/HomeSections" in full_path


def _section_key(full_path: str) -> Optional[str]:
    """从 .../Users/{uid}/Sections/{key}/Items 中提取 key。"""
    if "/Sections/" not in full_path or not full_path.rstrip('/').endswith("/Items"):
        return None
    parts = full_path.rstrip('/').split('/')
    try:
        items_index = parts.index('Items')
        return parts[items_index - 1]
    except (ValueError, IndexError):
        return None


async def handle_home_sections(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig
) -> Response | None:
    if method != "GET":
        return None
    # 没有虚拟库也可能需要过滤:仅配置 display_order / hide 隐藏真实库时,新版首页同样会泄露
    if not (config.virtual_libraries or config.display_order or config.hide):
        return None

    if _is_home_sections(full_path):
        return await _handle_home_sections_layout(request, full_path, real_emby_url, session, config)

    key = _section_key(full_path)
    if key in ("smalllibrarytiles", "librarybuttons"):
        return await _handle_small_library_tiles(request, full_path, real_emby_url, session, config)

    if key and key.startswith("latestmedia_"):
        vlib_id = key[len("latestmedia_"):]
        vlib = next((v for v in config.virtual_libraries if v.id == vlib_id), None)
        if vlib:
            return await _handle_vlib_latest_media(request, full_path, vlib, real_emby_url, session, config)

    return None


# --- 1. HomeSections:过滤隐藏库分区 + 为虚拟库补充 latestmedia 分区 ---

def _section_lib_id(section) -> Optional[str]:
    """分区绑定的库 Id:优先 ParentId / ParentItem.Id,latestmedia 分区兜底从 Id 前缀解析。

    全局分区(resume/nextup/onnow 等)没有这些字段,返回 None。
    """
    if not isinstance(section, dict):
        return None
    lib_id = section.get("ParentId") or (section.get("ParentItem") or {}).get("Id")
    if not lib_id:
        section_id = str(section.get("Id") or "")
        if section_id.startswith("latestmedia_"):
            lib_id = section_id[len("latestmedia_"):]
    return str(lib_id) if lib_id else None


def _is_latest_section(section) -> bool:
    if not isinstance(section, dict):
        return False
    return str(section.get("Id") or "").startswith("latestmedia_") or section.get("SectionType") == "latestmedia"


def _is_hidden_section(section, config: AppConfig, vlib_ids: set) -> bool:
    """判断一个真实库分区是否应按布局规则从首页隐藏。"""
    lib_id = _section_lib_id(section)
    if not lib_id or lib_id in vlib_ids:
        return False
    if config.display_order:
        return lib_id not in set(config.display_order)
    if config.hide:
        parent = section.get("ParentItem") or {}
        collection_type = parent.get("CollectionType") or section.get("CollectionType")
        return collection_type in config.hide
    return False


def _rebuild_layout(sections: list, hidden_idx: set, vlib_sections: dict, display_order: list) -> list:
    """按布局规则重建首页分区列表:剔除隐藏库分区,把 latestmedia 分区块
    按 display_order 重排并把虚拟库分区插入对应库的位置。

    display_order 为空(旧版 hide 模式)时,可见真实库分区保持原顺序、
    虚拟库分区排在其后。插入位置按原始列表中第一个 latestmedia 分区计算,
    保证真实库分区被剔光时虚拟库的"最新"行仍顶到原来的位置,而不是掉到
    继续观看/正在播放之后。
    """
    by_lib = {}
    for i, s in enumerate(sections):
        if i in hidden_idx:
            continue
        if _is_latest_section(s):
            by_lib.setdefault(_section_lib_id(s), s)
    by_lib.update(vlib_sections)

    ordered = []
    for lib_id in display_order:
        s = by_lib.pop(lib_id, None)
        if s is not None:
            ordered.append(s)
    ordered.extend(by_lib.values())

    out = []
    insert_at = None
    kept_count = 0
    for i, s in enumerate(sections):
        if _is_latest_section(s):
            if insert_at is None:
                insert_at = kept_count
            continue
        if i in hidden_idx:
            continue
        out.append(s)
        kept_count += 1
    if insert_at is None:
        insert_at = len(out)
    out[insert_at:insert_at] = ordered
    return out


def _build_vlib_latest_section(vlib: VirtualLibrary, server_id: str, user_id=None, api_key=None) -> dict:
    """仿照真实库的 latestmedia 分区结构,构造虚拟库的"最新"分区。"""
    parent_item = handler_views.build_vlib_view_stub(vlib, server_id, user_id, api_key)
    # 分区头可能引用 ParentItem 的这些字段,补齐常见项
    parent_item.setdefault("SortName", vlib.name)
    parent_item.setdefault("BackdropImageTags", [])
    parent_item.setdefault("UserData", {"PlaybackPositionTicks": 0, "IsFavorite": False, "Played": False})
    parent_item["DisplayPreferencesId"] = vlib.id

    return {
        "Name": f"最新{vlib.name}",
        "Id": f"latestmedia_{vlib.id}",
        "SectionType": "latestmedia",
        "CollectionType": "tvshows",
        "Monitor": ["markplayed", "videoplayback"],
        "ItemTypes": [],
        "ExcludedFolders": [],
        "CardSizeOffset": 0,
        "ParentItem": parent_item,
        "ParentId": vlib.id,
        "IncludeNextUpInResume": True,
        "Query": {"StudioIds": [], "TagIds": [], "GenreIds": [], "CollectionTypes": []}
    }


async def _handle_home_sections_layout(
    request: Request, full_path: str, real_emby_url: str, session: ClientSession, config: AppConfig
) -> Response | None:
    target_url = f"{real_emby_url}/{full_path}"

    async with session.get(target_url, params=request.query_params, headers=_forward_headers(request), proxy=emby_proxy_of(request)) as resp:
        if resp.status != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
            return None
        sections = await resp.json()

    if not isinstance(sections, list):
        return None

    # server_id 优先取已有分区里 ParentItem 的,兜底用配置或固定值
    server_id = "emby"
    for s in sections:
        if isinstance(s, dict) and isinstance(s.get("ParentItem"), dict) and s["ParentItem"].get("ServerId"):
            server_id = s["ParentItem"]["ServerId"]
            break
    else:
        server_id = config.emby_server_id or "emby"

    user_id = _get_user_id(request.query_params, full_path)
    api_key = request.query_params.get("X-Emby-Token") or config.emby_api_key
    vlib_ids = {v.id for v in config.virtual_libraries}
    existing_ids = {str(s.get("Id")) for s in sections if isinstance(s, dict)}

    # 1. 标记被隐藏真实库的分区(最新横排等一切绑定到隐藏库的分区)
    hidden_idx = {i for i, s in enumerate(sections) if _is_hidden_section(s, config, vlib_ids)}
    removed = len(hidden_idx)

    # 2. 注入虚拟库分区:display_order 模式下仅注入布局内的虚拟库,旧版模式全部注入
    vlib_sections = {}
    if config.display_order:
        order_set = set(config.display_order)
        for vlib in config.virtual_libraries:
            # 与 /Views 磁贴同一套规则:不在 display_order 里的虚拟库同样不进首页
            if vlib.id not in order_set or f"latestmedia_{vlib.id}" in existing_ids:
                continue
            vlib_sections[vlib.id] = _build_vlib_latest_section(vlib, server_id, user_id, api_key)
    else:
        for vlib in sorted(config.virtual_libraries, key=lambda v: v.order):
            if f"latestmedia_{vlib.id}" in existing_ids:
                continue
            vlib_sections[vlib.id] = _build_vlib_latest_section(vlib, server_id, user_id, api_key)

    added = len(vlib_sections)
    if not removed and not added:
        return None

    kept = _rebuild_layout(sections, hidden_idx, vlib_sections, config.display_order)
    logger.info(f"HOME_SECTIONS: 首页分区布局已调整(剔除隐藏库分区 {removed} 个,注入虚拟库分区 {added} 个)。")
    return Response(content=json.dumps(kept).encode('utf-8'), status_code=200, media_type="application/json")


# --- 2. smalllibrarytiles:"我的媒体"磁贴 ---

async def _handle_small_library_tiles(
    request: Request, full_path: str, real_emby_url: str, session: ClientSession, config: AppConfig
) -> Response | None:
    target_url = f"{real_emby_url}/{full_path}"

    async with session.get(target_url, params=request.query_params, headers=_forward_headers(request), proxy=emby_proxy_of(request)) as resp:
        if resp.status != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
            return None
        data = await resp.json()

    if not isinstance(data, dict):
        return None

    logger.info(f"HOME_SECTIONS: 拦截首页『我的媒体』磁贴 ({key}),按布局规则重建。")
    if config.display_order:
        data = apply_full_layout(data, config, request.query_params, full_path)
    else:
        data = apply_legacy_layout(data, config)

    return Response(content=json.dumps(data).encode('utf-8'), status_code=200, media_type="application/json")


# --- 3. latestmedia_{虚拟库Id}/Items:虚拟库的最新横排 ---

async def _handle_vlib_latest_media(
    request: Request, full_path: str, vlib: VirtualLibrary, real_emby_url: str, session: ClientSession, config: AppConfig
) -> Response | None:
    user_id = _get_user_id(request.query_params, full_path)
    if not user_id:
        return None

    logger.info(f"HOME_SECTIONS: 拦截虚拟库 '{vlib.name}' 的首页最新分区请求。")

    # RSS 库:直接读 rss_library_items.db(与 handler_latest 的 /Items/Latest 逻辑一致)
    if vlib.resource_type == 'rsshub':
        return await _rss_latest_items(request, vlib, user_id, session, real_emby_url)

    params = request.query_params
    new_params = {}
    safe_params_to_inherit = ["Fields", "EnableImageTypes", "ImageTypeLimit", "X-Emby-Token", "EnableUserData"]
    for key in safe_params_to_inherit:
        if key in params:
            new_params[key] = params[key]

    new_params["SortBy"] = "DateCreated"
    new_params["SortOrder"] = "Descending"
    new_params["Recursive"] = "true"
    new_params["IncludeItemTypes"] = "Movie,Series,Video"

    post_filter_rules = []
    if vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == vlib.advanced_filter_id), None)
        if adv_filter:
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update(emby_native_params)

    is_tmdb_merge_enabled = vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id
    try:
        client_limit = int(params.get("Limit", DEFAULT_LATEST_LIMIT))
    except (ValueError, TypeError):
        client_limit = DEFAULT_LATEST_LIMIT

    if post_filter_rules or is_tmdb_merge_enabled:
        # 后筛选/合并需要在更大的数据集上进行,取回后再截断
        fetch_limit = min(max(client_limit * 10, 50), 200)
        new_params["Limit"] = fetch_limit
    else:
        new_params["Limit"] = client_limit

    required_fields = set(["ProviderIds"])
    if post_filter_rules:
        for rule in post_filter_rules:
            required_fields.add(rule.field.split('.')[0])
    current_fields = set(new_params.get("Fields", "").split(','))
    current_fields.discard('')
    current_fields.update(required_fields)
    new_params["Fields"] = ",".join(sorted(list(current_fields)))

    resource_map = {"collection": "CollectionIds", "tag": "TagIds", "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds"}
    if vlib.resource_type in resource_map:
        new_params[resource_map[vlib.resource_type]] = vlib.resource_id
    # 'all' 类型不加资源过滤,即取全库最新

    target_url = f"{real_emby_url}/emby/Users/{user_id}/Items"
    async with session.get(target_url, params=new_params, headers=_forward_headers(request), proxy=emby_proxy_of(request)) as resp:
        if resp.status != 200 or "application/json" not in resp.headers.get("Content-Type", ""):
            return None

        data = await resp.json()
        items_list = data.get("Items", [])

        if post_filter_rules:
            items_list = _apply_post_filter(items_list, post_filter_rules)

        if is_tmdb_merge_enabled:
            items_list = await handler_merger.merge_items_by_tmdb(items_list)

        items_list = items_list[:client_limit]

        # 分区接口返回 {Items: [...]} 形状(与真实库的 latestmedia 分区一致)
        final_data = {"Items": items_list, "TotalRecordCount": len(items_list)}
        return Response(content=json.dumps(final_data).encode('utf-8'), status_code=200, media_type="application/json")


async def _rss_latest_items(
    request: Request, vlib: VirtualLibrary, user_id: str, session: ClientSession, real_emby_url: str
) -> Response:
    from .handler_rss import RssHandler
    rss_handler = RssHandler()
    try:
        limit = int(request.query_params.get("Limit", DEFAULT_LATEST_LIMIT))
    except (ValueError, TypeError):
        limit = DEFAULT_LATEST_LIMIT

    latest_items_from_db = rss_handler.rss_library_db.fetchall(
        "SELECT tmdb_id, media_type, emby_item_id FROM rss_library_items WHERE library_id = ? ORDER BY rowid DESC LIMIT ?",
        (vlib.id, limit)
    )
    if not latest_items_from_db:
        return Response(content=json.dumps({"Items": [], "TotalRecordCount": 0}).encode('utf-8'),
                        status_code=200, media_type="application/json")

    existing_emby_ids = [str(item['emby_item_id']) for item in latest_items_from_db if item['emby_item_id']]
    missing_items_info = [{'tmdb_id': item['tmdb_id'], 'media_type': item['media_type']}
                          for item in latest_items_from_db if not item['emby_item_id']]

    existing_items_data = []
    server_id = None
    if existing_emby_ids:
        existing_items_data = await rss_handler._get_emby_items_by_ids_async(
            existing_emby_ids,
            request_params=request.query_params,
            user_id=user_id,
            session=session,
            real_emby_url=real_emby_url,
            request_headers=request.headers
        )
        if existing_items_data:
            server_id = existing_items_data[0].get("ServerId")

    if not server_id:
        server_id = rss_handler.config.emby_server_id or "emby"

    final_items = list(existing_items_data)
    for item_info in missing_items_info:
        item = rss_handler._get_item_from_tmdb(item_info['tmdb_id'], item_info['media_type'], server_id)
        if item:
            final_items.append(item)

    final_data = {"Items": final_items, "TotalRecordCount": len(final_items)}
    return Response(content=json.dumps(final_data).encode('utf-8'), status_code=200, media_type="application/json")
