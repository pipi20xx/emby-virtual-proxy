# src/services/log_service.py —— 跨进程日志汇聚
"""proxy 与 admin 是两个独立进程,内存中的 LogBroadcaster 互相不可见。

proxy 进程把日志写到按天文件(config/logs/proxy-YYYY-MM-DD.log,格式与实时流一致),
本服务在 admin 进程内尾随该文件,把新行原样汇入 LogBroadcaster,
使前端一条 WebSocket 即可收到双进程合并的实时日志。

尾随从文件末尾续传:admin 进程启动/任务异常重启/跨天都直接 seek 到文件末尾,
绝不重播已写入的历史(否则每次容器重启,前端都会把当天日志从头到尾刷一遍);
停机期间产生的日志由日志页向上滚动时的 read_history 分页覆盖。

read_history() 提供按天文件的分页读取(合并 proxy+admin,时间升序),
供日志页向上滚动加载更早内容——文件是唯一保留 7 天的完整来源。
"""

import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import app_paths
from logger import LogBroadcaster

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5

# --- 历史日志分页读取(实时增量走 /ws/system/logs,这里负责"更早的内容") ---

MAX_HISTORY_DAYS = 7  # 与 DailyFileHandler 的保留天数一致
MAX_HISTORY_LIMIT = 500  # 单次翻页行数上限,防止一次拖回全部
_RECORD_PREFIX_RE = re.compile(r"^\d{2}:\d{2}:\d{2} \| ")
_PREFIXES = ("proxy", "admin")

# 按天合并结果缓存:键为日期,值为(文件签名, 合并行列表);文件续写(当天)时签名变化自动失效
_day_cache: dict = {}


def _day_file(log_dir: Path, prefix: str, date_str: str) -> Path:
    return log_dir / f"{prefix}-{date_str}.log"


def _file_signature(log_dir: Path, date_str: str) -> tuple:
    sig = []
    for prefix in _PREFIXES:
        path = _day_file(log_dir, prefix, date_str)
        if path.exists():
            stat = path.stat()
            sig.append((prefix, stat.st_size, stat.st_mtime))
    return tuple(sig)


def _split_records(text: str) -> list:
    """把单文件内容拆成记录:每条以 "HH:MM:SS | " 开头,无前缀的续行并入上一条。

    多行日志(traceback 等)由此保持整体不被拆散;文件开头罕见的无前缀残行
    归入 key='' 的记录,排序时落在最前。返回 [(时间前缀, [行...]), ...]。
    """
    records: list = []
    head: list = []
    for line in text.splitlines():
        if _RECORD_PREFIX_RE.match(line):
            records.append((line[:8], [line]))
        elif records:
            records[-1][1].append(line)
        else:
            head.append(line)
    if head:
        records.insert(0, ("", head))
    return records


def _load_day_lines(log_dir: Path, date_str: str) -> list:
    """读取并按时间合并某一天的 proxy/admin 日志(行升序),带文件签名缓存。"""
    sig = _file_signature(log_dir, date_str)
    cached = _day_cache.get(date_str)
    if cached and cached[0] == sig:
        return cached[1]

    merged_records: list = []
    for prefix in _PREFIXES:
        path = _day_file(log_dir, prefix, date_str)
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        merged_records.extend(_split_records(text))
    # 稳定排序:同秒记录保持 proxy 在前、admin 在后的固定相对顺序
    merged_records.sort(key=lambda record: record[0])
    lines = [line for _, record_lines in merged_records for line in record_lines]

    _day_cache[date_str] = (sig, lines)
    return lines


def _valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _history_dates(log_dir: Path) -> list:
    """可用的日志日期(今天 + 文件里出现的),从新到旧,最多保留窗口内天数。"""
    dates = set()
    for path in log_dir.glob("*-*.log"):
        prefix, _, date_part = path.stem.partition("-")
        if prefix in _PREFIXES and date_part:
            dates.add(date_part)
    dates.add(datetime.now().strftime("%Y-%m-%d"))
    return sorted((d for d in dates if _valid_date(d)), reverse=True)[:MAX_HISTORY_DAYS]


