# src/proxy_handlers/handler_image_cache.py —— 海报/背景图本地化缓存
"""把 Emby 的图片请求(海报/背景/缩略图)缓存在本地磁盘,命中后直接返回。

为什么值得做:一次首页加载会拉几十张海报,原先全部由代理**流式转发** Emby
(不缓存、不落盘)。局域网影响不大,远程访问时这几乎是首屏体感的全部瓶颈。

为什么可以放心缓存:Emby 的图片 URL 自带 `tag=`(图片内容指纹),同一个 URL
的内容永不改变。因此:
- 缓存键只依赖 (条目, 类型, tag, 尺寸参数),**不需要任何失效逻辑**;
- 可以回 `Cache-Control: immutable`,连客户端都不再重复请求。

边界与安全:
- 没有 `tag` 的请求(URL 不稳定)一律直通,不缓存;
- 响应不是 200 / 不是图片 / 超过单张上限 → 直通,不落盘;
- 只有携带令牌的请求才允许命中缓存(否则等于绕开 Emby 鉴权读图);
- 总量超过 `image_cache_max_mb` 时按修改时间(LRU 近似)淘汰到 90%。
"""

import asyncio
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Optional

from aiohttp import ClientSession
from fastapi import Request
from fastapi.responses import FileResponse, Response

import app_paths
import config_manager
import proxy_cache
from emby_net import emby_proxy_of

logger = logging.getLogger(__name__)

# 真实 Emby 条目 Id 的形态不固定:常见 32 位十六进制,但也有纯数字(如 499713)与
# 带连字符的 GUID。这里放宽到「字母数字加连字符」,再用下面两个前缀规则排除虚拟库自身:
#   - 虚拟库封面(uuid)由 handler_images 先处理,这里兜底跳过;
#   - tmdb_ / tmdb- 是「缺失剧集 / RSS 占位项」的伪造 Id,没有真实图片可取。
_IMAGE_PATH_RE = re.compile(r"/Items/([A-Za-z0-9\-]{1,64})/Images/([A-Za-z]+)")
_VLIB_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)

CACHE_DIR = Path(app_paths.CONFIG_DIR) / "image_cache"

_EXT_CANDIDATES = (".jpg", ".png", ".webp", ".gif", ".avif", ".jpeg")
_MEDIA_TYPE_BY_SUFFIX = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".gif": "image/gif", ".avif": "image/avif",
}
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
    "image/webp": ".webp", "image/gif": ".gif", "image/avif": ".avif",
}

# 单张图片超过这个大小就不落盘(避免把巨幅原图塞满磁盘)
_MAX_ENTRY_BYTES = 12 * 1024 * 1024
_IMMUTABLE_MAX_AGE = 31536000  # 一年

# 磁盘用量统计(近似;首次使用时扫描一次)
_total_bytes: Optional[int] = None
_scan_lock = asyncio.Lock()


def _cache_key(item_id: str, image_type: str, tag: str, params) -> str:
    """由 URL 语义生成稳定文件名:同一条目同一 tag 永远命中同一个文件。"""
    stable_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k.lower() not in ("x-emby-token", "api_key"))
    raw = f"{item_id}|{image_type}|{tag}|{stable_params}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


def _cache_path(key: str, ext: str) -> Path:
    return CACHE_DIR / key[:2] / f"{key}{ext}"


def _find_cached(key: str) -> Optional[Path]:
    for ext in _EXT_CANDIDATES:
        path = _cache_path(key, ext)
        if path.is_file():
            return path
    return None


def _has_credential(request: Request) -> bool:
    """判断请求是否携带了某种 Emby 凭证(只是"有会话"的门槛,不做真伪校验)。

    注意:浏览器 <img> 标签无法设置自定义请求头,Emby Web 与多数第三方客户端
    加载海报时依赖 Cookie 或 api_key 查询参数。只认 X-Emby-Token 会把这类
    正常图片请求全部挡掉(表现为 image_miss 永远是 0)。
    """
    if request.headers.get("x-emby-token") or request.headers.get("x-emby-authorization"):
        return True
    if request.headers.get("cookie"):
        return True
    params = request.query_params
    return bool(params.get("X-Emby-Token") or params.get("api_key"))


