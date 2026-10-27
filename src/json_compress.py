# src/json_compress.py —— 只压缩 JSON 响应的 ASGI 中间件
"""代理转发的绝大多数大响应是流式媒体(视频/图片),**绝对不能压缩**:

- 视频走 Range/分块,压缩会破坏 Content-Length、播放与拖动;
- 图片(jpg/webp)本身已是压缩格式,再压只烧 CPU、体积几乎不变。

因此这里只对 `content-type` 含 `application/json` 的响应做 gzip/br 压缩,
其余响应一个字节都不碰:不缓冲、不改头、不加 Vary。

为什么值得做:本地直出的一页 100 条条目(带 Overview/People/Genres)常见
200~600KB,压缩后通常只剩 10~20%,远程/窄带客户端提升非常直接。

安全细节:
- 请求带 Range 头 → 一律不压缩;
- 响应已有 Content-Encoding → 不动;
- 只压 200 状态;
- 压缩后重算 Content-Length,并补 Vary: Accept-Encoding(缓存正确性必需)。
"""

import logging
import zlib

try:  # Brotli 在 requirements 里已有,缺失时自动降级为 gzip
    import brotli  # type: ignore
except Exception:  # pragma: no cover
    brotli = None

logger = logging.getLogger(__name__)


class JsonCompressionMiddleware:
    """纯 ASGI 中间件:仅压缩 JSON 响应体。"""

    def __init__(self, app, minimum_size: int = 1024):
        self.app = app
        self.minimum_size = minimum_size

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("method") == "HEAD":
            await self.app(scope, receive, send)
            return

        req_headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        if b"range" in req_headers:
            await self.app(scope, receive, send)
            return

        accept = (req_headers.get(b"accept-encoding") or b"").decode("latin-1").lower()
        if brotli is not None and "br" in accept:
            encoding = "br"
        elif "gzip" in accept:
            encoding = "gzip"
        else:
            await self.app(scope, receive, send)
            return

        state = {"start": None, "compress": False}
        chunks = []

        async def send_wrapper(message):
            mtype = message.get("type")

            if mtype == "http.response.start":
                status = message["status"]
                headers = [(k, v) for k, v in message.get("headers") or []]
                ctype = ""
                for k, v in headers:
                    if k.lower() == b"content-type":
                        ctype = v.decode("latin-1", "ignore").lower()
                        break
                has_encoding = any(k.lower() == b"content-encoding" for k, _ in headers)
                state["compress"] = (
                    status == 200
                    and "application/json" in ctype
                    and not has_encoding
                )
                state["start"] = (status, headers)
                if not state["compress"]:
                    await send(message)
                return

            if mtype == "http.response.body" and state["compress"]:
                chunks.append(message.get("body") or b"")
                if message.get("more_body", False):
                    return
                await _send_compressed(send, state["start"], b"".join(chunks), encoding, self.minimum_size)
                return

            await send(message)

        await self.app(scope, receive, send_wrapper)


async def _send_compressed(send, start, body: bytes, encoding: str, minimum_size: int) -> None:
    status, headers = start

    if len(body) < minimum_size:
        out = [(k, v) for k, v in headers if k.lower() != b"content-length"]
        out.append((b"content-length", str(len(body)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": out})
        await send({"type": "http.response.body", "body": body})
        return

    try:
        if encoding == "br":
            data = brotli.compress(body, quality=4)
        else:
            compressor = zlib.compressobj(6, zlib.DEFLATED, 16 + zlib.MAX_WBITS)  # 16+MAX_WBITS = gzip 封装
            data = compressor.compress(body) + compressor.flush()
    except Exception as e:  # 压缩失败就原样返回,绝不让请求失败
        logger.warning(f"JSON 压缩失败,按原文返回: {e}")
        out = [(k, v) for k, v in headers if k.lower() != b"content-length"]
        out.append((b"content-length", str(len(body)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": out})
        await send({"type": "http.response.body", "body": body})
        return

    out = [
        (k, v) for k, v in headers
        if k.lower() not in (b"content-length", b"content-encoding")
    ]
    out.append((b"content-encoding", encoding.encode()))
    out.append((b"content-length", str(len(data)).encode()))
    out.append((b"vary", b"Accept-Encoding"))
    await send({"type": "http.response.start", "status": status, "headers": out})
    await send({"type": "http.response.body", "body": data})
