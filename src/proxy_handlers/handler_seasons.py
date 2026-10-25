# src/proxy_handlers/handler_seasons.py (修改后)

import asyncio
import json
import logging
import re
from fastapi import Request, Response
from aiohttp import ClientSession
from ._find_helper import find_all_series_by_tmdb_id, is_item_in_a_merge_enabled_vlib, resolve_tmdb_id_for_item  # noqa
from emby_net import emby_proxy_of

logger = logging.getLogger(__name__)

SEASONS_PATH_REGEX = re.compile(r"/Shows/([a-f0-9\-]+)/Seasons")

async def handle_seasons_merge(request: Request, full_path: str, session: ClientSession, real_emby_url: str) -> Response | None:
    match = SEASONS_PATH_REGEX.search(f"/{full_path}")
    if not match: return None

    representative_id = match.group(1)
    logger.info(f"SEASONS_HANDLER: 拦截到对剧集 {representative_id} 的“季”请求。")

    params = request.query_params
    user_id = params.get("UserId")
    if not user_id: return Response(content="UserId not found", status_code=400)

    headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
    auth_token_param = {'X-Emby-Token': params.get('X-Emby-Token')} if 'X-Emby-Token' in params else {}

    # --- 【【【 新增的资格检查 】】】 ---
    # 在执行任何昂贵的操作之前，先检查此剧集是否有资格进行合并
    should_merge = await is_item_in_a_merge_enabled_vlib(
        session, real_emby_url, user_id, representative_id, headers, auth_token_param,
        proxy=emby_proxy_of(request)
    )
    if not should_merge:
        # 如果检查不通过，立即返回 None，让默认处理器来处理这个请求
        return None
    # --- 资格检查结束 ---

    try:
        tmdb_id = await resolve_tmdb_id_for_item(session, real_emby_url, user_id, representative_id, headers, auth_token_param, proxy=emby_proxy_of(request))
    except Exception as e:
        logger.error(f"SEASONS_HANDLER: 获取TMDB ID失败: {e}"); return None

    if not tmdb_id: return None
    logger.info(f"SEASONS_HANDLER: 找到TMDB ID: {tmdb_id}。")

    original_series_ids = await find_all_series_by_tmdb_id(session, real_emby_url, user_id, tmdb_id, headers, auth_token_param, proxy=emby_proxy_of(request))
    if len(original_series_ids) < 2: return None
    logger.info(f"SEASONS_HANDLER: ✅ 找到 {len(original_series_ids)} 个关联剧集: {original_series_ids}。")

    async def fetch_seasons(series_id: str, proxy: str | None = None):
        url = f"{real_emby_url}/emby/Shows/{series_id}/Seasons"
        try:
            async with session.get(url, params=params, headers=headers, proxy=proxy) as resp:
                return (await resp.json()).get("Items", []) if resp.status == 200 else []
        except Exception: return []

    proxy = emby_proxy_of(request)
    tasks = [fetch_seasons(sid, proxy=proxy) for sid in original_series_ids]
    all_seasons = [s for sublist in await asyncio.gather(*tasks) for s in sublist]

    merged_seasons = {}
    for season in all_seasons:
        key = season.get("IndexNumber")
        if key is not None and key not in merged_seasons:
            merged_seasons[key] = season
    
    final_items = sorted(merged_seasons.values(), key=lambda x: x.get("IndexNumber", 0))
    logger.info(f"SEASONS_HANDLER: 合并完成。合并前总数: {len(all_seasons)}, 合并后最终数量: {len(final_items)}")

    return Response(content=json.dumps({"Items": final_items, "TotalRecordCount": len(final_items)}), status_code=200, media_type="application/json")
