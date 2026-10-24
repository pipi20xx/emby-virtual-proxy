# src/proxy_server.py (最终修复版)

import asyncio
import logging
from contextlib import asynccontextmanager
import aiohttp
# 【【【 修改这一行 】】】
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Tuple, Dict

# 【【【 同时修改这一行，从 proxy_cache 导入两个缓存实例 】】】
from proxy_cache import api_cache, vlib_items_cache
import config_manager
from proxy_handlers import (
    handler_system, 
    handler_views, 
    handler_items,
    handler_seasons,
    handler_episodes,
    handler_default,
    handler_latest,
    handler_home_sections,
    handler_images,
    handler_virtual_items
)

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def get_cache_key(request: Request, full_path: str) -> str:
    if request.method != "GET": return None
    params = dict(request.query_params); params.pop("X-Emby-Token", None); params.pop("api_key", None)
    sorted_params = tuple(sorted(params.items())); user_id_from_path = "public"
    if "/Users/" in full_path:
        try: parts = full_path.split("/"); user_id_from_path = parts[parts.index("Users") + 1]
        except (ValueError, IndexError): pass
    user_id = params.get("UserId", user_id_from_path)
    return f"user:{user_id}:path:{full_path}:params:{sorted_params}"

@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.aiohttp_session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()); logger.info("Global AIOHTTP ClientSession created.")
    yield
    await app.state.aiohttp_session.close(); logger.info("Global AIOHTTP ClientSession closed.")

proxy_app = FastAPI(title="Emby Virtual Proxy - Core", lifespan=lifespan)

covers_dir = Path("/app/config/images")
if covers_dir.is_dir():
    proxy_app.mount("/covers", StaticFiles(directory=str(covers_dir)), name="generated_covers")
    logger.info(f"Successfully mounted generated covers directory at /covers")
else:
    logger.warning(f"Generated covers directory not found at {covers_dir}, skipping mount.")

@proxy_app.get("/api/internal/get-cached-items/{library_id}")
async def get_cached_items_for_admin(library_id: str):
    """
    一个内部API，专门用于给admin服务提供已缓存的虚拟库项目列表。
    """
    cached_items = vlib_items_cache.get(library_id)
    if not cached_items:
        logger.warning(f"Admin请求缓存，但未找到库 {library_id} 的缓存。")
        raise HTTPException(
            status_code=404,
            detail="未找到该虚拟库的项目缓存。请先在Emby客户端中实际浏览一次该虚拟库，以生成缓存数据。"
        )
    
    logger.info(f"Admin成功获取到库 {library_id} 的 {len(cached_items)} 条缓存项目。")
    return JSONResponse(content={"Items": cached_items})
# --- 【【【 核心修复：重写 WebSocket 代理 】】】 ---
@proxy_app.websocket("/{full_path:path}")
async def websocket_proxy(client_ws: WebSocket, full_path: str):
    await client_ws.accept()
    config = config_manager.load_config()
    target_url = config.emby_url.replace("http", "ws", 1).rstrip('/') + "/" + full_path
    
    session = proxy_app.state.aiohttp_session
    try:
        headers = {k: v for k, v in client_ws.headers.items() if k.lower() not in ['host', 'connection', 'upgrade', 'sec-websocket-key', 'sec-websocket-version']}
        
        async with session.ws_connect(target_url, params=client_ws.query_params, headers=headers) as server_ws:

            async def forward_client_to_server():
                try:
                    while True:
                        # FastAPI WebSocket 使用 .receive_text() 或 .receive_bytes()
                        data = await client_ws.receive_text()
                        await server_ws.send_str(data)
                except WebSocketDisconnect:
                    logger.debug("Client WebSocket disconnected.")
                except Exception as e:
                    logger.debug(f"Error forwarding C->S: {e}")

            async def forward_server_to_client():
                try:
                    # aiohttp ClientWebSocketResponse 是一个异步迭代器
                    async for msg in server_ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await client_ws.send_text(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await client_ws.send_bytes(msg.data)
                except Exception as e:
                    logger.debug(f"Error forwarding S->C: {e}")

            # 并发运行两个转发任务
            await asyncio.gather(forward_client_to_server(), forward_server_to_client())

    except Exception as e:
        logger.warning(f"WebSocket proxy error for path '{full_path}': {e}")
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass
# --- 【【【 修复结束 】】】 ---


@proxy_app.api_route("/{full_path:path}", methods=["GET", "POST", "DELETE", "PUT"])
async def reverse_proxy(request: Request, full_path: str):
    config = config_manager.load_config()
    real_emby_url = config.emby_url.rstrip('/')
    
    cache_key = None
    if config.enable_cache:
        cache_key = get_cache_key(request, full_path)
        if cache_key and cache_key in api_cache:
            cached_response_data = api_cache.get(cache_key)
            if cached_response_data:
                content, status, headers = cached_response_data
                logger.info(f"✅ Cache HIT for key: {cache_key}")
                return Response(content=content, status_code=status, headers=headers)
        if cache_key:
            logger.info(f"❌ Cache MISS for key: {cache_key}")

    proxy_address = f"{request.url.scheme}://{request.url.netloc}"
    session = request.app.state.aiohttp_session
    response = None

    if not response: response = await handler_images.handle_virtual_library_image(request, full_path)
    if not response: response = await handler_virtual_items.handle_get_virtual_item_info(request, full_path, config)
    if not response: response = await handler_latest.handle_home_latest_items(request, full_path, request.method, real_emby_url, session, config)
    # Emby 4.10+ 首页分区接口(我的媒体磁贴/各库最新横排),必须在默认转发之前拦截
    if not response: response = await handler_home_sections.handle_home_sections(request, full_path, request.method, real_emby_url, session, config)
    if not response: response = await handler_system.handle_system_and_playback_info(request, full_path, request.method, real_emby_url, proxy_address, session)
    if not response: response = await handler_episodes.handle_episodes_merge(request, full_path, session, real_emby_url)
    if not response: response = await handler_seasons.handle_seasons_merge(request, full_path, session, real_emby_url)
    if not response: response = await handler_items.handle_virtual_library_items(request, full_path, request.method, real_emby_url, session, config)
    if not response: response = await handler_views.handle_view_injection(request, full_path, request.method, real_emby_url, session, config)
    if not response: response = await handler_default.forward_request(request, full_path, request.method, real_emby_url, session)

    if config.enable_cache and cache_key and response and response.status_code == 200 and not isinstance(response, StreamingResponse):
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            # For aiohttp responses, we need to read the body before caching
            if hasattr(response, 'body'):
                response_body = response.body
            else: # For regular FastAPI responses
                response_body = await response.body()

            response_to_cache: Tuple[bytes, int, Dict] = (response_body, response.status_code, dict(response.headers))
            api_cache[cache_key] = response_to_cache
            logger.info(f"📝 Cache SET for key: {cache_key}")
            
            # Since body is already read, return a new Response object
            return Response(content=response_body, status_code=response.status_code, headers=response.headers)

    return response
