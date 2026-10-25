# src/proxy_handlers/_find_helper.py (TMDB 索引加速版)

import asyncio
import logging
from typing import Dict, List, Optional
from aiohttp import ClientSession

import config_manager
import db_manager
from emby_net import emby_proxy_of
from proxy_cache import vlib_metadata_cache

logger = logging.getLogger(__name__)

# Ids 批量校验的每批条目数(控制 URL 长度)
_VALIDATION_CHUNK = 150


def _is_item_in_merge_vlib_via_metadata(item_id: str) -> Optional[bool]:
    """用直出缓存里的虚拟库元数据快速判定条目是否属于合并库。

    返回 True(确认属于)/ False(所有合并库的元数据都在且不含此条目)/
    None(部分合并库没有元数据,无法判定,需走旧路径)。
    """
    config = config_manager.load_config()
    if config.force_merge_by_tmdb_id:
        return True

    merge_vlibs = [v for v in config.virtual_libraries if v.merge_by_tmdb_id]
    if not merge_vlibs:
        return False

    checked = 0
    for vlib in merge_vlibs:
        meta = vlib_metadata_cache.get(vlib.id)
        if meta is None:
            continue  # 该库元数据不在内存,交给旧路径确认
        checked += 1
        if any(i.get("Id") == item_id for i in meta["items"]):
            logger.info(f"MERGE_CHECK: ✅ 元数据命中:条目 {item_id} 位于合并库 '{vlib.name}'。")
            return True

    if checked == len(merge_vlibs):
        logger.info(f"MERGE_CHECK: ❌ 元数据判定:条目 {item_id} 不在任何合并库中。")
        return False
    return None


async def is_item_in_a_merge_enabled_vlib(
    session: ClientSession, real_emby_url: str, user_id: str, item_id: str, headers: Dict, auth_token_param: Dict,
    proxy: Optional[str] = None
) -> bool:
    """
    检查给定的 item_id 是否属于任何一个启用了 TMDB 合并功能的虚拟库。
    优先用直出缓存里的共享元数据零成本判定;无法判定时回退详情请求。
    """
    fast = _is_item_in_merge_vlib_via_metadata(item_id)
    if fast is not None:
        return fast

    config = config_manager.load_config()
    merge_vlibs = [vlib for vlib in config.virtual_libraries if vlib.merge_by_tmdb_id]

    item_details_url = f"{real_emby_url}/emby/Users/{user_id}/Items/{item_id}"
    item_params = {
        'Fields': 'CollectionIds,TagItems,GenreItems,Studios,People,ProviderIds',
        **auth_token_param
    }

    item = None
    try:
        async with session.get(item_details_url, params=item_params, headers=headers, proxy=proxy) as resp:
            if resp.status != 200:
                logger.warning(f"MERGE_CHECK: 无法获取项目 {item_id} 的详情。状态码: {resp.status}")
                return False
            item = await resp.json()
    except Exception as e:
        logger.error(f"MERGE_CHECK: 获取项目 {item_id} 详情时发生严重错误: {e}")
        return False

    if not item:
        return False

    for vlib in merge_vlibs:
        resource_type = vlib.resource_type
        resource_id_str = str(vlib.resource_id)
        match_found = False

        try:
            if resource_type == "collection":
                item_collection_ids = [str(col_id) for col_id in item.get("CollectionIds", [])]
                if resource_id_str in item_collection_ids:
                    match_found = True

            elif resource_type == "tag":
                item_tag_ids = [str(tag.get("Id")) for tag in item.get("TagItems", []) if tag.get("Id")]
                if resource_id_str in item_tag_ids:
                    match_found = True

            elif resource_type == "genre":
                item_genre_ids = [str(genre.get("Id")) for genre in item.get("GenreItems", []) if genre.get("Id")]
                if resource_id_str in item_genre_ids:
                    match_found = True

            elif resource_type == "studio":
                item_studio_ids = [str(studio.get("Id")) for studio in item.get("Studios", []) if studio.get("Id")]
                if resource_id_str in item_studio_ids:
                    match_found = True

            elif resource_type == "person":
                item_person_ids = [str(person.get("Id")) for person in item.get("People", []) if person.get("Id")]
                if resource_id_str in item_person_ids:
                    match_found = True

        except Exception as e:
            logger.error(f"MERGE_CHECK: 在检查虚拟库 '{vlib.name}' 时发生内部错误: {e}")
            continue

        if match_found:
            logger.info(f"MERGE_CHECK: ✅ 成功! 条目 {item_id} ('{item.get('Name')}') 位于合并库 '{vlib.name}'。允许合并。")
            return True

    logger.info(f"MERGE_CHECK: ❌ 拒绝。条目 {item_id} 未在 {len(merge_vlibs)} 个合并库中找到。")
    return False


async def _validate_ids_visible(
    session: ClientSession, real_emby_url: str, user_id: str, ids: List[str], headers: Dict, auth_token_param: Dict,
    proxy: Optional[str] = None
) -> List[str]:
    """批量校验条目对当前用户是否可见,返回实际可见的 Id 列表。"""
    visible: List[str] = []
    for start in range(0, len(ids), _VALIDATION_CHUNK):
        chunk = ids[start:start + _VALIDATION_CHUNK]
        params = {
            'Ids': ','.join(chunk),
            'EnableUserData': 'false',
            'Limit': '2000',
            'UserId': user_id,
            **auth_token_param,
        }
        try:
            async with session.get(f"{real_emby_url}/emby/Users/{user_id}/Items", params=params, headers=headers, proxy=proxy) as resp:
                if resp.status != 200:
                    logger.warning(f"TMDB_INDEX: Ids 校验批次失败 Status={resp.status}。")
                    return []
                for it in (await resp.json(content_type=None)).get("Items", []):
                    if it.get("Id"):
                        visible.append(it["Id"])
        except Exception as e:
            logger.warning(f"TMDB_INDEX: Ids 校验出错: {e}")
            return []
    return visible


