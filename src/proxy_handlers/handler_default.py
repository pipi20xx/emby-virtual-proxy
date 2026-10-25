# src/proxy_handlers/handler_default.py (完整流式优化版)
import logging
from fastapi import Request
from fastapi.responses import StreamingResponse, Response
from aiohttp import ClientSession, ClientError

from emby_net import emby_proxy_of

logger = logging.getLogger(__name__)

async def forward_request(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    session: ClientSession,
) -> Response:
    """
    默认的请求转发器，使用流式传输将请求高效地转发到真实的 Emby 服务器。
    这对于视频播放、文件下载等大文件传输至关重要。
    """
    target_url = f"{real_emby_url}/{full_path}"
    
    # 准备要转发的头部。
    # 必须移除 'host' 头，否则 aiohttp 会报错，并且目标服务器可能会因此行为异常。
    # aiohttp 会根据 target_url 自动生成正确的 Host 头。
    headers = {k: v for k, v in request.headers.items() if k.lower() != 'host'}
    
    try:
        # 使用 aiohttp 发起流式请求。
        # 'data' 参数直接使用 request.stream()，它是一个异步生成器，会逐块读取客户端上传的数据。
        # 这样，即使客户端上传一个很大的文件，代理服务器的内存也不会暴涨。
        resp = await session.request(
            method=method,
            url=target_url,
            params=request.query_params,
            headers=headers,
            data=request.stream(),
            allow_redirects=False, # 让客户端自己处理重定向，这是反向代理的标准行为
            proxy=emby_proxy_of(request)
        )

        # 定义一个异步生成器，用于逐块读取来自 Emby 服务器的响应体并将其 yield 出去。
        async def stream_generator():
            try:
                # resp.content 是一个 aiohttp.StreamReader 对象。
                # .iter_chunked(8192) 会以 8KB 的块大小读取数据。
                # 这是一个合理的缓冲区大小，可以在网络效率和内存占用之间取得平衡。
                async for chunk in resp.content.iter_chunked(8192):
                    yield chunk
            except ClientError as e:
                logger.error(f"Error while streaming response from Emby for {full_path}: {e}")
            finally:
                # 无论成功还是失败，都确保上游响应被关闭，以释放连接回连接池。
                resp.release()

        # 过滤掉 hop-by-hop headers，这些头部是描述两个直接连接节点之间的信息，不应该被代理转发。
        # 'content-length' 也应该被移除，因为在流式传输（Transfer-Encoding: chunked）中，长度是动态的。
        response_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding', 'content-length')
        }

        # 使用 FastAPI 的 StreamingResponse 将数据流式返回给客户端。
        # 客户端可以立即开始接收数据，而无需等待整个文件在代理服务器上下载完成。
        return StreamingResponse(
            content=stream_generator(),
            status_code=resp.status,
            headers=response_headers,
            media_type=resp.headers.get('Content-Type')
        )

    except ClientError as e:
        logger.error(f"Proxy connection error to {target_url}: {e}")
        # 如果连接到上游服务器时就发生错误，返回一个 502 Bad Gateway 错误。
        return Response(content=f"Error connecting to upstream Emby server: {e}", status_code=502)