async def _ensure_total_scanned() -> int:
    """首次使用时扫描缓存目录总量(在线程里做,不阻塞事件循环)。"""
    global _total_bytes
    if _total_bytes is not None:
        return _total_bytes

    def _scan() -> int:
        total = 0
        if CACHE_DIR.is_dir():
            for f in CACHE_DIR.rglob("*"):
                try:
                    if f.is_file():
                        total += f.stat().st_size
                except OSError:
                    continue
        return total

    async with _scan_lock:
        if _total_bytes is None:
            _total_bytes = await asyncio.to_thread(_scan)
    return _total_bytes or 0


def _max_bytes() -> int:
    config = config_manager.load_config_readonly()
    mb = int(getattr(config, "image_cache_max_mb", 2048) or 0)
    return max(0, mb) * 1024 * 1024


async def _evict_if_needed() -> None:
    """超出配额时按修改时间从旧到新删到 90%(LRU 近似,够用且零额外状态)。"""
    global _total_bytes
    cap = _max_bytes()
    if cap <= 0 or (_total_bytes or 0) <= cap:
        return

    def _evict() -> int:
        files = []
        for f in CACHE_DIR.rglob("*"):
            try:
                if f.is_file():
                    st = f.stat()
                    files.append((st.st_mtime, st.st_size, f))
            except OSError:
                continue
        files.sort()
        removed = 0
        target = int(cap * 0.9)
        current = sum(size for _, size, _ in files)
        for _, size, path in files:
            if current <= target:
                break
            try:
                path.unlink()
                current -= size
                removed += 1
            except OSError:
                continue
        return removed

    removed = await asyncio.to_thread(_evict)
    if removed:
        logger.info(f"图片缓存超出配额,已淘汰 {removed} 个文件。")
    _total_bytes = await asyncio.to_thread(
        lambda: sum(f.stat().st_size for f in CACHE_DIR.rglob("*") if f.is_file())
    )


async def _store(key: str, body: bytes, content_type: str) -> None:
    """异步落盘(在线程里写,不阻塞响应)。"""
    global _total_bytes
    ext = _CONTENT_TYPE_EXT.get(content_type.split(";")[0].strip().lower())
    if ext is None or len(body) > _MAX_ENTRY_BYTES:
        return

    path = _cache_path(key, ext)

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)

    try:
        await asyncio.to_thread(_write)
        _total_bytes = (_total_bytes or 0) + len(body)
    except Exception as e:
        logger.debug(f"图片缓存写入失败({key[:8]}): {e}")
        return

    try:
        await _evict_if_needed()
    except Exception as e:
        logger.debug(f"图片缓存淘汰失败: {e}")


