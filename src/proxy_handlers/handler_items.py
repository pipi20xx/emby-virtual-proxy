# src/proxy_handlers/handler_items.py (高性能重构版)

import asyncio
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from fastapi import Request, Response
from aiohttp import ClientSession
from models import AppConfig, AdvancedFilter
from typing import List, Any, Dict, Optional, Tuple

from . import handler_merger, handler_views
# 导入我们新的翻译器和旧的后筛选逻辑
from ._filter_translator import (
    find_vlib_filter,
    is_local_mode,
    materialize_local_rules,
    resolve_for_serving,
    resolve_for_user_query,
    USER_STATE_FIELDS,
)
from .handler_rss import RssHandler
from proxy_cache import vlib_items_cache, vlib_metadata_cache, user_overlay_cache
from emby_net import emby_proxy_of
import db_manager
logger = logging.getLogger(__name__)

# ================= 本地直出(访问加速,分层缓存) =================
# 架构:共享元数据(每库一份,管理员身份解析,已剥离 UserData,所有用户共用)
#      + 用户叠加层(每 用户+库 一份,仅存 {条目ID: UserData},体积约为元数据的 5%)
# 服务时按叠加层裁剪可见性并叠加用户状态 —— 元数据只存/解析一份,任意用户数都低成本。
# 软过期:超过后照常返回,同时后台异步刷新(stale-while-revalidate)
_SERVING_SOFT_TTL = 6 * 3600
# 硬过期:元数据超过后不再直出,回退实时查询并后台重建;叠加层硬过期仍照常返回
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

# 叠加层按 Id 批量查询的每批条目数(控制 URL 长度)
_SERVING_OVERLAY_CHUNK = 150

# 转发上游的请求头白名单(与 handler_views 保持一致)
_FORWARDABLE_HEADERS = [
    'accept', 'accept-language', 'user-agent',
    'x-emby-authorization', 'x-emby-client', 'x-emby-device-name',
    'x-emby-device-id', 'x-emby-client-version', 'x-emby-language',
    'x-emby-token'
]

# --- 后筛选逻辑 (本地全量模式的执行引擎;原生模式下处理无法翻译的规则) ---
def _get_nested_value(item: Dict[str, Any], field_path: str) -> Any:
    keys = field_path.split('.')
    value = item
    for key in keys:
        if isinstance(value, dict): value = value.get(key)
        else: return None
    return value

def _parse_dt(value: Any) -> Optional[object]:
    """尝试把值解析为 datetime;支持 'YYYY-MM-DD'(视为当日零点 UTC)与 ISO 8601 时间戳。"""
    if not isinstance(value, str): return None
    s = value.strip()
    if len(s) == 10:
        try: return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError: return None
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None

def _same_date(a, b) -> bool:
    """日期粒度相等比较:一方是日期粒度(YYYY-MM-DD)时只比到天,兼容时区差。"""
    if a.tzinfo is not None and b.tzinfo is not None:
        return a.astimezone(timezone.utc).date() == b.astimezone(timezone.utc).date()
    return a.date() == b.date()

def _compare_ordered(item_value: Any, operator: str, rule_value: str) -> Optional[bool]:
    """数值/日期感知的有序比较;无法比较时返回 None 交由字符串比较兜底。"""
    # 数值优先(年份、评分等)
    try:
        num_a, num_b = float(item_value), float(rule_value)
        if operator == "greater_than": return num_a > num_b
        if operator == "less_than": return num_a < num_b
        if operator == "equals": return num_a == num_b
        if operator == "not_equals": return num_a != num_b
    except (ValueError, TypeError):
        pass
    # 日期感知:Emby 的时间字段是 ISO 时间戳,规则值常是日期粒度
    dt_a, dt_b = _parse_dt(item_value), _parse_dt(rule_value)
    if dt_a is not None and dt_b is not None:
        if operator == "greater_than": return dt_a > dt_b
        if operator == "less_than": return dt_a < dt_b
        if operator == "equals": return _same_date(dt_a, dt_b)
        if operator == "not_equals": return not _same_date(dt_a, dt_b)
    return None

