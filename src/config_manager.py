# src/config_manager.py —— config.json 的唯一读写入口
"""带 1 秒 TTL 进程内缓存 + 原子写入的配置管理。

- load_config():代理核心每个请求都会调用本函数。缓存把磁盘 IO 从
  "每请求一次"降为"最多每秒一次";管理后台保存后,代理进程最迟
  1 秒内读到新配置(与人工操作感知一致)。
  返回值始终是深拷贝,调用方可放心就地修改后再 save_config()。
- save_config():先写临时文件再 os.replace 原子替换,
  避免另一进程恰好读到写到一半的 JSON。保存失败会上抛,由路由层返回错误。
"""

import json
import logging
import os
import threading
import time

from models import AppConfig
from app_paths import CONFIG_DIR, CONFIG_FILE_PATH

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 1.0
# RLock:默认配置首次落盘时 save_config 会在持有锁的加载流程内被再次调用
_cache_lock = threading.RLock()
_cache = {"config": None, "loaded_at": 0.0}


def _read_from_disk():
    """从磁盘读取并解析配置;失败返回 None(由调用方决定回退策略)。"""
    try:
        CONFIG_DIR.mkdir(exist_ok=True)

        if not CONFIG_FILE_PATH.is_file():
            logger.info("Config file not found. Creating a new one with default values.")
            default_config = AppConfig()
            save_config(default_config)
            return default_config

        with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 兼容旧配置文件:缺失的新字段补默认值
            if isinstance(data, dict):
                data.setdefault('advanced_filters', [])
                data.setdefault('show_missing_episodes', False)
                data.setdefault('tmdb_api_key', "")
                data.setdefault('tmdb_proxy', "")
                data.setdefault('emby_proxy', "")
            return AppConfig.model_validate(data)

    except Exception as e:
        logger.error(f"Error loading or parsing config file: {e}")
        return None


def load_config(force_reload: bool = False) -> AppConfig:
    now = time.monotonic()
    cached = _cache["config"]
    if not force_reload and cached is not None and now - _cache["loaded_at"] < _CACHE_TTL_SECONDS:
        return cached.model_copy(deep=True)

    with _cache_lock:
        # 双重检查:等锁期间可能已被其他线程刷新
        now = time.monotonic()
        cached = _cache["config"]
        if not force_reload and cached is not None and now - _cache["loaded_at"] < _CACHE_TTL_SECONDS:
            return cached.model_copy(deep=True)

        config = _read_from_disk()
        if config is None:
            # 磁盘读取失败:优先回退到最近一次成功加载的配置,
            # 避免瞬态 IO 错误导致整个代理"以为"配置被清空。
            if _cache["config"] is not None:
                logger.warning("Falling back to the last successfully loaded config.")
                return _cache["config"].model_copy(deep=True)
            logger.warning("Returning a temporary default config.")
            return AppConfig()

        _cache["config"] = config
        _cache["loaded_at"] = now
        return config.model_copy(deep=True)


def save_config(config: AppConfig) -> None:
    try:
        CONFIG_DIR.mkdir(exist_ok=True)

        payload = config.model_dump_json(by_alias=True, indent=4)
        tmp_path = CONFIG_FILE_PATH.with_suffix(".json.tmp")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(payload)
        os.replace(tmp_path, CONFIG_FILE_PATH)

        # 立即刷新本进程缓存;另一进程(代理)最迟在下个 TTL 周期读到
        with _cache_lock:
            _cache["config"] = config.model_copy(deep=True)
            _cache["loaded_at"] = time.monotonic()

        logger.info(f"Configuration successfully saved to {CONFIG_FILE_PATH}")
    except Exception as e:
        logger.error(f"Error saving config file: {e}")
        raise
