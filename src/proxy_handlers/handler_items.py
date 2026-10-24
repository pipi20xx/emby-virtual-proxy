# src/proxy_handlers/handler_items.py (高性能重构版)

import asyncio
import json
import logging
import random
import time
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig, AdvancedFilter
from typing import List, Any, Dict, Optional, Tuple

from . import handler_merger, handler_views
# 导入我们新的翻译器和旧的后筛选逻辑
from ._filter_translator import translate_rules
from .handler_rss import RssHandler
from proxy_cache import vlib_items_cache, serving_items_cache
import db_manager
logger = logging.getLogger(__name__)

# ================= 本地直出(访问加速)配置 =================
# 软过期:超过后照常直出,同时后台异步刷新(stale-while-revalidate)
_SERVING_SOFT_TTL = 6 * 3600
# 硬过期:超过后不再直出,回退实时查询并后台刷新
_SERVING_HARD_TTL = 72 * 3600

# 全量解析时向 Emby 索取的字段(覆盖常规客户端浏览所需)
_SERVING_FETCH_FIELDS = [
    "ProviderIds", "Genres", "Tags", "Studios", "People", "OfficialRatings", "CommunityRating",
    "ProductionYear", "VideoRange", "Container", "Overview", "PremiereDate", "DateCreated",
    "SortName", "PrimaryImageAspectRatio", "ChildCount", "MediaSourceCount", "BasicSyncInfo", "CanDelete",
]

# 请求中出现以下参数说明不是"简单浏览",回退实时查询(保证过滤语义正确)
_SERVING_BLOCKLIST_PARAMS = [
    "SearchTerm", "Filters", "Genres", "Years", "OfficialRating", "Tags", "Studios",
    "Person", "PersonIds", "GenreIds", "StudioIds", "TagIds", "CollectionIds",
    "MinCommunityRating", "MinDateCreated", "MaxDateCreated", "Ids", "IsPlayed",
    "IsUnplayed", "IsFavorite", "IsResume", "IsLiked", "AlbumArtistIds", "ArtistIds",
]

# 直出响应的本地排序支持
_SERVING_NUMERIC_SORT_FIELDS = {"CommunityRating", "ProductionYear"}
_SERVING_DATE_SORT_FIELDS = {"DateCreated", "PremiereDate"}

# 转发上游的请求头白名单(与 handler_views 保持一致)
_FORWARDABLE_HEADERS = [
    'accept', 'accept-language', 'user-agent',
    'x-emby-authorization', 'x-emby-client', 'x-emby-device-name',
    'x-emby-device-id', 'x-emby-client-version', 'x-emby-language',
    'x-emby-token'
]

# --- 后筛选逻辑 (保留用于处理无法翻译的规则) ---
def _get_nested_value(item: Dict[str, Any], field_path: str) -> Any:
    keys = field_path.split('.')
    value = item
    for key in keys:
        if isinstance(value, dict): value = value.get(key)
        else: return None
    return value

def _check_condition(item_value: Any, operator: str, rule_value: str) -> bool:
    if operator not in ["is_empty", "is_not_empty"]:
        if item_value is None: return False
        if isinstance(item_value, list):
            if operator == "contains": return rule_value in item_value
            if operator == "not_contains": return rule_value not in item_value
            return False
        if operator in ["greater_than", "less_than"]:
            try:
                if operator == "greater_than": return float(item_value) > float(rule_value)
                if operator == "less_than": return float(item_value) < float(rule_value)
            except (ValueError, TypeError): return False
        item_value_str = str(item_value).lower()
        rule_value_str = str(rule_value).lower()
        if operator == "equals": return item_value_str == rule_value_str
        if operator == "not_equals": return item_value_str != rule_value_str
        if operator == "contains": return rule_value_str in item_value_str
        if operator == "not_contains": return rule_value_str not in item_value_str
    if operator == "is_empty": return item_value is None or item_value == '' or item_value == []
    if operator == "is_not_empty": return item_value is not None and item_value != '' and item_value != []
    return False

def _apply_post_filter(items: List[Dict[str, Any]], post_filter_rules: List[Dict]) -> List[Dict[str, Any]]:
    if not post_filter_rules: return items
    logger.info(f"在 {len(items)} 个项目上应用 {len(post_filter_rules)} 条后筛选规则。")
    filtered_items = []
    for item in items:
        if all(_check_condition(_get_nested_value(item, rule.field), rule.operator, rule.value) for rule in post_filter_rules):
            filtered_items.append(item)
    return filtered_items


