# src/services/auth_service.py —— 管理面板登录会话
"""管理面板的访问控制:可选拍照门卫。

- 密码来自 config.admin_password;留空 = 未启用,面板直接放行。
- 登录成功签发随机 Token(内存保存,7 天有效,管理后台重启后需重新登录)。
- 只保护管理后台 API(/api/*);代理核心(8999)完全不受影响。
- 密码明文存于 config.json(与 emby_api_key 同级),仅建议在可信局域网使用。
"""

import secrets
import threading
import time
from typing import Dict, Optional

_TOKEN_TTL_SECONDS = 7 * 24 * 3600

_tokens: Dict[str, float] = {}
_lock = threading.Lock()


def is_enabled(config) -> bool:
    """是否启用了访问控制。"""
    return bool(config.admin_password)


def login(config, password: str) -> Optional[str]:
    """校验密码,成功返回新 Token,失败返回 None(未启用时返回 None,调用方先行判断)。"""
    if not is_enabled(config):
        return None
    if secrets.compare_digest(str(password or ""), str(config.admin_password)):
        token = secrets.token_hex(32)
        with _lock:
            _cleanup()
            _tokens[token] = time.time() + _TOKEN_TTL_SECONDS
        return token
    return None


def verify(token: Optional[str]) -> bool:
    """校验 Token 是否存在且未过期。"""
    if not token:
        return False
    with _lock:
        expires_at = _tokens.get(token)
        if expires_at is None:
            return False
        if expires_at < time.time():
            del _tokens[token]
            return False
        return True


def logout(token: Optional[str]) -> None:
    """注销 Token。"""
    if token:
        with _lock:
            _tokens.pop(token, None)


def _cleanup() -> None:
    now = time.time()
    expired = [k for k, v in _tokens.items() if v < now]
    for key in expired:
        del _tokens[key]