async def handle_cached_image(request: Request, full_path: str, method: str,
                              session: ClientSession, real_emby_url: str) -> Optional[Response]:
    """命中本地缓存直接返回;未命中则转发并顺手落盘。返回 None 表示不接管。"""
    if method != "GET":
        return None

    config = config_manager.load_config_readonly()
    if not getattr(config, "enable_image_cache", True):
        proxy_cache.bump("image_skip_disabled")
        return None

    match = _IMAGE_PATH_RE.search(f"/{full_path}")
    if not match:
        return None

    item_id, image_type = match.group(1), match.group(2)
    # 虚拟库封面 / 伪造占位条目不走这里(前者由 handler_images 处理,后者没有真实图片)
    if _VLIB_ID_RE.match(item_id) or item_id.startswith(("tmdb_", "tmdb-")):
        proxy_cache.bump("image_skip_virtual")
        return None
    # 分块请求(Range)不接管:整图返回会破坏 Range 语义,交给默认转发
    if request.headers.get("range"):
        proxy_cache.bump("image_skip_range")
        return None
    tag = request.query_params.get("tag") or request.query_params.get("Tag")
    if not tag:
        proxy_cache.bump("image_skip_no_tag")
        return None  # 没有 tag 的 URL 不稳定,不能当 immutable 缓存
    # 默认不校验凭证:Emby 的图片接口通常不鉴权,浏览器 <img> 也不带可识别凭证。
    # 只有在 Emby 确实对图片做了鉴权时,才建议打开 image_cache_require_credential。
    if getattr(config, "image_cache_require_credential", False) and not _has_credential(request):
        proxy_cache.bump("image_skip_no_cred")
        return None

    key = _cache_key(item_id, image_type, tag, request.query_params)
    etag = f'"{tag}"'

    if request.headers.get("if-none-match") == etag:
        proxy_cache.bump("image_304")
        return Response(status_code=304, headers={
            "ETag": etag,
            "Cache-Control": f"public, max-age={_IMMUTABLE_MAX_AGE}, immutable",
            "X-EVP-Image-Cache": "hit-304",
        })

    cached = _find_cached(key)
    if cached is not None:
        proxy_cache.bump("image_hit")
        # 命中是"稀有事件"(客户端拿到 immutable 后基本不再请求),所以用 INFO 记为可见证据
        logger.info(f"图片缓存命中: {item_id}/{image_type}")
        return FileResponse(
            str(cached),
            media_type=_MEDIA_TYPE_BY_SUFFIX.get(cached.suffix, "image/jpeg"),
            headers={
                "ETag": etag,
                "Cache-Control": f"public, max-age={_IMMUTABLE_MAX_AGE}, immutable",
                # 调试标记:命中时能看到,未接管(无 tag / 无令牌 / Range)时没有这个头
                "X-EVP-Image-Cache": "hit",
            },
        )

    # 未命中:回源取一次(把图片整体读进内存,顺手写盘;图片都很小)
    proxy_cache.bump("image_miss")
    logger.debug(f"图片缓存未命中,回源: {item_id}/{image_type}")
    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    try:
        async with session.get(f"{real_emby_url}/{full_path}", params=request.query_params,
                               headers=headers, proxy=emby_proxy_of(request)) as resp:
            if resp.status != 200:
                return None  # 交给默认转发路径,保持原有错误语义
            content_type = resp.headers.get("Content-Type", "")
            if not content_type.lower().startswith("image/"):
                return None
            body = await resp.read()
    except Exception as e:
        logger.debug(f"图片回源失败({item_id[:8]}): {e}")
        return None

    await _ensure_total_scanned()
    await _store(key, body, content_type)

    return Response(
        content=body,
        media_type=content_type,
        headers={
            "ETag": etag,
            "Cache-Control": f"public, max-age={_IMMUTABLE_MAX_AGE}, immutable",
            "Content-Length": str(len(body)),
            "X-EVP-Image-Cache": "miss",
        },
    )


async def cache_usage() -> dict:
    """缓存用量(给状态面板用)。"""
    total = await _ensure_total_scanned()
    files = 0
    if CACHE_DIR.is_dir():
        files = await asyncio.to_thread(lambda: sum(1 for f in CACHE_DIR.rglob("*") if f.is_file()))
    return {"files": files, "bytes": total, "max_bytes": _max_bytes()}


async def clear_cache() -> dict:
    """清空图片缓存目录(只删图片缓存,不动虚拟库封面)。"""
    global _total_bytes

    def _clear() -> int:
        removed = 0
        if CACHE_DIR.is_dir():
            for f in list(CACHE_DIR.rglob("*")):
                if not f.is_file():
                    continue
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    removed = await asyncio.to_thread(_clear)
    _total_bytes = 0
    logger.info(f"图片缓存已清空,删除 {removed} 个文件。")
    return {"removed": removed}