# ================= 本地直出(访问加速) =================

def _build_resolution_params(config: AppConfig, vlib, api_token: Optional[str]) -> Tuple[Dict[str, Any], List[Dict]]:
    """构建虚拟库解析请求参数(规则过滤 + 高级筛选翻译),供全量拉取复用。"""
    new_params: Dict[str, Any] = {
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series,Video",
        "Fields": ",".join(_SERVING_FETCH_FIELDS),
    }
    if api_token:
        new_params["X-Emby-Token"] = api_token

    resource_map = {"collection": "CollectionIds", "tag": "TagIds", "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds"}
    if vlib.resource_type in resource_map:
        new_params[resource_map[vlib.resource_type]] = vlib.resource_id

    post_filter_rules: List[Dict] = []
    if vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == vlib.advanced_filter_id), None)
        if adv_filter:
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update(emby_native_params)

    return new_params, post_filter_rules


async def resolve_full_vlib_items(config: AppConfig, vlib, user_id: str, session: ClientSession,
                                  real_emby_url: str, headers: Dict[str, str]) -> List[Dict[str, Any]]:
    """解析虚拟库完整项目列表(规则过滤 + 高级筛选 + TMDB 合并),供本地直出与预热使用。

    与浏览路径的页式查询不同,这里始终全量分页拉取;失败返回空列表。
    """
    try:
        token = headers.get("X-Emby-Token") or headers.get("x-emby-token")
        new_params, post_filter_rules = _build_resolution_params(config, vlib, token)
        search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"

        all_items: List[Dict[str, Any]] = []
        start_index, limit = 0, 200
        while True:
            fetch_params = new_params.copy()
            fetch_params["StartIndex"] = str(start_index)
            fetch_params["Limit"] = str(limit)
            async with session.get(search_url, params=fetch_params, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"全量解析 '{vlib.name}' 批次请求失败, Status: {resp.status}")
                    return []
                batch_items = (await resp.json(content_type=None)).get("Items", [])
                if not batch_items:
                    break
                all_items.extend(batch_items)
                start_index += len(batch_items)
                if len(batch_items) < limit:
                    break

        if post_filter_rules:
            all_items = _apply_post_filter(all_items, post_filter_rules)

        if vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id:
            all_items = await handler_merger.merge_items_by_tmdb(all_items)

        logger.info(f"全量解析 '{vlib.name}' 完成,共 {len(all_items)} 个项目。")
        return all_items
    except Exception as e:
        logger.warning(f"解析虚拟库 '{vlib.name}' 完整列表时出错: {e}")
        return []


def store_serving_cache(vlib, user_id: str, items: List[Dict[str, Any]]) -> None:
    """写入单个 (用户, 虚拟库) 的直出缓存(内存 + SQLite),供预热与后台刷新复用。"""
    now = int(time.time())
    serving_items_cache[(user_id, vlib.id)] = {"items": items, "updated_at": now}
    db_manager.save_vlib_serving_items(user_id, vlib.id, items, now)


async def refresh_serving_cache(config: AppConfig, vlib, user_id: str, session: ClientSession,
                                real_emby_url: str, headers: Dict[str, str]) -> int:
    """重建单个 (用户, 虚拟库) 的直出缓存(受缓存总开关约束),返回项目数。"""
    items = await resolve_full_vlib_items(config, vlib, user_id, session, real_emby_url, headers)
    if items and config.enable_vlib_cache and config.enable_vlib_local_serving:
        store_serving_cache(vlib, user_id, items)
    return len(items)


_refreshing_serving_keys = set()


def _refresh_serving_background(config: AppConfig, vlib, user_id: str, session: ClientSession,
                                real_emby_url: str, headers: Dict[str, str]) -> None:
    """后台重建直出缓存(带去重锁,避免并发风暴)。"""
    key = (user_id, vlib.id)
    if key in _refreshing_serving_keys:
        return
    _refreshing_serving_keys.add(key)

    async def _task():
        try:
            count = await refresh_serving_cache(config, vlib, user_id, session, real_emby_url, headers)
            logger.info(f"后台刷新直出缓存完成:'{vlib.name}' 共 {count} 个项目。")
        except Exception as e:
            logger.warning(f"后台刷新直出缓存失败 ('{vlib.name}'): {e}")
        finally:
            _refreshing_serving_keys.discard(key)

    asyncio.create_task(_task())