def read_history(start_index: int = None, tail: int = None, limit: int = 100, log_dir=None) -> dict:
    """分页读取历史日志(proxy+admin 按天文件合并,时间升序)。

    - tail 模式:start_index 为空时返回最新 tail 行;
    - start_index 模式:返回"结束于 start_index(不含)"的最多 limit 行,
      即 [max(start_index-limit, 0), start_index)。

    翻页契约:start_index 是窗口的"结束游标",响应里的 next_start_index
    (= 本页第一行的全局索引)就是下一页的结束游标,为 None 表示已翻到最早。
    索引锚定在"最旧可用日志的第一行":日志文件只追加,翻页期间新写入的
    日志不会造成错位/重复;仅当日志超出保留期被清理时索引才会整体位移。

    返回 {lines, next_start_index, has_more}。
    """
    limit = max(1, min(int(limit or 100), MAX_HISTORY_LIMIT))
    log_dir = Path(log_dir) if log_dir else Path(app_paths.LOG_DIR)

    all_lines: list = []
    for date_str in reversed(_history_dates(log_dir)):  # 从旧到新拼接
        all_lines.extend(_load_day_lines(log_dir, date_str))

    if start_index is None:
        end = len(all_lines)
        start = max(end - max(int(tail or limit), 0), 0)
    else:
        end = max(int(start_index), 0)
        start = max(end - limit, 0)

    return {
        "lines": all_lines[start:end],
        "next_start_index": start if start > 0 else None,
        "has_more": start > 0,
    }


def _open_for_date(log_dir: Path, date_str: str):
    """二进制模式打开当天 proxy 日志。

    不用 text 模式:text 的 tell() 是不透明 cookie,与 os.path.getsize 的
    字节数比较不可靠;二进制下两者都是字节偏移,"文件被清空"的判断才准确。
    """
    path = log_dir / f"proxy-{date_str}.log"
    if not path.exists():
        return None
    return open(path, "rb")


def _pop_complete_lines(file, buffer: bytes):
    """读出新写入内容,只返回以换行结束的完整行(字节),半行留在缓冲区。"""
    chunk = file.read()
    if not chunk:
        return [], buffer
    buffer += chunk
    if buffer.endswith(b"\n"):
        lines, buffer = buffer[:-1].split(b"\n"), b""
    else:
        head, _, tail = buffer.rpartition(b"\n")
        lines, buffer = head.split(b"\n"), tail
    return lines, buffer


async def tail_proxy_logs() -> None:
    """尾随 proxy 当天日志文件,新行逐行转发进 LogBroadcaster。

    打开文件一律先 seek 到末尾(仅"文件被清空重建"例外):进程启动/任务
    异常重启/跨天都不重播历史,历史内容由 read_history 分页负责。
    每次连接文件都会打一条启动日志,便于确认当前运行的代码行为。
    """
    log_dir = Path(app_paths.LOG_DIR)
    current_date: str | None = None
    file_handle = None
    buffer = b""
    reopen_from_start = False  # 仅"文件被清空重建"时从 0 读,其余一律从末尾续传

    while True:
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            if today != current_date:
                # 首次/跨天:重置状态;文件此刻可能还没被 proxy 创建,交给下面的重试逻辑
                if file_handle is not None:
                    file_handle.close()
                    file_handle = None
                current_date = today
                buffer = b""
            elif file_handle is not None and os.path.getsize(file_handle.name) < file_handle.tell():
                # 文件被外部清空/重建:这才需要从 0 读
                file_handle.close()
                buffer = b""
                file_handle = None
                reopen_from_start = True

            if file_handle is None:
                file_handle = _open_for_date(log_dir, today)
                if file_handle is not None:
                    if reopen_from_start:
                        reopen_from_start = False
                    else:
                        file_handle.seek(0, os.SEEK_END)
                    logger.info(
                        f"日志尾随:已连接 proxy-{today}.log,从"
                        f"{'零' if file_handle.tell() == 0 else '末尾续传'}"
                        f"(跳过 {file_handle.tell() / 1024:.0f}KB 已有内容)"
                    )
                await asyncio.sleep(POLL_INTERVAL)
                continue

            lines, buffer = _pop_complete_lines(file_handle, buffer)
            if lines:
                for raw_line in lines:
                    line = raw_line.decode("utf-8", "replace").rstrip("\r")
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
            buffer = b""
            await asyncio.sleep(2)
