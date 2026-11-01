# src/services/credential_service.py —— 后台任务凭证解析(API 密钥优先,账号密码兜底)
"""统一解析访问 Emby 上游所需的凭证,供两个进程共用(代理核心 + 管理后台)。

背景:预热/索引/封面生成/RSS 匹配等后台任务原先硬依赖 emby_api_key,
但 API 密钥只能由 Emby 管理员生成。本模块在密钥缺失时改用账号密码调
/Users/AuthenticateByName 登录,AccessToken 落盘缓存(SQLite,双进程共享),
401 自愈:清缓存 → 下次调用重新登录。

模式判定 auth_mode():
  - api_key   配置了 emby_api_key → 直接使用(不依赖密码,首选)
  - user_pass 配置了 emby_username + emby_password → 登录换取 token
  - none      两者皆无 → 后台任务不可用(与旧行为一致)

安全说明:密码与 admin_password 同级明文存于 config.json,仅建议可信环境使用。
"""

import asyncio
import json
import logging
import time
from typing import Dict, Optional, Tuple

import aiohttp
import requests

import config_manager
import db_manager
from emby_net import emby_proxy_from_config

logger = logging.getLogger(__name__)

# 登录请求的固定设备身份(Emby 4.x 要求 AuthenticateByName 携带 X-Emby-Authorization)
_AUTH_HEADER = ('MediaBrowser Client="EmbyVirtualProxy", Device="EVP-Backend", '
                'DeviceId="evp-backend", Version="4.8.11.0"')
_LOGIN_TIMEOUT = aiohttp.ClientTimeout(total=15)

# 模块级单飞锁:并发调用只触发一次真实登录(Python 3.10+ Lock 与事件循环解绑,可安全模块级创建)
_login_lock = asyncio.Lock()

# 进程内缓存(避免每次读 SQLite):key 变化(改地址/账号)即视为失效
_mem_cache: Dict[str, object] = {"key": None, "auth": None}


class CredentialError(Exception):
    """后台凭证不可用(未配置 / 登录失败)。"""


def auth_mode(config) -> str:
    """判定当前凭证模式:api_key / user_pass / none。"""
    if (getattr(config, "emby_api_key", "") or "").strip():
        return "api_key"
    if (getattr(config, "emby_username", "") or "").strip() and \
       (getattr(config, "emby_password", "") or "").strip():
        return "user_pass"
    return "none"


def _cache_key(config) -> str:
    return f"{(config.emby_url or '').rstrip('/')}|{(config.emby_username or '').strip()}"


def _mem_get(config) -> Optional[Dict[str, object]]:
    if _mem_cache["key"] == _cache_key(config) and _mem_cache["auth"]:
        return _mem_cache["auth"]
    return None


def _mem_put(config, auth: Dict[str, object]) -> None:
    _mem_cache["key"] = _cache_key(config)
    _mem_cache["auth"] = auth


# --- 异步核心(代理核心与管理后台的 async 路径) ---

async def _login_user_pass(config, session: aiohttp.ClientSession) -> Dict[str, object]:
    """账号密码登录 Emby,返回 {token, user_id, user_name, mode}。失败抛 CredentialError。"""
    url = f"{config.emby_url.rstrip('/')}/emby/Users/AuthenticateByName"
    payload = {"Username": (config.emby_username or "").strip(),
               "Pw": config.emby_password or ""}
    try:
        async with session.post(
            url, json=payload, timeout=_LOGIN_TIMEOUT,
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "X-Emby-Authorization": _AUTH_HEADER},
            proxy=emby_proxy_from_config(config),
        ) as resp:
            text = await resp.text()
            if resp.status == 401:
                raise CredentialError("Emby 登录失败:用户名或密码错误 (401)。")
            if resp.status != 200:
                raise CredentialError(f"Emby 登录失败 (Status: {resp.status}): {text[:200]}")
            data = json.loads(text)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        raise CredentialError(f"连接 Emby 失败,无法登录: {e}") from e
    except json.JSONDecodeError as e:
        raise CredentialError(f"Emby 登录响应解析失败: {e}") from e

    token = (data.get("AccessToken") or "").strip()
    user = data.get("User") or {}
    user_id = str(user.get("Id") or "")
    if not token or not user_id:
        raise CredentialError("Emby 登录响应中缺少 AccessToken/User.Id。")
    return {"token": token, "user_id": user_id, "user_name": str(user.get("Name") or ""),
            "mode": "user_pass"}


