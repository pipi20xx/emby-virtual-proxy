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
import db_manager
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
    handler_virtual_items,
    handler_autogen
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
    # 启动预热:延迟到服务开始监听后,为缺少封面的虚拟库补生成
    prewarm_task = asyncio.create_task(_prewarm_missing_covers())
    yield
    prewarm_task.cancel()
    await app.state.aiohttp_session.close(); logger.info("Global AIOHTTP ClientSession closed.")

proxy_app = FastAPI(title="Emby Virtual Proxy - Core", lifespan=lifespan)

# 代理核心自身监听地址(供内部端点自调用走完整 handler 链)
_SELF_PROXY_URL = "http://localhost:8999"

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
    优先读内存,未命中回退 SQLite 持久层(重启不丢)。
    """
    cached_items = vlib_items_cache.get(library_id)
    if not cached_items:
        cached_items = db_manager.load_vlib_items_cache(library_id)
        if cached_items:
            vlib_items_cache[library_id] = cached_items  # 回填内存,后续请求免读盘
            logger.info(f"Admin请求缓存,已从 SQLite 恢复库 {library_id} 的 {len(cached_items)} 条项目。")
    if not cached_items:
        logger.warning(f"Admin请求缓存，但内存与 SQLite 均未找到库 {library_id} 的缓存。")
        raise HTTPException(
            status_code=404,
            detail="未找到该虚拟库的项目缓存。可通过 /api/internal/refresh-vlib-items 主动预热,或在 Emby 客户端中浏览一次该库。"
        )

    logger.info(f"Admin成功获取到库 {library_id} 的 {len(cached_items)} 条缓存项目。")
    return JSONResponse(content={"Items": cached_items})


async def _resolve_default_user_id(config) -> str | None:
    """从 Emby 解析一个可用用户(取第一个),供内部请求模拟客户端身份。"""
    try:
        async with proxy_app.state.aiohttp_session.get(
            f"{config.emby_url.rstrip('/')}/emby/Users",
            headers={"X-Emby-Token": config.emby_api_key or ""},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status == 200:
                users = await resp.json(content_type=None)
                if isinstance(users, list) and users:
                    return users[0].get("Id")
    except Exception as e:
        logger.warning(f"解析 Emby 用户列表失败: {e}")
    return None


async def _fetch_vlib_items_via_self(config, user_id: str, library_id: str) -> list:
    """以模拟客户端身份自调用代理的 items 接口,主动触发虚拟库规则解析与 Emby 查询。

    与 handler_autogen 的内部请求同源;响应体直接返回项目列表。
    """
    params = {
        "ParentId": library_id,
        "Limit": 100,
        "Fields": "ImageTags,ProviderIds",
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series,Video",
        "X-Emby-Token": config.emby_api_key,
        "X-Emby-Client": "Proxy (Internal Refresh)",
        "X-Emby-Device-Name": "ProxyInternalRefresh",
        "X-Emby-Device-Id": "proxy-internal-refresh",
        "X-Emby-Client-Version": "4.8.11.0",
    }
    internal_headers = {
        'Accept': 'application/json',
        'X-Emby-Authorization': f'Emby UserId="{user_id}", Client="Proxy (Internal Refresh)", Device="ProxyInternalRefresh", DeviceId="proxy-internal-refresh", Version="4.8.11.0", Token="{config.emby_api_key}"'
    }
    try:
        async with proxy_app.state.aiohttp_session.get(
            f"{_SELF_PROXY_URL}/emby/Users/{user_id}/Items",
            params=params, headers=internal_headers, timeout=aiohttp.ClientTimeout(total=90),
        ) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                if isinstance(data, dict):
                    return data.get("Items", []) or []
            else:
                logger.warning(f"内部预热请求失败 (Status: {resp.status}): {await resp.text()}")
    except Exception as e:
        logger.warning(f"内部预热请求出错: {e}")
    return []


@proxy_app.post("/api/internal/refresh-vlib-items/{library_id}")
async def refresh_vlib_items(library_id: str):
    """主动预热虚拟库项目缓存(内存 + SQLite),不依赖客户端访问。

    admin 面板封面生成在缓存 miss 时调用;RSS 库同样适用(走 RssHandler)。
    """
    config = config_manager.load_config()
    vlib = next((v for v in config.virtual_libraries if v.id == library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="虚拟库不存在")

    user_id = await _resolve_default_user_id(config)
    if not user_id:
        raise HTTPException(status_code=502, detail="无法从 Emby 解析可用用户,请检查 emby_url / emby_api_key 配置")

    items = await _fetch_vlib_items_via_self(config, user_id, library_id)
    if not items:
        raise HTTPException(status_code=502, detail="未能从 Emby 获取到该虚拟库的任何项目,请检查库规则或 RSS 数据")

    vlib_items_cache[library_id] = items
    db_manager.save_vlib_items_cache(library_id, items)
    logger.info(f"主动预热完成:库 '{vlib.name}' ({library_id}) 已缓存 {len(items)} 个项目。")
    return JSONResponse(content={"Items": items, "TotalRecordCount": len(items)})


async def _trigger_autogen_cover(library_id: str) -> dict:
    """为虚拟库触发一次后台封面自动生成(带运行中去重)。"""
    config = config_manager.load_config()
    if not next((v for v in config.virtual_libraries if v.id == library_id), None):
        raise HTTPException(status_code=404, detail="虚拟库不存在")
    if library_id in handler_autogen.GENERATION_IN_PROGRESS:
        return {"success": True, "message": "该库已有封面生成任务在运行"}

    user_id = await _resolve_default_user_id(config)
    if not user_id:
        raise HTTPException(status_code=502, detail="无法从 Emby 解析可用用户,请检查 emby_url / emby_api_key 配置")

    asyncio.create_task(handler_autogen.generate_poster_in_background(library_id, user_id, config.emby_api_key))
    logger.info(f"已为虚拟库 {library_id} 触发封面自动生成后台任务。")
    return {"success": True, "message": "封面生成任务已启动"}


@proxy_app.post("/api/internal/autogen-cover/{library_id}")
async def autogen_cover_for_library(library_id: str):
    """主动为虚拟库生成封面(保存库/启动预热时调用,不依赖客户端访问)。"""
    return await _trigger_autogen_cover(library_id)


async def _prewarm_missing_covers():
    """启动预热:等待本服务开始监听后,为缺少封面文件的虚拟库逐个补生成。"""
    await asyncio.sleep(10)
    try:
        config = config_manager.load_config()
    except Exception as e:
        logger.warning(f"启动预热:读取配置失败,跳过: {e}")
        return
    for vlib in config.virtual_libraries:
        try:
            if handler_images.cover_file_exists(vlib.id):
                continue
            logger.info(f"启动预热:发现虚拟库 '{vlib.name}' ({vlib.id}) 缺少封面,尝试自动生成。")
            await _trigger_autogen_cover(vlib.id)
        except Exception as e:
            logger.warning(f"启动预热:为库 {vlib.id} 触发生成失败: {e}")
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
