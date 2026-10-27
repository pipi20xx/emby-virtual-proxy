# src/logger.py —— 进程内日志采集(实时广播 + 按天文件)
"""参考 Anime-Manager backend/logger.py 的方案,按本项目双进程结构精简:
    1. 控制台 StreamHandler:格式与各进程原 basicConfig 一致,stdout 直通 docker logs,行为不变。
    2. BroadcastHandler -> LogBroadcaster:实时流,格式 "HH:MM:SS | LEVEL | 消息";
       proxy 进程内无人订阅,广播自然丢弃,无副作用。
    3. DailyFileHandler(LOG_DIR/{process_name}-YYYY-MM-DD.log):按天滚动保留 7 天;
       文件与实时流共用同一格式,admin 进程尾随 proxy 文件逐行转发即可汇聚进同一条实时流。

    **写盘与控制台输出走后台线程**(logging.handlers.QueueListener):
    原先一行日志要在事件循环线程里同步格式化多次、写文件一次、写 stdout 一次,
    DEBUG 级别下这会成为高并发时的稳定 CPU/IO 开销,磁盘慢时会直接卡住请求。

    另重定向 sys.stdout/stderr 进 root logger,捕获 print() 与第三方库输出;
    控制台 handler 先持有原 stdout 引用,不会递归。
"""

import asyncio
import logging
import os
import queue
import sys
import time
from collections import deque
from datetime import datetime
from logging.handlers import QueueHandler as _StdQueueHandler, QueueListener

import app_paths

LOG_DIR = str(app_paths.LOG_DIR)
HISTORY_SIZE = 1000  # 内存回填缓冲条数,新 WS 连接建立时直接回填
SUBSCRIBER_QUEUE_SIZE = 2000  # 单个订阅者积压上限,超出即丢弃(慢客户端不拖垮广播者)

# 持有 QueueListener 引用:被 GC 回收会让后台写线程停摆
_listeners: list = []


def apply_tz_env() -> str:
    """应用 TZ 环境变量指定的时区。

    Linux 容器内 TZ 在进程启动后需显式 tzset() 才对 time.localtime /
    datetime.now() 生效(日志时间戳与按天日志切分都依赖它们);
    Windows 无 tzset,系统本地时区即最终行为,直接跳过。

    返回生效的 TZ 值(未设置返回空串),供启动日志说明。
    """
    tz = os.environ.get("TZ", "").strip()
    if tz and hasattr(time, "tzset"):
        os.environ["TZ"] = tz
        time.tzset()
    return tz


class LogBroadcaster:
    """实时广播管理器:订阅者 asyncio.Queue 集合 + 环形历史缓冲。"""

    _subscribers: set = set()
    _history: deque = deque(maxlen=HISTORY_SIZE)
    _main_loop = None  # 主事件循环引用(工作线程日志回投用)

    @classmethod
    def set_loop(cls, loop):
        cls._main_loop = loop

    @classmethod
    def subscribe(cls) -> asyncio.Queue:
        q = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        cls._subscribers.add(q)
        return q

    @classmethod
    def unsubscribe(cls, q) -> None:
        cls._subscribers.discard(q)

    @classmethod
    def history_snapshot(cls) -> list:
        return list(cls._history)

    @classmethod
    async def broadcast(cls, message: str) -> None:
        cls._history.append(message)
        for q in list(cls._subscribers):
            try:
                q.put_nowait(message)
            except Exception:
                # QueueFull 等:慢客户端丢弃新消息,绝不阻塞或积压
                pass


class BroadcastHandler(logging.Handler):
    """把日志记录投递到 LogBroadcaster;无事件循环的工作线程回投主循环。

    (原名 QueueHandler,与 logging.handlers.QueueHandler 重名,已改名以免混淆)
    """

    def emit(self, record):
        try:
            message = self.format(record).strip()
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(LogBroadcaster.broadcast(message))
            except RuntimeError:
                main_loop = LogBroadcaster._main_loop
                if main_loop is not None and main_loop.is_running():
                    asyncio.run_coroutine_threadsafe(LogBroadcaster.broadcast(message), main_loop)
        except Exception:
            pass


