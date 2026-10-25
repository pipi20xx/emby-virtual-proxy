# src/services/log_service.py —— 跨进程日志汇聚
"""proxy 与 admin 是两个独立进程,内存中的 LogBroadcaster 互相不可见。

proxy 进程把日志写到按天文件(config/logs/proxy-YYYY-MM-DD.log,格式与实时流一致),
本服务在 admin 进程内尾随该文件,把新行原样汇入 LogBroadcaster,
使前端一条 WebSocket 即可收到双进程合并的实时日志。
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

import app_paths
from logger import LogBroadcaster

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5


def _open_for_date(log_dir: Path, date_str: str):
    path = log_dir / f"proxy-{date_str}.log"
    if not path.exists():
        return None
    return open(path, "r", encoding="utf-8", errors="replace")


def _pop_complete_lines(file, buffer: str):
    """读出新写入内容,只返回以换行结束的完整行,半行留在缓冲区。"""
    text = file.read()
    if not text:
        return [], buffer
    buffer += text
    if buffer.endswith("\n"):
        lines, buffer = buffer[:-1].split("\n"), ""
    else:
        head, _, tail = buffer.rpartition("\n")
        lines, buffer = head.split("\n"), tail
    return lines, buffer


async def tail_proxy_logs() -> None:
    """尾随 proxy 当天日志文件,新行逐行转发进 LogBroadcaster。"""
    log_dir = Path(app_paths.LOG_DIR)
    current_date: str | None = None
    file_handle = None
    buffer = ""

    while True:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != current_date:
                # 跨天:旧文件剩余的半行内容不足为道,直接切换到新文件
                if file_handle is not None:
                    file_handle.close()
                    file_handle = None
                current_date = today
                buffer = ""
                file_handle = _open_for_date(log_dir, today)
            elif file_handle is not None and os.path.getsize(file_handle.name) < file_handle.tell():
                # 文件被外部清空/重建,从头再读
                file_handle.close()
                buffer = ""
                file_handle = _open_for_date(log_dir, today)

            if file_handle is None:
                await asyncio.sleep(POLL_INTERVAL)
                continue

            lines, buffer = _pop_complete_lines(file_handle, buffer)
            if lines:
                for line in lines:
                    line = line.rstrip("\r")
                    if line.strip():
                        await LogBroadcaster.broadcast(line)
            else:
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            if file_handle is not None:
                file_handle.close()
            raise
        except Exception:
            logger.exception("proxy 日志尾随任务异常,稍后重试")
            if file_handle is not None:
                try:
                    file_handle.close()
                except Exception:
                    pass
            file_handle = None
            buffer = ""
            await asyncio.sleep(2)
            file_handle = _open_for_date(log_dir, current_date or datetime.now().strftime("%Y-%m-%d"))
