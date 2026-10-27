# src/proxy_handlers/handler_episodes.py (修改后)

import asyncio
import json
import logging
import re
from fastapi import Request, Response
from aiohttp import ClientSession
from ._find_helper import find_all_series_by_tmdb_id, is_item_in_a_merge_enabled_vlib, resolve_tmdb_id_for_item  # noqa
from emby_net import emby_proxy_of
import config_manager
import proxy_cache

logger = logging.getLogger(__name__)

TMDB_API_BASE_URL = "https://api.themoviedb.org/3"

EPISODES_PATH_REGEX = re.compile(r"/Shows/([a-f0-9\-]+)/Episodes")

async def handle_episodes_merge(request: Request, full_path: str, session: ClientSession, real_emby_url: str) -> Response | None:
    match = EPISODES_PATH_REGEX.search(f"/{full_path}")
    season_id = request.query_params.get("SeasonId")
    if not match or not season_id: return None

    series_id_from_path = match.group(1)
    logger.info(f"EPISODES_HANDLER: 拦截到对剧集 {series_id_from_path} 下，季 {season_id} 的“集”请求。")

    params = request.query_params
    user_id = params.get("UserId")
    if not user_id: return Response(content="UserId not found", status_code=400)

    headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
    auth_token_param = {'X-Emby-Token': params.get('X-Emby-Token')} if 'X-Emby-Token' in params else {}

    # --- 合并结果缓存:命中后进详情页不再逐个剧集拉「季 + 集」,也不再请求 TMDB ---
    config = config_manager.load_config_readonly()
    show_missing = config.show_missing_episodes
    tmdb_api_key = config.tmdb_api_key
    tmdb_proxy = config.tmdb_proxy
    cache_key = (
        "episodes", user_id, series_id_from_path, season_id,
        params.get("Fields", ""), bool(show_missing),
    )
    if proxy_cache.merge_cache_enabled:
        cached_items = proxy_cache.merge_result_cache.get(cache_key)
        if cached_items is not None:
            logger.info(f"EPISODES_HANDLER: 合并结果缓存命中,直接返回 {len(cached_items)} 集。")
            return Response(
                content=json.dumps({"Items": cached_items, "TotalRecordCount": len(cached_items)}),
                status_code=200, media_type="application/json",
            )
    
    # --- 【【【 新增的资格检查 】】】 ---
    should_merge = await is_item_in_a_merge_enabled_vlib(
        session, real_emby_url, user_id, series_id_from_path, headers, auth_token_param,
        proxy=emby_proxy_of(request)
    )
    if not should_merge:
        return None
    # --- 资格检查结束 ---

    try:
        tmdb_id = await resolve_tmdb_id_for_item(session, real_emby_url, user_id, series_id_from_path, headers, auth_token_param, proxy=emby_proxy_of(request))

        season_url = f"{real_emby_url}/emby/Users/{user_id}/Items/{season_id}"
        season_params = {'Fields': 'IndexNumber', **auth_token_param}
        async with session.get(season_url, params=season_params, headers=headers, proxy=emby_proxy_of(request)) as resp:
            target_season_number = (await resp.json()).get("IndexNumber")

    except Exception as e:
        logger.error(f"EPISODES_HANDLER: 获取TMDB ID或季号失败: {e}"); return None

    if not tmdb_id or target_season_number is None: return None
    logger.info(f"EPISODES_HANDLER: 找到TMDB ID: {tmdb_id}，目标季号: {target_season_number}。")

    original_series_ids = await find_all_series_by_tmdb_id(session, real_emby_url, user_id, tmdb_id, headers, auth_token_param, proxy=emby_proxy_of(request))
    
    # 如果不显示缺失剧集，并且只有一个库，那么就没必要继续执行了
    if not show_missing and len(original_series_ids) < 2:
        return None
        
    logger.info(f"EPISODES_HANDLER: ✅ 找到 {len(original_series_ids)} 个关联剧集: {original_series_ids}。")

    async def fetch_episodes(series_id: str):
        seasons_url = f"{real_emby_url}/emby/Shows/{series_id}/Seasons"
        try:
            async with session.get(seasons_url, params=auth_token_param, headers=headers, proxy=emby_proxy_of(request)) as resp:
                seasons = (await resp.json()).get("Items", [])
            
            # 找到这个剧集里，与我们目标季号相同的那个季的ID
            matching_season = next((s for s in seasons if s.get("IndexNumber") == target_season_number), None)
            if not matching_season: return []

            episodes_url = f"{real_emby_url}/emby/Shows/{series_id}/Episodes"
            episode_params = dict(params)
            episode_params["SeasonId"] = matching_season.get("Id") # 使用正确的季ID
            # 移除分页参数，以获取所有集
            episode_params.pop("Limit", None)
            episode_params.pop("StartIndex", None)
            
            async with session.get(episodes_url, params=episode_params, headers=headers, proxy=emby_proxy_of(request)) as resp:
                return (await resp.json()).get("Items", []) if resp.status == 200 else []
        except Exception: return []

    tasks = [fetch_episodes(sid) for sid in original_series_ids]
    all_episodes = [ep for sublist in await asyncio.gather(*tasks) for ep in sublist]

    merged_episodes = {}
    server_id = None  # 用于存储 ServerId
    for episode in all_episodes:
        if not server_id:
            server_id = episode.get("ServerId")  # 从一个真实的剧集中获取 ServerId
        key = episode.get("IndexNumber")
        if key is not None and key not in merged_episodes:
            merged_episodes[key] = episode

    if show_missing:
        logger.info(f"EPISODES_HANDLER: '显示缺失剧集' 已开启，开始从 TMDB 获取信息。")
        tmdb_episodes = await fetch_tmdb_episodes(session, tmdb_api_key, tmdb_id, target_season_number, tmdb_proxy)
        
        if tmdb_episodes:
            # 获取当前剧集信息以用于填充缺失剧集
            series_info_url = f"{real_emby_url}/emby/Users/{user_id}/Items/{series_id_from_path}"
            series_info_params = {**auth_token_param}
            async with session.get(series_info_url, params=series_info_params, headers=headers, proxy=emby_proxy_of(request)) as resp:
                series_info = await resp.json()

            for tmdb_episode in tmdb_episodes:
                episode_number = tmdb_episode.get("episode_number")
                if episode_number is not None and episode_number not in merged_episodes:
                    missing_episode = {
                        "Name": tmdb_episode.get("name"),
                        "IndexNumber": episode_number,
                        "SeasonNumber": target_season_number,
                        "Id": f"tmdb_{tmdb_episode.get('id')}",
                        "Type": "Episode",
                        "IsFolder": False,
                        "UserData": {"Played": False},
                        "SeriesId": series_id_from_path,
                        "SeriesName": series_info.get("Name"),
                        "SeriesPrimaryImageTag": series_info.get("ImageTags", {}).get("Primary"),
                        "ImageTags": {
                            "Primary": "placeholder"
                        },
                        "PrimaryImageAspectRatio": 1.7777777777777777,
                        # --- 【【【 新增的关键字段 】】】 ---
                        "ServerId": server_id,
                        "Overview": tmdb_episode.get("overview"),
                        "PremiereDate": tmdb_episode.get("air_date"),
                    }
                    merged_episodes[episode_number] = missing_episode

    final_items = sorted(merged_episodes.values(), key=lambda x: x.get("IndexNumber", 0))
    logger.info(f"EPISODES_HANDLER: 合并完成。合并前总数: {len(all_episodes)}, 合并后最终数量: {len(final_items)}")

    if proxy_cache.merge_cache_enabled and final_items:
        proxy_cache.merge_result_cache[cache_key] = final_items

    return Response(content=json.dumps({"Items": final_items, "TotalRecordCount": len(final_items)}), status_code=200, media_type="application/json")

async def fetch_tmdb_episodes(session: ClientSession, api_key: str, tmdb_id: str, season_number: int, proxy: str | None = None):
    """从TMDB获取指定季的所有集信息"""
    if not api_key:
        logger.warning("TMDB_API_KEY not configured. Skipping TMDB fetch.")
        return []
    
    url = f"{TMDB_API_BASE_URL}/tv/{tmdb_id}/season/{season_number}?api_key={api_key}&language=zh-CN"
    try:
        async with session.get(url, proxy=proxy) as response:
            if response.status == 200:
                data = await response.json()
                return data.get("episodes", [])
            else:
                logger.error(f"Error fetching TMDB season details: {response.status}")
                return []
    except Exception as e:
        logger.error(f"Exception fetching TMDB season details: {e}")
        return []
