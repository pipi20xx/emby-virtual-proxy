# src/services/server_registry.py —— 多服务器注册表归一化与主服镜像
"""config.emby_servers 的归一化与兼容层(纯函数,均原地修改、可重复调用)。

调用时机:config_manager.save_config() 序列化之前——所有写路径(面板保存、
后台封面回写等)自动经过,保证落盘的 emby_servers 永远自洽。

- normalize_servers():id 清洗/自动生成、主服唯一化;
- sync_legacy_primary_fields():把主服条目镜像写入旧字段(emby_url 等),
  使现有直接读 config.emby_url 的代码永远指向主服;列表为空时 no-op,
  旧字段即全部真相(从未使用新面板的安装不受影响)。

阶段 1 仅有面板读取 emby_servers;代理核心行为完全由旧字段驱动。
"""

import re
from typing import List

# id 允许字符与长度(设计文档 §2.2:URL 安全、不含 ~,与前缀解析无歧义)
_ID_PATTERN = re.compile(r"[^a-z0-9_-]+")
_ID_MAX_LEN = 8


def _sanitize_server_id(raw: str) -> str:
    """清洗用户输入的 id:小写、剔除非法字符、截断到 8 位。"""
    return _ID_PATTERN.sub("", (raw or "").strip().lower())[:_ID_MAX_LEN]


def _next_free_id(taken: set) -> str:
    """依次尝试 s1..sN,返回第一个未被占用的短 id(必要时进位)。"""
    n = 1
    while True:
        candidate = f"s{n}"
        if candidate not in taken:
            return candidate
        n += 1


def normalize_servers(config) -> None:
    """归一化 config.emby_servers(原地修改,幂等):

    - trim url/name/id;
    - id 非法 → 清洗;清洗后为空或与其它条目重复 → 自动分配空闲 sN;
    - 主服唯一化:无主服 → 第一条升主;多条主服 → 保留第一条;
      全表仅一条时强制为主服。
    """
    servers = list(getattr(config, "emby_servers", None) or [])
    if not servers:
        return

    taken = set()
    for server in servers:
        server.url = (server.url or "").strip()
        server.name = (server.name or "").strip()

        cleaned = _sanitize_server_id(server.id)
        if not cleaned or cleaned in taken:
            cleaned = _next_free_id(taken)
        server.id = cleaned
        taken.add(cleaned)

    primary_indexes = [i for i, s in enumerate(servers) if s.is_primary]
    if not primary_indexes:
        servers[0].is_primary = True
    else:
        for i in primary_indexes[1:]:
            servers[i].is_primary = False
        if len(servers) == 1:
            servers[0].is_primary = True


def get_primary_server(config):
    """返回主服条目;注册表为空时返回 None(调用方回退旧字段)。"""
    servers: List = getattr(config, "emby_servers", None) or []
    for server in servers:
        if server.is_primary:
            return server
    return None


def sync_legacy_primary_fields(config) -> None:
    """主服条目 → 旧字段镜像(保存时调用;列表为空时 no-op)。"""
    primary = get_primary_server(config)
    if primary is None:
        return
    config.emby_url = primary.url or config.emby_url
    config.emby_api_key = primary.api_key if primary.api_key is not None else (config.emby_api_key or "")
    config.emby_username = primary.username if primary.username is not None else (config.emby_username or "")
    config.emby_password = primary.password if primary.password is not None else (config.emby_password or "")
    config.emby_proxy = primary.proxy if primary.proxy is not None else (config.emby_proxy or "")
