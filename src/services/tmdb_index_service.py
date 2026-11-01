# src/services/tmdb_index_service.py —— TMDB 全库索引服务
"""全库 媒体项 → TMDB ID 索引的构建与维护。

移植自 Anime-Manager 的 emby_index_service 思路:把"按 TMDB ID 找条目"从
每次全库遍历变成查本地索引表 —— 索引由定时预热/启动预热全量重建,
日常查询配合一次 Ids 批量校验完成存活与用户可见性确认,失效条目由
下一轮全量重建清理。

多服聚合:索引覆盖主服 + 各从服(主服行裸 ID,从服行复合 ID {server}~{id},
主键天然不撞、反查直接可用)。索引为"构建账号视角",返回给具体用户前
必须经过调用方的可见性校验,不可直接信任。
"""

import logging
from typing import Dict, List

import config_manager
import db_manager
from services import server_context, credential_service

logger = logging.getLogger(__name__)

# 单页拉取条数
_INDEX_FETCH_PAGE = 500


async def rebuild_index_from_emby(config, session) -> int:
    """从主服 + 各从服全量拉取带 TMDB ID 的媒体项并重建索引,返回条目数;失败抛异常。

    从服条目以复合 ID({server}~{id})入库;单台失败警告跳过,不阻塞其它服务器。
    """
    entries: List[Dict[str, str]] = []

    for sid, server_cfg in server_context.iter_server_configs(config):
        label = sid or "主服(旧字段)"
        try:
            user_id = await server_context.resolve_server_user_id(server_cfg, session)
            headers = await credential_service.get_backend_headers(server_cfg, session=session)
        except Exception as e:
            logger.warning(f"TMDB 索引:跳过 {label}:凭证/用户解析失败: {e}")
            continue

        base = server_cfg.emby_url.rstrip("/")
        start, limit = 0, _INDEX_FETCH_PAGE
        server_count = 0
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
            try:
                from emby_net import emby_proxy_from_config
                async with session.get(f"{base}/emby/Items", params=params, headers=headers,
                                       proxy=emby_proxy_from_config(server_cfg)) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"索引重建批次请求失败 Status={resp.status}")
                    batch = (await resp.json(content_type=None)).get("Items", [])
            except Exception as e:
                logger.warning(f"TMDB 索引:{label} 拉取失败,该服条目跳过: {e}")
                break

            if not batch:
                break

            for item in batch:
                tmdb_id = (item.get("ProviderIds") or {}).get("Tmdb")
                if tmdb_id and item.get("Id"):
                    item_id = item.get("Id")
                    if sid:
                        item_id = server_context.encode_item_ref(sid, item_id)
                    entries.append({
                        "tmdb_id": str(tmdb_id),
                        "media_type": item.get("Type", ""),
                        "emby_item_id": item_id,
                        "title": item.get("Name", ""),
                    })
                    server_count += 1

            if len(batch) < limit:
                break
            start += len(batch)

        logger.info(f"TMDB 索引:{label} 拉取完成,共 {server_count} 条。")

    if not entries:
        logger.warning("TMDB 索引重建:所有服务器均无带 TMDB ID 的条目。")

    count = db_manager.replace_tmdb_index(entries)
    logger.info(f"TMDB 索引重建完成,共 {len(entries)} 条 (写库 {count})。")
    return len(entries)


async def rebuild_if_stale(config, session) -> Dict[str, object]:
    """按配置判定是否需要重建(开关关闭/仍然新鲜则跳过),需要时执行全量重建。

    返回 {"rebuilt": bool, "count": 当前条数, "reason": 跳过原因(可选)}。
    判定规则:
      - 开关关闭 → 跳过;
      - 索引为空 → 重建(功能可用的前提);
      - 重建间隔 > 0 且索引年龄达到间隔 → 重建;
      - 重建间隔 = 0 → 仅在索引为空时构建,之后靠查询自愈回写维持。
    """
    if not config.enable_tmdb_index:
        return {"rebuilt": False, "count": db_manager.count_tmdb_index(), "reason": "disabled"}

    count_now = db_manager.count_tmdb_index()
    age = db_manager.get_tmdb_index_age_seconds()
    interval = max(0, int(config.tmdb_index_refresh_interval or 0))

    need = count_now == 0 or age is None or (interval > 0 and age >= interval * 3600)
    if not need:
        return {"rebuilt": False, "count": count_now}

    count = await rebuild_index_from_emby(config, session)
    return {"rebuilt": True, "count": count}