class DailyFileHandler(logging.FileHandler):
    """始终写 {prefix}-YYYY-MM-DD.log,跨天自动切换并清理超出保留天数的旧文件。"""

    def __init__(self, dirname, prefix: str, backup_count=7, encoding="utf-8"):
        self.dirname = dirname
        self.prefix = prefix
        self.backup_count = backup_count
        self.encoding = encoding
        self.current_date = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(self.dirname, f"{prefix}-{self.current_date}.log")
        super().__init__(log_file, encoding=encoding)

    def emit(self, record):
        new_date = datetime.now().strftime("%Y-%m-%d")
        if new_date != self.current_date:
            self.current_date = new_date
            self.stream.close()
            self.baseFilename = os.path.abspath(os.path.join(self.dirname, f"{self.prefix}-{new_date}.log"))
            self.stream = self._open()
            self._cleanup_old_logs()
        super().emit(record)

    def _cleanup_old_logs(self):
        try:
            files = sorted(
                (f for f in os.listdir(self.dirname) if f.startswith(f"{self.prefix}-") and f.endswith(".log")),
                reverse=True,
            )
            for old_file in files[self.backup_count:]:
                os.remove(os.path.join(self.dirname, old_file))
        except Exception:
            pass


class _StreamToLogger:
    """把 stdout/stderr 写入重定向到 logger(按行)。

    日志系统自身故障(如磁盘满)绝不反噬业务代码:写失败一律静默丢弃。
    """

    def __init__(self, logger, level):
        self.logger = logger
        self.level = level

    def write(self, buf):
        try:
            for line in buf.rstrip().splitlines():
                self.logger.log(self.level, line.rstrip())
        except Exception:
            pass

    def flush(self):
        try:
            pass
        except Exception:
            pass


def init_logging(process_name: str, level=logging.INFO, console_format="%(asctime)s - %(levelname)s - %(message)s") -> None:
    """初始化 root logger:控制台 + 实时广播 + 按天文件(写盘在后台线程)。"""
    tz_name = apply_tz_env()
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    # 1. 控制台与按天文件:真正干活的两个 target,交给 QueueListener 在后台线程执行
    console_target = logging.StreamHandler(sys.stdout)
    console_target.setFormatter(logging.Formatter(console_format))

    # 实时流与按天文件共用同一格式,前端按 "HH:MM:SS | LEVEL | 消息" 解析
    stream_formatter = logging.Formatter("%(asctime)s | %(levelname)-5s | %(message)s", datefmt="%H:%M:%S")

    file_target = DailyFileHandler(LOG_DIR, prefix=process_name, backup_count=7, encoding="utf-8")
    file_target.setFormatter(stream_formatter)

    log_queue = queue.SimpleQueue()
    # 队列 handler 不设 formatter:格式化留到后台线程由 target 的 formatter 完成
    queue_handler = _StdQueueHandler(log_queue)
    root.addHandler(queue_handler)

    listener = QueueListener(log_queue, console_target, file_target, respect_handler_level=True)
    listener.start()
    _listeners.append(listener)

    # 2. 实时广播(内存内、无磁盘 IO;proxy 进程无订阅者时自然丢弃)
    broadcast_handler = BroadcastHandler()
    broadcast_handler.setFormatter(stream_formatter)
    root.addHandler(broadcast_handler)

    # uvicorn 的 logger 不向 root 传播,显式挂同一套 handler 才能捕获 access 日志;
    # uvicorn.error 无自有 handler、向 "uvicorn" 传播,这里不能重复挂,否则每条记录处理两次
    for logger_name in ("uvicorn", "uvicorn.access"):
        uv_logger = logging.getLogger(logger_name)
        uv_logger.addHandler(queue_handler)
        uv_logger.addHandler(broadcast_handler)

    # 重定向 stdout/stderr,捕获 print() 与库输出(必须在 console_target 建好之后)
    sys.stdout = _StreamToLogger(root, logging.INFO)
    sys.stderr = _StreamToLogger(root, logging.ERROR)

    # 时区生效情况写入日志流,容器时间不对时一眼可见
    offset = datetime.now().astimezone().strftime("%z") or "+0000"
    logging.getLogger(__name__).info(f"日志时区: {tz_name or '系统默认'} (UTC{offset}),当前时间 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


def set_level(level: int) -> None:
    """运行期调整 root 日志级别(启动后按 config.log_level 应用)。"""
    logging.getLogger().setLevel(level)
