# src/services/tmdb_index_service.py —— TMDB 全库索引服务
"""全库 媒体项 → TMDB ID 索引的构建与维护。

移植自 Anime-Manager 的 emby_index_service 思路:把"按 TMDB ID 找条目"从
每次全库遍历变成查本地索引表 —— 索引由定时预热/启动预热全量重建,
日常查询配合一次 Ids 批量校验完成存活与用户可见性确认,失效条目由
下一轮全量重建清理。

索引为"全库视角"(管理员 api_key 构建),返回给具体用户前必须经过
调用方的可见性校验,不可直接信任。
"""

import logging
from typing import Dict, List

import config_manager
import db_manager

logger = logging.getLogger(__name__)

# 单页拉取条数
_INDEX_FETCH_PAGE = 500


async def rebuild_index_from_emby(session, real_emby_url: str, headers: Dict[str, str],
                                  proxy: str | None = None) -> int:
    """从 Emby 全量拉取带 TMDB ID 的媒体项并重建索引,返回条目数;失败抛异常。"""
    entries: List[Dict[str, str]] = []
    start, limit = 0, _INDEX_FETCH_PAGE

    while True:
        params = {
            "Recursive": "true",
            "IncludeItemTypes": "Movie,Series",
            "Fields": "ProviderIds",
            "EnableUserData": "false",
            "HasTmdbId": "true",
            "StartIndex": str(start),
            "Limit": str(limit),
        }
        async with session.get(f"{real_emby_url}/emby/Items", params=params, headers=headers,
                               proxy=proxy) as resp:
            if resp.status != 200:
                raise RuntimeError(f"索引重建批次请求失败 Status={resp.status}")
            batch = (await resp.json(content_type=None)).get("Items", [])

        if not batch:
            break

        for item in batch:
            tmdb_id = (item.get("ProviderIds") or {}).get("Tmdb")
            if tmdb_id and item.get("Id"):
                entries.append({
                    "tmdb_id": str(tmdb_id),
                    "media_type": item.get("Type", ""),
                    "emby_item_id": item.get("Id"),
                    "title": item.get("Name", ""),
                })

        if len(batch) < limit:
            break
        start += len(batch)

    if not entries:
        logger.warning("TMDB 索引重建:Emby 库中没有任何带 TMDB ID 的条目。")

    count = db_manager.replace_tmdb_index(entries)
    logger.info(f"TMDB 索引重建完成,共 {len(entries)} 条 (写库 {count})。")
    return len(entries)


async def rebuild_if_stale(session, real_emby_url: str, headers: Dict[str, str]) -> Dict[str, object]:
    """按配置判定是否需要重建(开关关闭/仍然新鲜则跳过),需要时执行全量重建。

    返回 {"rebuilt": bool, "count": 当前条数, "reason": 跳过原因(可选)}。
    判定规则:
      - 开关关闭 → 跳过;
      - 索引为空 → 重建(功能可用的前提);
      - 重建间隔 > 0 且索引年龄达到间隔 → 重建;
      - 重建间隔 = 0 → 仅在索引为空时构建,之后靠查询自愈回写维持。
    """
    config = config_manager.load_config()

    if not config.enable_tmdb_index:
        return {"rebuilt": False, "count": db_manager.count_tmdb_index(), "reason": "disabled"}

    count_now = db_manager.count_tmdb_index()
    age = db_manager.get_tmdb_index_age_seconds()
    interval = max(0, int(config.tmdb_index_refresh_interval or 0))

    need = count_now == 0 or age is None or (interval > 0 and age >= interval * 3600)
    if not need:
        return {"rebuilt": False, "count": count_now}

    count = await rebuild_index_from_emby(session, real_emby_url, headers,
                                          proxy=(config.emby_proxy or "").strip() or None)
    return {"rebuilt": True, "count": count}