def _sort_items_locally(items: List[Dict[str, Any]], sort_by: str, sort_order: str) -> List[Dict[str, Any]]:
    """在代理端对直出列表按客户端的 SortBy/SortOrder 排序。"""
    descending = (sort_order or "Ascending").lower() == "descending"
    tokens = [t.strip() for t in (sort_by or "SortName").split(",") if t.strip()]

    if "Random" in tokens:
        random.shuffle(items)
        tokens = [t for t in tokens if t != "Random"]

    def _key(token: str):
        if token in _SERVING_NUMERIC_SORT_FIELDS:
            return lambda i: float(i.get(token) or 0)
        if token in _SERVING_DATE_SORT_FIELDS:
            return lambda i: (i.get(token) or "")
        return lambda i: str(i.get(token) or i.get("Name") or "").lower()

    # 逆序多级稳定排序,保证第一个 SortBy 权重最高
    for token in reversed(tokens):
        items.sort(key=_key(token), reverse=descending)
    return items


async def try_serve_locally(config: AppConfig, vlib, user_id: str, params, method: str,
                            session: ClientSession, real_emby_url: str,
                            request_headers) -> Optional[Response]:
    """本地直出:命中完整解析缓存时在代理端排序/分页后直接返回,不再请求 Emby。

    仅处理"简单浏览"请求(无筛选/搜索参数);未命中或超过硬过期时回退实时查询,
    超过软过期时照常直出并在后台异步刷新(stale-while-revalidate)。
    """
    if method != "GET" or not config.enable_vlib_local_serving or not config.enable_vlib_cache:
        return None
    # 仅当筛选/搜索参数带"非空值"时才回退实时查询;
    # Emby 客户端常发送空值参数(如 Filters=),不能据此判定为筛选请求
    blocked_params = [p for p in _SERVING_BLOCKLIST_PARAMS if params.get(p) not in (None, "")]
    if blocked_params:
        logger.info(f"直出跳过:'{vlib.name}' 请求包含筛选参数 {blocked_params},回退实时查询。")
        return None
    if str(params.get("Recursive", "true")).lower() not in ("", "true"):
        return None

    key = (user_id, vlib.id)
    entry = serving_items_cache.get(key)
    if entry is None:
        persisted = db_manager.load_vlib_serving_items(user_id, vlib.id)
        if persisted:
            entry = {"items": persisted[0], "updated_at": persisted[1]}
            serving_items_cache[key] = entry

    headers = {k: v for k, v in request_headers.items() if k.lower() in _FORWARDABLE_HEADERS}

    if entry is None:
        # 首次访问:本次仍走实时查询,同时后台构建全量缓存,下次即可直出
        logger.info(f"直出缓存未命中('{vlib.name}'),本次实时查询并后台构建缓存。")
        _refresh_serving_background(config, vlib, user_id, session, real_emby_url, headers)
        return None

    age = int(time.time()) - int(entry["updated_at"])
    if age > _SERVING_HARD_TTL:
        logger.info(f"直出缓存已硬过期({age // 3600}h),回退实时查询并后台刷新。")
        _refresh_serving_background(config, vlib, user_id, session, real_emby_url, headers)
        return None

    items = list(entry["items"])

    # IncludeItemTypes 本地过滤(缓存本身是 Movie,Series,Video 的并集)
    type_filter = params.get("IncludeItemTypes")
    if type_filter:
        allowed = {t.strip() for t in type_filter.split(",") if t.strip()}
        if allowed:
            items = [i for i in items if i.get("Type") in allowed]

    items = _sort_items_locally(items, params.get("SortBy", "SortName"), params.get("SortOrder", "Ascending"))

    try:
        start_idx = max(0, int(params.get("StartIndex") or 0))
    except (TypeError, ValueError):
        start_idx = 0
    limit_raw = params.get("Limit")
    try:
        limit = max(0, int(limit_raw)) if limit_raw else None
    except (TypeError, ValueError):
        limit = None
    page = items[start_idx:] if not limit else items[start_idx:start_idx + limit]

    payload = {"Items": page, "TotalRecordCount": len(items), "StartIndex": start_idx}
    content = json.dumps(payload).encode("utf-8")

    if age > _SERVING_SOFT_TTL:
        logger.info(f"直出缓存已软过期({age // 3600}h),照常返回并后台刷新。")
        _refresh_serving_background(config, vlib, user_id, session, real_emby_url, headers)

    logger.info(f"本地直出:'{vlib.name}' 命中解析缓存(共 {len(items)} 项),返回 {len(page)} 项。")
    return Response(content=content, status_code=200, media_type="application/json")