def _check_condition(item_value: Any, operator: str, rule_value: str) -> bool:
    if operator not in ["is_empty", "is_not_empty"]:
        if item_value is None: return False
        if isinstance(item_value, list):
            # 列表字段(Genres/Tags/Studios 等):equals 精确匹配成员,
            # contains 在成员内做子串匹配,排除类取反
            members = [str(v).lower() for v in item_value]
            rule_value_str = str(rule_value).lower()
            if operator == "equals": return rule_value_str in members
            if operator == "not_equals": return rule_value_str not in members
            if operator == "contains": return any(rule_value_str in m for m in members)
            if operator == "not_contains": return all(rule_value_str not in m for m in members)
            return False
        ordered = _compare_ordered(item_value, operator, rule_value)
        if ordered is not None:
            return ordered
        item_value_str = str(item_value).lower()
        rule_value_str = str(rule_value).lower()
        if operator in ("equals", "not_equals"):
            equal = item_value_str == rule_value_str
            return equal if operator == "equals" else not equal
        if operator == "contains": return rule_value_str in item_value_str
        if operator == "not_contains": return rule_value_str not in item_value_str
    if operator == "is_empty": return item_value is None or item_value == '' or item_value == []
    if operator == "is_not_empty": return item_value is not None and item_value != '' and item_value != []
    return False

def _resolve_post_filter_field(item: Dict[str, Any], field: str) -> Any:
    """伪字段/非常规字段 -> 条目实际属性(按 Emby 4.10 实际返回结构取值)"""
    if field == "IsMovie": return item.get("Type") == "Movie"
    if field == "IsSeries": return item.get("Type") == "Series"
    if field == "IsPlayed": return (item.get("UserData") or {}).get("Played") is True
    if field == "IsUnplayed": return (item.get("UserData") or {}).get("Played") is not True
    if field == "HasSubtitles": return item.get("HasSubtitles") is True
    if field == "HasOfficialRating": return item.get("OfficialRating") is not None
    if field == "Tags":
        # 实测 Emby 4.10 条目 DTO 不返回 Tags 数组,标签在 TagItems 对象里
        tags = item.get("Tags")
        if not tags:
            tags = [t.get("Name") for t in (item.get("TagItems") or []) if isinstance(t, dict) and t.get("Name")]
        return tags or []
    if field == "Studios":
        # Studios 在 DTO 里是 {Name, Id} 对象数组,后筛选按名称比较
        return [s.get("Name") if isinstance(s, dict) else s for s in (item.get("Studios") or [])]
    return _get_nested_value(item, field)


def _apply_post_filter(items: List[Dict[str, Any]], post_filter_rules: List, match_all: bool = True) -> List[Dict[str, Any]]:
    """对完整数据集逐条评估规则;match_all=True 取全部满足(AND),False 取任一满足(OR)。

    注意:必须传入全量数据集再调用(本地全量模式的设计前提),
    在分页后的子集上调用会导致计数与翻页漂移。
    """
    if not post_filter_rules: return items
    combine = all if match_all else any
    logger.info(f"在 {len(items)} 个项目上应用 {len(post_filter_rules)} 条后筛选规则({('AND' if match_all else 'OR')})。")
    filtered_items = []
    for item in items:
        if combine(_check_condition(_resolve_post_filter_field(item, rule.field), rule.operator, rule.value) for rule in post_filter_rules):
            filtered_items.append(item)
    return filtered_items


# ================= 本地直出(访问加速) =================

def _extract_token(headers: Dict[str, str]) -> Optional[str]:
    for key, value in headers.items():
        if key.lower() == "x-emby-token" and value:
            return value
    # 部分客户端只在 X-Emby-Authorization 里携带令牌(Token="...")
    for key, value in headers.items():
        if key.lower() == "x-emby-authorization" and value:
            m = re.search(r'Token="([^"]+)"', value)
            if m:
                return m.group(1)
    return None


def _build_user_forward_headers(request_headers, params) -> Dict[str, str]:
    """按白名单收集用户请求头,并在缺失时从查询参数补齐令牌。

    Emby Web 等客户端把令牌放在 URL 查询参数(X-Emby-Token / api_key)而非请求头,
    只转发请求头会让后台请求无凭证,被 Emby 以 401 拒绝。
    """
    headers = {k: v for k, v in request_headers.items() if k.lower() in _FORWARDABLE_HEADERS}
    if not _extract_token(headers):
        token = next((v for k, v in params.items() if k.lower() in ("x-emby-token", "api_key")), None)
        if token:
            headers["X-Emby-Token"] = token
    return headers