async def find_all_series_by_tmdb_id(
    session: ClientSession, real_emby_url: str, user_id: str, tmdb_id: str, headers: Dict, auth_token_param: Dict,
    proxy: Optional[str] = None
) -> List[str]:
    """按 TMDB ID 查找全部关联剧集。

    索引开启时:查本地索引(毫秒级) → Ids 批量校验确认存活与用户可见性;
    索引未命中或校验全部失效时回退全局遍历,并把结果回写索引自愈。
    索引关闭时:直接走旧的全局遍历方案(不读写索引)。
    """
    if not config_manager.load_config().enable_tmdb_index:
        return await _find_all_series_by_tmdb_id_traversal(session, real_emby_url, user_id, tmdb_id, headers, auth_token_param, proxy=proxy)

    entries = db_manager.find_items_by_tmdb(tmdb_id, "Series")
    if entries:
        candidate_ids = [e["emby_item_id"] for e in entries]
        visible_ids = await _validate_ids_visible(session, real_emby_url, user_id, candidate_ids, headers, auth_token_param, proxy=proxy)
        if visible_ids:
            logger.info(f"TMDB_INDEX: 索引命中 tmdb_id={tmdb_id},候选 {len(candidate_ids)},用户可见 {len(visible_ids)}。")
            return list(set(visible_ids))
        # 索引有但校验全失效:可能索引过期,回退遍历兜底(下一轮预热会重建索引)
        logger.info(f"TMDB_INDEX: 索引条目全部校验失效 (tmdb_id={tmdb_id}),回退全局遍历。")

    found_ids = await _find_all_series_by_tmdb_id_traversal(session, real_emby_url, user_id, tmdb_id, headers, auth_token_param, proxy=proxy)

    if found_ids:
        # 自愈回写:把遍历结果写进索引,后续请求直接走索引
        db_manager.upsert_tmdb_index_entries([
            {"tmdb_id": tmdb_id, "media_type": "Series", "emby_item_id": sid, "title": ""}
            for sid in found_ids
        ])
        logger.info(f"TMDB_INDEX: 遍历结果已回写索引 ({len(found_ids)} 条)。")

    return found_ids


async def _find_all_series_by_tmdb_id_traversal(
    session: ClientSession, real_emby_url: str, user_id: str, tmdb_id: str, headers: Dict, auth_token_param: Dict,
    proxy: Optional[str] = None
) -> List[str]:
    """旧方案兜底:全库遍历所有剧集后按 TMDB ID 过滤(仅索引未命中时使用)。"""
    search_url = f"{real_emby_url}/emby/Items"
    search_params = {
        'Recursive': 'true',
        'IncludeItemTypes': 'Series',
        'Fields': 'ProviderIds',
        'HasTmdbId': 'true',
        'UserId': user_id,
        **auth_token_param
    }
    logger.info(f"TMDB_INDEX: 执行全局剧集遍历兜底搜索 (TMDB ID: {tmdb_id})")
    try:
        async with session.get(search_url, params=search_params, headers=headers, timeout=120, proxy=proxy) as resp:
            if resp.status == 200:
                data = await resp.json()
                all_series = data.get("Items", [])
                found_ids = [
                    item.get("Id") for item in all_series
                    if str(item.get("ProviderIds", {}).get("Tmdb")) == str(tmdb_id)
                ]
                return list(set(found_ids))
            else:
                logger.error(f"全局遍历搜索失败，状态码: {resp.status}，响应: {await resp.text()}")
                return []
    except Exception as e:
        logger.error(f"全局遍历搜索时发生异常: {e}")
        return []


async def resolve_tmdb_id_for_item(
    session: ClientSession, real_emby_url: str, user_id: str, item_id: str, headers: Dict, auth_token_param: Dict,
    proxy: Optional[str] = None
) -> Optional[str]:
    """获取条目的 TMDB ID:索引开启时优先反查索引(零网络请求),未索引时回退详情请求并回写;
    索引关闭时直接走详情请求。"""
    if config_manager.load_config().enable_tmdb_index:
        tmdb_id = db_manager.get_tmdb_id_for_item(item_id)
        if tmdb_id:
            return tmdb_id
        writeback = True
    else:
        writeback = False

    item_url = f"{real_emby_url}/emby/Users/{user_id}/Items/{item_id}"
    try:
        async with session.get(item_url, params={'Fields': 'ProviderIds', **auth_token_param}, headers=headers, proxy=proxy) as resp:
            if resp.status != 200:
                return None
            item = await resp.json()
            tmdb_id = (item.get("ProviderIds") or {}).get("Tmdb")
            if tmdb_id and writeback:
                db_manager.upsert_tmdb_index_entries([{
                    "tmdb_id": tmdb_id,
                    "media_type": item.get("Type", "Series"),
                    "emby_item_id": item_id,
                    "title": item.get("Name", ""),
                }])
            return tmdb_id
    except Exception as e:
        logger.error(f"获取条目 {item_id} 的 TMDB ID 失败: {e}")
        return None