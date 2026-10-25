# src/emby_net.py —— Emby 上游连接的代理辅助
"""所有访问 Emby 上游的 HTTP 请求统一从这里解析可选代理。

- 客户端转发链路:reverse_proxy 入口把配置写入 request.state,
  各 handler 用 emby_proxy_of(request) 获取(无 request 的后台任务除外);
- 非请求上下文(启动预热/封面素材下载/管理侧查询):用 emby_proxy_from_config(config)。

仅支持 HTTP 代理(含认证),格式 http://[user:pass@]host:port;留空直连。
代理值按请求解析(reverse_proxy 每次读配置),保存设置后即时生效,无需重启。
"""

from typing import Optional


def normalize_emby_proxy(value: Optional[str]) -> Optional[str]:
    proxy = (value or "").strip()
    return proxy or None


def emby_proxy_from_config(config) -> Optional[str]:
    """非请求上下文使用:从 AppConfig 解析代理值。"""
    return normalize_emby_proxy(getattr(config, "emby_proxy", None))


def emby_proxy_of(request) -> Optional[str]:
    """请求上下文使用:读取 reverse_proxy 入口写入的 request.state。"""
    try:
        return request.state.emby_proxy
    except AttributeError:
        return None
