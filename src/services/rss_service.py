# src/services/rss_service.py —— RSS 虚拟库刷新与定时调度
"""RSS 虚拟库的刷新逻辑与 APScheduler 定时任务管理。

数据处理器在 rss_processor/(豆瓣/Bangumi 各自实现,内部为同步阻塞代码);
代理核心侧的 RSS 条目服务在 proxy_handlers/handler_rss.py,二者职责不同:
本模块负责"把数据写进 rss_library_items.db",handler_rss 负责"把数据读出来给客户端"。
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config_manager
from models import AppConfig, VirtualLibrary

logger = logging.getLogger(__name__)

# 调度器只属于管理后台进程;代理核心进程不运行任何定时任务。
scheduler = AsyncIOScheduler()

_REFRESH_JOB_ID = "rss_refresh_job"


def _get_processor(vlib: VirtualLibrary):
    """按 RSS 类型创建对应处理器(懒加载,保持与原实现一致)。"""
    if vlib.rss_type == "douban":
        from rss_processor.douban import DoubanProcessor
        return DoubanProcessor(vlib)
    if vlib.rss_type == "bangumi":
        from rss_processor.bangumi import BangumiProcessor
        return BangumiProcessor(vlib)
    return None


async def refresh_library(vlib: VirtualLibrary) -> None:
    """刷新单个 RSS 虚拟库。

    处理器内部是同步阻塞实现(requests + sqlite),
    放入线程池执行,避免长时间阻塞管理后台的事件循环。
    """
    try:
        processor = _get_processor(vlib)
        if processor is None:
            logger.warning(f"Unknown RSS type: {vlib.rss_type} for library {vlib.id}")
            return
        await asyncio.to_thread(processor.process)
    except Exception as e:
        logger.error(f"Error refreshing RSS library {vlib.id}: {e}", exc_info=True)


async def refresh_all_libraries() -> None:
    """刷新所有 RSS 虚拟库(定时任务入口)。"""
    logger.info("Starting scheduled RSS library refresh...")
    config = config_manager.load_config()
    rss_libraries = [lib for lib in config.virtual_libraries if lib.resource_type == "rsshub"]

    for vlib in rss_libraries:
        logger.info(f"Refreshing RSS library: {vlib.name} ({vlib.id})")
        await refresh_library(vlib)

    logger.info("Scheduled RSS library refresh finished.")


def reschedule_refresh_job(config: AppConfig) -> None:
    """按配置重建定时任务(rss_refresh_interval 小时;0/空表示禁用)。"""
    if scheduler.get_job(_REFRESH_JOB_ID):
        scheduler.remove_job(_REFRESH_JOB_ID)

    if config.rss_refresh_interval and config.rss_refresh_interval > 0:
        scheduler.add_job(
            refresh_all_libraries,
            'interval',
            hours=config.rss_refresh_interval,
            id=_REFRESH_JOB_ID,
            replace_existing=True
        )
        logger.info(f"RSS refresh job scheduled to run every {config.rss_refresh_interval} hours.")


def start_scheduler() -> None:
    scheduler.start()


def shutdown_scheduler() -> None:
    scheduler.shutdown()
