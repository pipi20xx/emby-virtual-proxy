# src/proxy_handlers/handler_system.py
import json
import logging
from fastapi import Request, Response
from aiohttp import ClientSession

from emby_net import emby_proxy_of

logger = logging.getLogger(__name__)

async def handle_system_and_playback_info(
    request: Request,
    full_path: str,
    method: str,
    real_emby_url: str,
    proxy_address: str,
    session: ClientSession
) -> Response | None:
    """
    拦截 /System/Info 和 /PlaybackInfo 请求，重写其中的 URL。
    如果不匹配，则返回 None。
    """
    target_url = f"{real_emby_url}/{full_path}"
    headers = dict(request.headers)
    params = request.query_params
    data = await request.body()
    
    # 关键功能 1: 欺骗客户端，使其始终通过代理通信
    if ("/System/Info" in full_path or "/system/info/public" in full_path) and method == "GET":
        logger.info(f"Intercepting /System/Info to rewrite URLs for path: {full_path}")
        async with session.get(target_url, params=params, headers=headers, proxy=emby_proxy_of(request)) as resp:
            if resp.status == 200:
                content_text = await resp.text()
                modified_content_text = content_text.replace(real_emby_url, proxy_address)
                final_content = modified_content_text.encode('utf-8')
                response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding', 'content-length')}
                return Response(content=final_content, status_code=resp.status, headers=response_headers)
            # 如果请求失败，让默认处理器来处理
            return None

    # 关键功能 2: 重写播放信息中的地址 (已禁用)
    # if "/PlaybackInfo" in full_path:
    #     logger.info(f"Intercepting /PlaybackInfo to rewrite stream URLs for path: {full_path}")
    #     async with session.request(method, target_url, params=params, headers=headers, data=data) as resp:
    #         if resp.status == 200 and "application/json" in resp.headers.get("Content-Type", ""):
    #             content_json = await resp.json()
    #             if "MediaSources" in content_json and isinstance(content_json["MediaSources"], list):
    #                 for source in content_json["MediaSources"]:
    #                     for key, value in source.items():
    #                         if isinstance(value, str) and real_emby_url in value:
    #                             source[key] = value.replace(real_emby_url, proxy_address)
                
    #             final_content = json.dumps(content_json).encode('utf-8')
    #             response_headers = {k: v for k, v in resp.headers.items() if k.lower() not in ('transfer-encoding', 'connection', 'content-encoding', 'content-length')}
    #             return Response(content=final_content, status_code=resp.status, media_type="application/json")
    #         # 如果不是我们关心的类型，让默认处理器来处理
    #         return None
            
    return None # 此处理器不处理该请求