def _build_resolution_params(config: AppConfig, vlib, api_token: Optional[str]) -> Tuple[Dict[str, Any], List, List, bool]:
    """构建虚拟库解析请求参数与本地评估规则,供全量拉取复用。

    返回 (拉取参数, 元数据后筛选规则, 用户态规则, 匹配方式):
    - 原生模式:可翻译规则进拉取参数,其余规则全部本地后筛选(历史行为);
    - 本地全量模式:拉取不带任何筛选参数(裸拉),元数据规则本地全量评估,
      用户态规则(IsPlayed/IsUnplayed)由直出服务时叠加用户状态后评估。
    """
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

    emby_native_params, post_filter_rules, user_state_rules, match_all = resolve_for_serving(config, vlib)
    new_params.update(emby_native_params)

    return new_params, post_filter_rules, user_state_rules, match_all


async def resolve_shared_metadata(config: AppConfig, vlib, user_id: str, session: ClientSession,
                                  real_emby_url: str, headers: Dict[str, str],
                                  proxy: Optional[str] = None) -> List[Dict[str, Any]]:
    """解析虚拟库共享元数据(规则过滤 + 高级筛选 + TMDB 合并),并剥离 UserData。

    共享元数据与用户无关,所有用户共用一份;可见性差异由用户叠加层在服务时裁剪。
    以传入身份查询(预热时为管理员 api_key,可见性最全);失败返回空列表。
    用户态规则(IsPlayed/IsUnplayed)在此不评估——共享元数据已剥离用户状态,
    由 try_serve_locally 在叠加用户 UserData 后评估。
    """
    try:
        new_params, post_filter_rules, _user_state_rules, match_all = _build_resolution_params(config, vlib, _extract_token(headers))
        search_url = f"{real_emby_url}/emby/Users/{user_id}/Items"

        all_items: List[Dict[str, Any]] = []
        start_index, limit = 0, 200
        while True:
            fetch_params = new_params.copy()
            fetch_params["StartIndex"] = str(start_index)
            fetch_params["Limit"] = str(limit)
            async with session.get(search_url, params=fetch_params, headers=headers, proxy=proxy) as resp:
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
            all_items = _apply_post_filter(all_items, post_filter_rules, match_all=match_all)

        if vlib.merge_by_tmdb_id or config.force_merge_by_tmdb_id:
            all_items = await handler_merger.merge_items_by_tmdb(all_items)

        for item in all_items:
            item.pop("UserData", None)

        logger.info(f"全量解析 '{vlib.name}' 完成,共 {len(all_items)} 个项目。")
        return all_items
    except Exception as e:
        logger.warning(f"解析虚拟库 '{vlib.name}' 完整列表时出错: {e}")
        return []


def get_shared_metadata(vlib_id: str) -> Optional[Dict[str, Any]]:
    """读取共享元数据(内存优先,未命中回退 SQLite 并回填)。"""
    entry = vlib_metadata_cache.get(vlib_id)
    if entry is None:
        persisted = db_manager.load_vlib_metadata(vlib_id)
        if persisted:
            entry = {"items": persisted[0], "updated_at": persisted[1]}
            vlib_metadata_cache[vlib_id] = entry
    return entry


def store_shared_metadata(vlib, items: List[Dict[str, Any]]) -> None:
    now = int(time.time())
    vlib_metadata_cache[vlib.id] = {"items": items, "updated_at": now}
    db_manager.save_vlib_metadata(vlib.id, items, now)


async def build_user_overlay_data(config: AppConfig, vlib, user_id: str, session: ClientSession,
                                  real_emby_url: str, headers: Dict[str, str],
                                  proxy: Optional[str] = None) -> Optional[Dict[str, dict]]:
    """基于共享元数据的条目 Id 分批拉取该用户的 UserData,返回 {条目ID: UserData}。

    返回 None 表示构建失败(调用方不应落盘);叠加层缺失的 Id 即该用户不可见的条目。
    """
    meta = get_shared_metadata(vlib.id)
    if meta is None:
        logger.warning(f"叠加层构建跳过:'{vlib.name}' 尚无共享元数据。")
        return None
    if not meta["items"]:
        return {}

    token = _extract_token(headers)
    url = f"{real_emby_url}/emby/Users/{user_id}/Items"
    ids = [i.get("Id") for i in meta["items"] if i.get("Id")]
    data: Dict[str, dict] = {}

    for chunk_start in range(0, len(ids), _SERVING_OVERLAY_CHUNK):
        chunk = ids[chunk_start:chunk_start + _SERVING_OVERLAY_CHUNK]
        params: Dict[str, Any] = {"Ids": ",".join(chunk), "EnableUserData": "true", "Limit": "2000"}
        if token:
            params["X-Emby-Token"] = token
        try:
            async with session.get(url, params=params, headers=headers, proxy=proxy) as resp:
                if resp.status != 200:
                    body_snippet = (await resp.text())[:200].replace("\n", " ")
                    logger.warning(f"叠加层构建失败:'{vlib.name}' 用户 {user_id} 批次 Status={resp.status} Body={body_snippet}")
                    return None
                for it in (await resp.json(content_type=None)).get("Items", []):
                    if it.get("Id"):
                        data[it["Id"]] = it.get("UserData") or {}
        except Exception as e:
            logger.warning(f"叠加层构建失败 ('{vlib.name}' 用户 {user_id}): {e}")
            return None
    return data