async def get_backend_auth(config, session: Optional[aiohttp.ClientSession] = None,
                           force_refresh: bool = False) -> Dict[str, object]:
    """解析后台凭证:{token, user_id, user_name, mode}。失败抛 CredentialError。

    - api_key 模式直接返回(不产生网络请求);
    - user_pass 模式:内存缓存 → SQLite 缓存 → 真实登录,单飞锁防登录风暴。
    """
    mode = auth_mode(config)
    if mode == "none":
        raise CredentialError("未配置 Emby 凭证:请填写 API 密钥,或账号 + 密码。")
    if mode == "api_key":
        return {"token": (config.emby_api_key or "").strip(), "user_id": "",
                "user_name": "", "mode": "api_key"}

    if not (config.emby_url or "").strip():
        raise CredentialError("未配置 Emby 服务器地址。")

    if not force_refresh:
        mem = _mem_get(config)
        if mem:
            return mem
        cached = db_manager.get_backend_auth_cache(_cache_key(config))
        if cached and cached.get("token"):
            auth = {"token": cached["token"], "user_id": cached["user_id"],
                    "user_name": "", "mode": "user_pass"}
            _mem_put(config, auth)
            return auth

    async with _login_lock:
        # 双重检查:等锁期间可能已被并发登录填充
        if not force_refresh:
            mem = _mem_get(config)
            if mem:
                return mem
        own_session = session is None
        try:
            sess = session or aiohttp.ClientSession()
            try:
                auth = await _login_user_pass(config, sess)
            finally:
                if own_session:
                    await sess.close()
        except CredentialError as e:
            db_manager.record_backend_auth_error(_cache_key(config), str(e))
            raise
        db_manager.save_backend_auth_cache(_cache_key(config), auth["token"],
                                           auth["user_id"], "user_pass")
        _mem_put(config, auth)
        logger.info(f"Emby 账号密码登录成功(用户: {auth['user_name'] or auth['user_id']})。")
        return auth


async def get_backend_headers(config, session: Optional[aiohttp.ClientSession] = None,
                              force_refresh: bool = False,
                              accept: str = "application/json") -> Dict[str, str]:
    """后台请求头:{Accept, X-Emby-Token}。失败抛 CredentialError。"""
    auth = await get_backend_auth(config, session=session, force_refresh=force_refresh)
    headers = {"X-Emby-Token": auth["token"]}
    if accept:
        headers["Accept"] = accept
    return headers


async def invalidate_backend_auth(reason: str = "") -> None:
    """401 自愈:清空内存与 SQLite 缓存,下次调用重新登录。"""
    _mem_cache["key"] = None
    _mem_cache["auth"] = None
    try:
        config = config_manager.load_config_readonly()
        db_manager.clear_backend_auth_cache(_cache_key(config))
    except Exception as e:
        logger.warning(f"清除后台凭证缓存时出错: {e}")
    if reason:
        logger.warning(f"后台凭证已失效并清除: {reason}")


