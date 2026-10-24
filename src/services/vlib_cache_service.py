# src/services/vlib_cache_service.py —— 虚拟库项目缓存主动预热
"""虚拟库项目缓存的定时预热与手动触发。

与 RSS 定时刷新同一模式:调度器只属于管理后台进程,到点后调用代理核心的
/api/internal/refresh-vlib-items 端点,由代理核心以模拟客户端身份走完整
规则解析并向 Emby 拉取项目列表(内存 + SQLite 双写)。

预热完成后顺带检查缺封面的库并触发自动生成,形成
"数据刷新 -> 缓存就绪 -> 封面补齐" 的流水线,全程不依赖客户端访问。
"""

import asyncio
import logging
import os
from typing import Optional

import aiohttp
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config_manager
from models import AppConfig

logger = logging.getLogger(__name__)

# 调度器只属于管理后台进程;代理核心进程不运行任何定时任务。
scheduler = AsyncIOScheduler()

_REFRESH_JOB_ID = "vlib_cache_refresh_job"


def _proxy_core_url() -> Optional[str]:
    return os.getenv("PROXY_CORE_URL")


async def refresh_vlib_cache(library_id: str) -> bool:
    """预热单个虚拟库的项目缓存;缓存就绪后为缺封面的库触发自动生成。"""
    proxy_core_url = _proxy_core_url()
    if not proxy_core_url:
        logger.warning("未配置 PROXY_CORE_URL,无法预热虚拟库缓存。")
        return False

    base = proxy_core_url.rstrip("/")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/api/internal/refresh-vlib-items/{library_id}",
                timeout=aiohttp.ClientTimeout(total=180),
            ) as resp:
                if resp.status != 200:
                    detail = (await resp.json(content_type=None)).get("detail", "未知错误")
                    logger.warning(f"预热虚拟库 {library_id} 缓存失败 (Status: {resp.status}): {detail}")
                    return False

                count = (await resp.json(content_type=None)).get("TotalRecordCount", 0)
                logger.info(f"虚拟库 {library_id} 缓存预热完成,共 {count} 个项目。")
    except Exception as e:
        logger.error(f"预热虚拟库 {library_id} 缓存时出错: {e}")
        return False

    # 缓存就绪后,缺封面的库顺势补生成(端点内部自带运行中去重)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base}/api/internal/autogen-cover/{library_id}",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    logger.info(f"已为虚拟库 {library_id} 触发缺封面自动生成检查。")
    except Exception as e:
        logger.warning(f"触发虚拟库 {library_id} 封面自动生成失败: {e}")

    return True


async def refresh_all_vlib_caches() -> None:
    """刷新所有虚拟库的项目缓存(定时任务入口),逐个串行避免压垮 Emby。"""
    logger.info("Starting scheduled vlib cache refresh...")
    config = config_manager.load_config()

    ok = 0
    for vlib in config.virtual_libraries:
        if await refresh_vlib_cache(vlib.id):
            ok += 1

    logger.info(f"Scheduled vlib cache refresh finished ({ok}/{len(config.virtual_libraries)} succeeded).")


# --- TMDB 索引定时重建(独立任务,间隔独立于列表预热) ---

_TMDB_INDEX_JOB_ID = "tmdb_index_refresh_job"


async def refresh_tmdb_index() -> None:
    """触发代理核心重建 TMDB 索引(端点内部自判开关与新鲜度,仍新鲜则跳过)。"""
    proxy_core_url = _proxy_core_url()
    if not proxy_core_url:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{proxy_core_url.rstrip('/')}/api/internal/rebuild-tmdb-index",
                timeout=aiohttp.ClientTimeout(total=600),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data.get("skipped"):
                        logger.info("TMDB 索引仍新鲜或已禁用,本轮跳过重建。")
                    else:
                        logger.info(f"TMDB 索引定时重建完成,共 {data.get('count')} 条。")
                else:
                    logger.warning(f"TMDB 索引定时重建失败 (Status: {resp.status})")
    except Exception as e:
        logger.warning(f"TMDB 索引定时重建出错: {e}")


def reschedule_tmdb_index_job(config: AppConfig) -> None:
    """按配置重建 TMDB 索引定时任务(开关关闭或间隔 0 均不调度)。"""
    if scheduler.get_job(_TMDB_INDEX_JOB_ID):
        scheduler.remove_job(_TMDB_INDEX_JOB_ID)

    if config.enable_tmdb_index and config.tmdb_index_refresh_interval and config.tmdb_index_refresh_interval > 0:
        scheduler.add_job(
            refresh_tmdb_index,
            'interval',
            hours=config.tmdb_index_refresh_interval,
            id=_TMDB_INDEX_JOB_ID,
            replace_existing=True
        )
        logger.info(f"TMDB index refresh job scheduled to run every {config.tmdb_index_refresh_interval} hours.")


def reschedule_refresh_job(config: AppConfig) -> None:
    """按配置重建定时任务(缓存功能关闭或间隔 0/空均视为禁用)。"""
    if scheduler.get_job(_REFRESH_JOB_ID):
        scheduler.remove_job(_REFRESH_JOB_ID)

    if config.enable_vlib_cache and config.vlib_cache_refresh_interval and config.vlib_cache_refresh_interval > 0:
        scheduler.add_job(
            refresh_all_vlib_caches,
            'interval',
            hours=config.vlib_cache_refresh_interval,
            id=_REFRESH_JOB_ID,
            replace_existing=True
        )
        logger.info(f"Vlib cache refresh job scheduled to run every {config.vlib_cache_refresh_interval} hours.")


async def clear_all_caches() -> dict:
    """清空虚拟库缓存:SQLite 持久层 + 代理核心内存,返回各类删除量。"""
    import db_manager
    removed_libraries = db_manager.clear_vlib_items_cache()
    removed_meta = db_manager.clear_vlib_metadata()
    removed_overlay = db_manager.clear_vlib_user_overlay()
    removed_index = db_manager.clear_tmdb_index()
    # 旧版按用户全量缓存表一并清理(历史数据)
    db_manager.clear_vlib_serving_cache()

    proxy_core_url = _proxy_core_url()
    if proxy_core_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_core_url.rstrip('/')}/api/internal/clear-vlib-cache",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"清空代理核心内存缓存失败 (Status: {resp.status})")
        except Exception as e:
            logger.warning(f"通知代理核心清空内存缓存时出错: {e}")

    logger.info(
        f"虚拟库缓存已清空(项目缓存 {removed_libraries} 库,共享元数据 {removed_meta},"
        f"用户叠加层 {removed_overlay},TMDB 索引 {removed_index},内存已通知清空)。"
    )
    return {"vlib_items": removed_libraries, "serving_rows": removed_meta + removed_overlay}


def start_scheduler() -> None:
    scheduler.start()


def shutdown_scheduler() -> None:
    scheduler.shutdown()