async def handle_virtual_library_items(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
    config: AppConfig
) -> Response | None:
    if "/Items/Prefixes" in full_path or "/Items/Counts" in full_path or "/Items/Latest" in full_path:
        return None

    params = request.query_params
    found_vlib = None
    
    parent_id_from_param = params.get("ParentId")
    if parent_id_from_param:
        found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == parent_id_from_param), None)

    if not found_vlib and method == "GET" and 'Items' in full_path:
        path_parts = full_path.split('/');
        try:
            items_index = path_parts.index('Items')
            if items_index + 1 < len(path_parts):
                potential_path_vlib_id = path_parts[items_index + 1]
                found_vlib = next((vlib for vlib in config.virtual_libraries if vlib.id == potential_path_vlib_id), None)
        except ValueError: pass
    
    if not found_vlib:
        # 兼容Go版本的后备方案：处理非标准客户端（如网易爆米花）的请求
        # 这些客户端通过 /Users/xxx/Items 获取根视图，且请求参数中不含任何 'Id'
        has_id_param = any(key.lower().endswith('id') for key in params.keys())
        
        if not has_id_param:
            logger.info("检测到非标准客户端请求 (无 'Id' 后缀参数)，作为后备方案返回媒体库视图。")
            # 调用 handler_views 中的逻辑来返回一个伪造的根视图
            return await handler_views.handle_view_injection(request, full_path, method, real_emby_url, session, config)
        
        # 如果有 'Id' 参数但不是虚拟库，则为正常请求，放行
        return None

    logger.info(f"拦截到虚拟库 '{found_vlib.name}'，开始高性能筛选流程。")
    
    user_id = params.get("UserId")
    if not user_id:
        # (保持原有的UserId查找逻辑)
        path_parts_for_user = full_path.split('/')
        if 'Users' in path_parts_for_user:
            try:
                user_id_index = path_parts_for_user.index('Users') + 1
                if user_id_index < len(path_parts_for_user): user_id = path_parts_for_user[user_id_index]
            except (ValueError, IndexError): pass
    if not user_id: return Response(content="UserId not found", status_code=400)

    # --- 开始构建请求 ---
    new_params = {}
    
    # 【【【核心优化点 1】】】: 默认继承客户端的所有安全参数，包括分页和排序！
    client_start_index = params.get("StartIndex", "0")
    client_limit = params.get("Limit", "50")
    safe_params_to_inherit = [
        "SortBy", "SortOrder", "Fields", "EnableImageTypes", "ImageTypeLimit", 
        "EnableTotalRecordCount", "X-Emby-Token", "StartIndex", "Limit"
    ]
    for key in safe_params_to_inherit:
        if key in params: new_params[key] = params[key]

    required_fields = ["ProviderIds", "Genres", "Tags", "Studios", "People", "OfficialRatings", "CommunityRating", "ProductionYear", "VideoRange", "Container"]
    if "Fields" in new_params:
        existing_fields = set(new_params["Fields"].split(','))
        missing_fields = [f for f in required_fields if f not in existing_fields]
        if missing_fields: new_params["Fields"] += "," + ",".join(missing_fields)
    else: new_params["Fields"] = ",".join(required_fields)

    new_params["Recursive"] = "true"
    new_params["IncludeItemTypes"] = "Movie,Series,Video"

    resource_map = {"collection": "CollectionIds", "tag": "TagIds", "person": "PersonIds", "genre": "GenreIds", "studio": "StudioIds"}
    if found_vlib.resource_type in resource_map:
        new_params[resource_map[found_vlib.resource_type]] = found_vlib.resource_id
    # --- 【【【 新增：借鉴“缺失剧集”逻辑，重构 RSS 库的统一处理方案 】】】 ---
    elif found_vlib.resource_type == "rsshub":
        # 终极修复：将所有 RSS 逻辑委托给 RssHandler，并传入必要的上下文
        rss_handler = RssHandler()
        # 关键：传入 request.query_params 以便 RssHandler 可以继承 Fields 等参数
        # 关键：传入 user_id 和 session 以便 RssHandler 可以自己发起请求
        response_data = await rss_handler.handle(
            request_path=full_path, 
            vlib_id=found_vlib.id,
            request_params=request.query_params,
            user_id=user_id,
            session=session,
            real_emby_url=real_emby_url,
            request_headers=request.headers
        )
        
        # 手动分页
        start_idx = int(params.get("StartIndex", 0))
        limit_str = params.get("Limit")
        final_items = response_data.get("Items", [])
        
        if limit_str:
            try:
                limit = int(limit_str)
                paginated_items = final_items[start_idx : start_idx + limit]
            except (ValueError, TypeError):
                paginated_items = final_items[start_idx:]
        else:
            paginated_items = final_items[start_idx:]
            
        final_response = {"Items": paginated_items, "TotalRecordCount": len(final_items)}
        return Response(content=json.dumps(final_response).encode('utf-8'), media_type="application/json")
    # --- 【【【 RSS 逻辑结束 】】】 ---

    # --- 【【【 本地直出：命中解析缓存时不再请求 Emby（访问加速） 】】】 ---
    served = await try_serve_locally(config, found_vlib, user_id, params, method, session, real_emby_url, request.headers)
    if served is not None:
        return served

    # 【【【核心优化点 2】】】: 应用高级筛选器翻译
    post_filter_rules = []
    if found_vlib.advanced_filter_id:
        adv_filter = next((f for f in config.advanced_filters if f.id == found_vlib.advanced_filter_id), None)
        if adv_filter:
            logger.info(f"正在为高级筛选器 '{adv_filter.name}' 翻译规则...")
            emby_native_params, post_filter_rules = translate_rules(adv_filter.rules)
            new_params.update(emby_native_params)
            if post_filter_rules: logger.info(f"有 {len(post_filter_rules)} 条规则需要在代理端后筛选。")
        else:
            logger.warning(f"虚拟库配置了高级筛选器ID '{found_vlib.advanced_filter_id}'，但未找到。")

    # 修复：如果 IsMovie 为 true，则强制 IncludeItemTypes 为 Movie
    if new_params.get("IsMovie") == "true":
        new_params["IncludeItemTypes"] = "Movie"
        logger.info("检测到 IsMovie: 'true'，强制设置 IncludeItemTypes 为 'Movie'。")
    elif new_params.get("IsSeries") == "true":
        new_params["IncludeItemTypes"] = "Series"
        logger.info("检测到 IsSeries: 'true'，强制设置 IncludeItemTypes 为 'Series'。")

    # 【【【核心优化点 3】】】: 处理合并的特殊情况
    # 如果启用了TMDB合并，我们需要获取一个更大的数据集来进行有效的合并，然后再在代理端进行分页。
    # 这是一种混合模式，仍然远比获取所有项目要高效。
    is_tmdb_merge_enabled = found_vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id

    target_emby_api_path = f"Users/{user_id}/Items"
    search_url = f"{real_emby_url}/emby/{target_emby_api_path}"
    
    headers_to_forward = {
        k: v for k, v in request.headers.items() 
        if k.lower() in [
            'accept', 'accept-language', 'user-agent',
            'x-emby-authorization', 'x-emby-client', 'x-emby-device-name',
            'x-emby-device-id', 'x-emby-client-version', 'x-emby-language',
            'x-emby-token'
        ]
    }
    
    logger.debug(f"向真实 Emby 发起优化后的最终请求: URL={search_url}, Params={new_params}")

    # 如果不启用TMDB合并，或者有无法翻译的后筛选规则，则走常规分页逻辑
    if not is_tmdb_merge_enabled or post_filter_rules:
        if is_tmdb_merge_enabled and post_filter_rules:
            logger.warning("TMDB合并已启用，但存在无法翻译的后筛选规则，合并将在当前页进行，可能不完整。")

        async with session.request(method, search_url, params=new_params, headers=headers_to_forward) as resp:
            if resp.status != 200:
                content = await resp.read()
                return Response(content=content, status_code=resp.status, headers=resp.headers)
            
            content = await resp.read()
            response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding', 'content-length')}
            
            if "application/json" in resp.headers.get("Content-Type", ""):
                try:
                    data = json.loads(content)
                    items_list = data.get("Items", [])
                    
                    if post_filter_rules:
                        items_list = _apply_post_filter(items_list, post_filter_rules)
                    
                    if is_tmdb_merge_enabled:
                        logger.info("正在对当前页的数据集执行TMDB合并...")
                        items_list = await handler_merger.merge_items_by_tmdb(items_list)
                    
                    data["Items"] = items_list
                    logger.info(f"原生筛选/合并完成。Emby返回总数: {data.get('TotalRecordCount')}, 当前页项目数: {len(items_list)}")
                    
                    final_items_to_return = data.get("Items", [])
                    if final_items_to_return and config.enable_vlib_cache:
                        vlib_items_cache[found_vlib.id] = final_items_to_return
                        db_manager.save_vlib_items_cache(found_vlib.id, final_items_to_return)
                        logger.info(f"✅ 已为虚拟库 '{found_vlib.name}' 缓存 {len(final_items_to_return)} 个项目以供封面生成使用。")
                    
                    content = json.dumps(data).encode('utf-8')
                except (json.JSONDecodeError, Exception) as e:
                    logger.error(f"处理响应时发生错误: {e}")

            return Response(content=content, status_code=resp.status, headers=response_headers)

    # --- TMDB合并的全量获取逻辑 ---
    else:
        logger.info("TMDB合并已启用，开始获取全量数据...")
        all_items = []
        start_index = 0
        limit = 200  # 每次请求200个
        
        # 移除客户端的分页参数，因为我们要自己控制
        new_params.pop("StartIndex", None)
        new_params.pop("Limit", None)
        
        while True:
            fetch_params = new_params.copy()
            fetch_params["StartIndex"] = str(start_index)
            fetch_params["Limit"] = str(limit)
            
            logger.debug(f"正在获取批次: StartIndex={start_index}, Limit={limit}")
            async with session.request(method, search_url, params=fetch_params, headers=headers_to_forward) as resp:
                if resp.status != 200:
                    logger.error(f"获取批次失败，状态码: {resp.status}")
                    # 返回错误或一个空的成功响应
                    return Response(content=json.dumps({"Items": [], "TotalRecordCount": 0}), status_code=200, media_type="application/json")

                batch_data = await resp.json()
                batch_items = batch_data.get("Items", [])
                
                if not batch_items:
                    logger.info("已获取所有数据。")
                    break
                
                all_items.extend(batch_items)
                start_index += len(batch_items)
                
                # 如果返回的项目数小于请求的limit，说明是最后一页
                if len(batch_items) < limit:
                    logger.info("已到达最后一页。")
                    break
        
        logger.info(f"全量数据获取完成，总共 {len(all_items)} 个项目。")

        # 1. 应用TMDB合并
        logger.info("正在对获取到的全量数据集执行TMDB合并...")
        merged_items = await handler_merger.merge_items_by_tmdb(all_items)
        
        # 2. 对合并后的结果进行手动分页
        total_record_count = len(merged_items)
        start_idx = int(client_start_index)
        limit_count = int(client_limit)
        paginated_items = merged_items[start_idx : start_idx + limit_count]
        
        # 3. 构建最终的响应
        final_data = {
            "Items": paginated_items,
            "TotalRecordCount": total_record_count,
            "StartIndex": start_idx
        }
        logger.info(f"合并后手动分页完成。总数: {total_record_count}, 返回页面项目数: {len(paginated_items)}")

        if paginated_items and config.enable_vlib_cache:
            vlib_items_cache[found_vlib.id] = paginated_items
            db_manager.save_vlib_items_cache(found_vlib.id, paginated_items)
            logger.info(f"✅ 已为虚拟库 '{found_vlib.name}' 缓存 {len(paginated_items)} 个项目以供封面生成使用。")

        content = json.dumps(final_data).encode('utf-8')
        # 伪造一个成功的响应头
        response_headers = {
            'Content-Type': 'application/json; charset=utf-8',
            'Content-Length': str(len(content))
        }
        return Response(content=content, status_code=200, headers=response_headers)

    return None