async def fetch_json_with_auth(session: aiohttp.ClientSession, url: str, *, config,
                               method: str = "GET", params=None, timeout=None,
                               proxy: Optional[str] = None) -> Tuple[int, object]:
    """带凭证的直连 Emby 请求:注入 X-Emby-Token,401 → 重登后重试一次。

    返回 (status, parsed_json 或原始文本);网络异常原样抛出,由调用方按既有语义处理。
    """
    for attempt in (1, 2):
        headers = await get_backend_headers(config, session=session,
                                            force_refresh=(attempt == 2))
        async with session.request(method, url, params=params, headers=headers,
                                   timeout=timeout, proxy=proxy) as resp:
            if resp.status == 401 and attempt == 1:
                await invalidate_backend_auth(f"上游对 {url} 返回 401")
                continue
            text = await resp.text()
            if "application/json" in (resp.headers.get("Content-Type") or ""):
                try:
                    return resp.status, json.loads(text)
                except json.JSONDecodeError:
                    pass
            return resp.status, text
    # 理论不可达(循环内必 return);保险返回
    return 401, None


# --- 同步版(仅限已运行在工作线程中的调用方,如 RSS 处理器) ---

def _login_user_pass_sync(config) -> Dict[str, object]:
    url = f"{config.emby_url.rstrip('/')}/emby/Users/AuthenticateByName"
    payload = {"Username": (config.emby_username or "").strip(),
               "Pw": config.emby_password or ""}
    try:
        resp = requests.post(url, json=payload, timeout=15, headers={
            "Accept": "application/json", "Content-Type": "application/json",
            "X-Emby-Authorization": _AUTH_HEADER,
        })
    except requests.RequestException as e:
        raise CredentialError(f"连接 Emby 失败,无法登录: {e}") from e
    if resp.status_code == 401:
        raise CredentialError("Emby 登录失败:用户名或密码错误 (401)。")
    if resp.status_code != 200:
        raise CredentialError(f"Emby 登录失败 (Status: {resp.status_code}): {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as e:
        raise CredentialError(f"Emby 登录响应解析失败: {e}") from e
    token = (data.get("AccessToken") or "").strip()
    user = data.get("User") or {}
    user_id = str(user.get("Id") or "")
    if not token or not user_id:
        raise CredentialError("Emby 登录响应中缺少 AccessToken/User.Id。")
    return {"token": token, "user_id": user_id, "user_name": str(user.get("Name") or ""),
            "mode": "user_pass"}


def get_backend_headers_sync(config) -> Dict[str, str]:
    """同步解析后台请求头:api_key → 内存 → SQLite → 同步登录(调用方须在工作线程)。"""
    mode = auth_mode(config)
    if mode == "none":
        raise CredentialError("未配置 Emby 凭证:请填写 API 密钥,或账号 + 密码。")
    if mode == "api_key":
        return {"Accept": "application/json", "X-Emby-Token": (config.emby_api_key or "").strip()}

    if not (config.emby_url or "").strip():
        raise CredentialError("未配置 Emby 服务器地址。")

    mem = _mem_get(config)
    if mem and mem.get("token"):
        return {"Accept": "application/json", "X-Emby-Token": mem["token"]}
    cached = db_manager.get_backend_auth_cache(_cache_key(config))
    if cached and cached.get("token"):
        auth = {"token": cached["token"], "user_id": cached["user_id"],
                "user_name": "", "mode": "user_pass"}
        _mem_put(config, auth)
        return {"Accept": "application/json", "X-Emby-Token": auth["token"]}

    try:
        auth = _login_user_pass_sync(config)
    except CredentialError:
        db_manager.record_backend_auth_error(_cache_key(config), "账号密码登录失败(同步路径)")
        raise
    db_manager.save_backend_auth_cache(_cache_key(config), auth["token"],
                                       auth["user_id"], "user_pass")
    _mem_put(config, auth)
    return {"Accept": "application/json", "X-Emby-Token": auth["token"]}


def get_backend_json_sync(url: str, params, config) -> Tuple[int, object]:
    """同步版带凭证 GET:401 → 重登后重试一次。返回 (status, parsed_json 或原始文本)。"""
    for attempt in (1, 2):
        headers = get_backend_headers_sync(config)
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        if resp.status_code == 401 and attempt == 1:
            _mem_cache["key"] = None
            _mem_cache["auth"] = None
            db_manager.clear_backend_auth_cache(_cache_key(config))
            continue
        try:
            return resp.status_code, resp.json()
        except ValueError:
            return resp.status_code, resp.text
    return 401, None


def peek_backend_token(config) -> str:
    """只读缓存取 token(绝不发请求,供客户端请求路径的同步 fallback)。

    api_key 模式直接返回密钥;user_pass 模式返回缓存 token(冷缓存返回空串,
    行为与旧的"未配置 api_key 传空"完全一致)。
    """
    mode = auth_mode(config)
    if mode == "api_key":
        return (config.emby_api_key or "").strip()
    if mode != "user_pass":
        return ""
    mem = _mem_get(config)
    if mem and mem.get("token"):
        return mem["token"]
    cached = db_manager.get_backend_auth_cache(_cache_key(config))
    return (cached or {}).get("token") or ""


# --- 管理面板:连接测试与状态快照 ---

async def test_connection(config=None) -> Dict[str, object]:
    """面板「测试连接」:api_key 模式校验密钥;user_pass 模式强制重新登录。"""
    cfg = config or config_manager.load_config()
    mode = auth_mode(cfg)
    if not (cfg.emby_url or "").strip():
        return {"ok": False, "mode": mode, "user": "", "message": "请先配置 Emby 服务器地址。"}
    if mode == "none":
        return {"ok": False, "mode": mode, "user": "",
                "message": "请填写 API 密钥,或账号 + 密码。"}

    try:
        if mode == "api_key":
            async with aiohttp.ClientSession() as session:
                status, data = await fetch_json_with_auth(
                    session, f"{cfg.emby_url.rstrip('/')}/emby/System/Info", config=cfg,
                    timeout=aiohttp.ClientTimeout(total=15), proxy=emby_proxy_from_config(cfg))
            if status == 200 and isinstance(data, dict):
                server_name = data.get("ServerName") or "未知服务器"
                return {"ok": True, "mode": "api_key", "user": server_name,
                        "message": f"API 密钥有效(服务器: {server_name})。"}
            return {"ok": False, "mode": "api_key", "user": "",
                    "message": f"API 密钥校验失败 (Status: {status})。"}

        auth = await get_backend_auth(cfg, force_refresh=True)
        user_name = auth.get("user_name") or auth.get("user_id") or ""
        return {"ok": True, "mode": "user_pass", "user": user_name,
                "message": f"账号密码登录成功(用户: {user_name})。"}
    except CredentialError as e:
        return {"ok": False, "mode": mode, "user": "", "message": str(e)}
    except aiohttp.ClientError as e:
        return {"ok": False, "mode": mode, "user": "", "message": f"连接 Emby 失败: {e}"}


def status_snapshot(config=None) -> Dict[str, object]:
    """面板状态展示:当前模式 + 缓存健康度(读 SQLite,不发网络请求)。"""
    cfg = config or config_manager.load_config()
    mode = auth_mode(cfg)
    result: Dict[str, object] = {
        "mode": mode,
        "username": (cfg.emby_username or "").strip() if mode == "user_pass" else "",
        "token_cached": False,
        "updated_at": None,
        "last_ok_at": None,
        "last_error": "",
        "user_id": "",
    }
    if mode != "user_pass":
        return result

    key = _cache_key(cfg)
    mem = _mem_get(cfg)
    row = db_manager.get_backend_auth_cache(key) or {}
    token = (mem or {}).get("token") or row.get("token") or ""
    result.update({
        "token_cached": bool(token),
        "updated_at": row.get("updated_at") or None,
        "last_ok_at": row.get("last_ok_at") or None,
        "last_error": row.get("last_error") or "",
        "user_id": row.get("user_id") or "",
    })
    return result