def get_user_overlay(vlib_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """读取用户叠加层(内存优先,未命中回退 SQLite 并回填)。"""
    key = (user_id, vlib_id)
    entry = user_overlay_cache.get(key)
    if entry is None:
        persisted = db_manager.load_vlib_user_overlay(user_id, vlib_id)
        if persisted:
            entry = {"data": persisted[0], "updated_at": persisted[1]}
            user_overlay_cache[key] = entry
    return entry


def store_user_overlay(vlib, user_id: str, data: Dict[str, dict]) -> None:
    now = int(time.time())
    user_overlay_cache[(user_id, vlib.id)] = {"data": data, "updated_at": now}
    db_manager.save_vlib_user_overlay(user_id, vlib.id, data, now)


async def build_and_store_user_overlay(config: AppConfig, vlib, user_id: str, session: ClientSession,
                                       real_emby_url: str, headers: Dict[str, str],
                                       proxy: Optional[str] = None) -> Optional[int]:
    """重建并持久化用户叠加层(预热链路使用),返回叠加条目数;失败返回 None。"""
    data = await build_user_overlay_data(config, vlib, user_id, session, real_emby_url, headers, proxy=proxy)
    if data is None:
        return None
    store_user_overlay(vlib, user_id, data)
    return len(data)


_refreshing_meta_keys = set()
_refreshing_overlay_keys = set()


def _refresh_metadata_background(config: AppConfig, vlib, user_id: str, session: ClientSession,
                                 real_emby_url: str, proxy: Optional[str] = None) -> None:
    """后台重建共享元数据(带去重锁;以管理员 api_key 身份,可见性最全)。"""
    if vlib.id in _refreshing_meta_keys:
        return
    _refreshing_meta_keys.add(vlib.id)

    async def _task():
        try:
            headers = {"Accept": "application/json", "X-Emby-Token": config.emby_api_key or ""}
            items = await resolve_shared_metadata(config, vlib, user_id, session, real_emby_url, headers, proxy=proxy)
            if items:
                store_shared_metadata(vlib, items)
                logger.info(f"后台重建共享元数据完成:'{vlib.name}' 共 {len(items)} 项。")
            else:
                logger.warning(f"后台重建共享元数据失败:'{vlib.name}' 解析结果为空。")
        except Exception as e:
            logger.warning(f"后台重建共享元数据出错 ('{vlib.name}'): {e}")
        finally:
            _refreshing_meta_keys.discard(vlib.id)

    asyncio.create_task(_task())


def _refresh_overlay_background(config: AppConfig, vlib, user_id: str, session: ClientSession,
                                real_emby_url: str, headers: Dict[str, str],
                                proxy: Optional[str] = None) -> None:
    """后台重建用户叠加层(带去重锁;以该用户身份拉取其 UserData)。"""
    key = (user_id, vlib.id)
    if key in _refreshing_overlay_keys:
        return
    _refreshing_overlay_keys.add(key)

    async def _task():
        try:
            data = await build_user_overlay_data(config, vlib, user_id, session, real_emby_url, headers, proxy=proxy)
            if data is None:
                logger.warning(f"后台重建用户叠加层失败 ('{vlib.name}' 用户 {user_id})。")
                return
            store_user_overlay(vlib, user_id, data)
            logger.info(f"后台重建用户叠加层完成:'{vlib.name}' 用户 {user_id} 共 {len(data)} 项状态。")
        except Exception as e:
            logger.warning(f"后台重建用户叠加层出错 ('{vlib.name}' 用户 {user_id}): {e}")
        finally:
            _refreshing_overlay_keys.discard(key)

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
                            request_headers, proxy: Optional[str] = None) -> Optional[Response]:
    """本地直出:命中分层缓存时在代理端叠加用户状态、排序、分页后直接返回,不再请求 Emby。

    共享元数据未命中/硬过期 → 回退实时查询并后台重建;
    用户叠加层未命中 → 回退实时查询并后台构建(该用户此后秒开);
    任意一层软过期 → 照常返回并后台刷新(stale-while-revalidate)。
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

    now = int(time.time())

    meta = get_shared_metadata(vlib.id)
    if meta is None:
        logger.info(f"直出元数据未命中('{vlib.name}'),本次实时查询并后台构建共享元数据。")
        _refresh_metadata_background(config, vlib, user_id, session, real_emby_url, proxy=proxy)
        return None
    meta_age = now - int(meta["updated_at"])
    if meta_age > _SERVING_HARD_TTL:
        logger.info(f"直出元数据已硬过期({meta_age // 3600}h),回退实时查询并后台重建。")
        _refresh_metadata_background(config, vlib, user_id, session, real_emby_url, proxy=proxy)
        return None

    overlay = get_user_overlay(vlib.id, user_id)
    if overlay is None:
        logger.info(f"用户叠加层未命中(用户 {user_id[:8]}…, '{vlib.name}'),本次实时查询并后台构建。")
        headers = _build_user_forward_headers(request_headers, params)
        _refresh_overlay_background(config, vlib, user_id, session, real_emby_url, headers, proxy=proxy)
        return None
    overlay_age = now - int(overlay["updated_at"])

    # 组装:元数据 ∩ 用户可见条目,并叠加用户自己的 UserData
    overlay_data = overlay["data"]
    items = []
    for item in meta["items"]:
        ud = overlay_data.get(item.get("Id"))
        if ud is None:
            continue  # 该用户不可见(权限差异或元数据滞后),自然裁剪
        if ud:
            item = {**item, "UserData": ud}
        items.append(item)

    # 本地全量模式:用户态规则(已播放/未播放)在叠加该用户播放状态后评估,
    # 每个用户看到的是自己的播放状态,而非管理员的
    _p, _meta_rules, user_state_rules, match_all = resolve_for_serving(config, vlib)
    if user_state_rules:
        logger.info(f"直出评估用户态规则:'{vlib.name}' 在 {len(items)} 个可见项上应用 {len(user_state_rules)} 条。")
        items = _apply_post_filter(items, user_state_rules, match_all=match_all)

    if meta_age > _SERVING_SOFT_TTL:
        logger.info(f"直出元数据已软过期({meta_age // 3600}h),照常返回并后台重建。")
        _refresh_metadata_background(config, vlib, user_id, session, real_emby_url, proxy=proxy)
    if overlay_age > _SERVING_SOFT_TTL:
        logger.info(f"用户叠加层已软过期({overlay_age // 3600}h),照常返回并后台重建。")
        headers = _build_user_forward_headers(request_headers, params)
        _refresh_overlay_background(config, vlib, user_id, session, real_emby_url, headers, proxy=proxy)

    logger.info(f"本地直出:'{vlib.name}' 命中分层缓存(元数据 {len(meta['items'])} 项 / 用户可见 {len(items)} 项)。")

    # IncludeItemTypes 本地过滤(元数据本身是 Movie,Series,Video 的并集)
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
        # 这些客户端通过 /Users/xxx/Views 获取根视图，且请求参数中不含任何 'Id'
        # 仅在视图类路径上触发;图片等其它无 Id 参数的请求直接放行转发
        if method == "GET" and "Users" in full_path and "/Views" in full_path:
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

    # 【【【核心优化点 2】】】: 应用高级筛选器(按执行模式分流)
    # 原生模式:可翻译规则进查询参数,其余规则当前页后筛选(历史行为);
    # 本地全量模式:不带筛选参数裸拉,全部规则由代理端对全量数据评估(见下方全量拉取分支)。
    # 实时路径携带用户令牌,响应自带该用户的 UserData,用户态规则同样可本地评估。
    adv_filter = find_vlib_filter(config, found_vlib)
    if found_vlib.advanced_filter_id and adv_filter is None:
        logger.warning(f"虚拟库配置了高级筛选器ID '{found_vlib.advanced_filter_id}'，但未找到。")
    post_filter_rules: List = []
    match_all = True
    is_local_exec = is_local_mode(adv_filter) if adv_filter is not None else False
    if adv_filter is not None:
        emby_native_params, post_filter_rules, match_all = resolve_for_user_query(config, found_vlib)
        new_params.update(emby_native_params)
        if is_local_exec:
            logger.info(f"筛选器 '{adv_filter.name}' 为本地全量模式:裸拉全量后本地评估 {len(post_filter_rules)} 条规则。")
        elif post_filter_rules:
            logger.info(f"筛选器 '{adv_filter.name}' 有 {len(post_filter_rules)} 条规则需要在代理端后筛选。")

    # 本地全量模式按共享元数据同款字段清单拉取,保证本地评估字段齐全
    if is_local_exec:
        required_fields = _SERVING_FETCH_FIELDS
    else:
        required_fields = ["ProviderIds", "Genres", "Tags", "Studios", "People", "OfficialRatings", "CommunityRating", "ProductionYear", "VideoRange", "Container"]
    if "Fields" in new_params:
        existing_fields = set(new_params["Fields"].split(','))
        missing_fields = [f for f in required_fields if f not in existing_fields]
        if missing_fields: new_params["Fields"] += "," + ",".join(missing_fields)
    else: new_params["Fields"] = ",".join(required_fields)

    new_params["Recursive"] = "true"
    # 条目类型与展示类型一致:混合库拉全集,电视剧/电影库只拉对应类型,
    # 与本地直出路径及客户端浏览请求(由 stub 的 CollectionType 决定)保持同一语义
    new_params["IncludeItemTypes"] = handler_views.get_vlib_include_item_types(found_vlib)

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
    served = await try_serve_locally(config, found_vlib, user_id, params, method, session, real_emby_url, request.headers, proxy=emby_proxy_of(request))
    if served is not None:
        return served

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

    # 本地全量模式,或启用 TMDB 合并且无后筛选规则时,走全量拉取:
    # 本地全量模式的全部规则必须对完整数据集评估,手动分页保证 TotalRecordCount 正确;
    # 原生模式的行为保持不变(有后筛选规则时仍在当前页执行,历史兼容)。
    use_full_fetch = is_local_exec or (is_tmdb_merge_enabled and not post_filter_rules)
    if not use_full_fetch:
        if is_tmdb_merge_enabled and post_filter_rules:
            logger.warning("TMDB合并已启用，但存在无法翻译的后筛选规则，合并将在当前页进行，可能不完整。")

        async with session.request(method, search_url, params=new_params, headers=headers_to_forward, proxy=emby_proxy_of(request)) as resp:
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

    # --- 全量拉取逻辑(本地全量模式 / TMDB合并) ---
    else:
        logger.info(f"{'本地全量模式' if is_local_exec else 'TMDB合并已启用'},开始获取全量数据...")
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
            async with session.request(method, search_url, params=fetch_params, headers=headers_to_forward, proxy=emby_proxy_of(request)) as resp:
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

        # 1. 本地评估筛选规则:先筛后合并,避免合并主条目的字段遮蔽本应命中的版本
        if post_filter_rules:
            all_items = _apply_post_filter(all_items, post_filter_rules, match_all=match_all)

        # 2. 本地全量模式:拉取固定为 Movie,Series,Video 全集,
        #    客户端请求的条目类型在本地过滤(与本地直出路径行为一致)
        if is_local_exec:
            client_types = params.get("IncludeItemTypes")
            if client_types:
                allowed_types = {t.strip() for t in client_types.split(",") if t.strip()}
                if allowed_types:
                    all_items = [i for i in all_items if i.get("Type") in allowed_types]

        # 3. 应用TMDB合并
        if is_tmdb_merge_enabled:
            logger.info("正在对获取到的全量数据集执行TMDB合并...")
            all_items = await handler_merger.merge_items_by_tmdb(all_items)

        # 4. 手动分页:TotalRecordCount 恒为筛选/合并后的真实总数
        total_record_count = len(all_items)
        try:
            start_idx = max(0, int(client_start_index))
        except (TypeError, ValueError):
            start_idx = 0
        limit_raw = params.get("Limit")
        try:
            limit_count = int(limit_raw) if limit_raw is not None else total_record_count
        except (TypeError, ValueError):
            limit_count = total_record_count
        paginated_items = all_items[start_idx : start_idx + limit_count]

        # 5. 构建最终的响应
        final_data = {
            "Items": paginated_items,
            "TotalRecordCount": total_record_count,
            "StartIndex": start_idx
        }
        logger.info(f"全量处理完成,手动分页返回。总数: {total_record_count}, 返回页面项目数: {len(paginated_items)}")

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
