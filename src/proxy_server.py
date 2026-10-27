# src/proxy_server.py (最终修复版)

import asyncio
import logging
import time
from contextlib import asynccontextmanager
import aiohttp
from emby_net import emby_proxy_from_config
# 【【【 修改这一行 】】】
from fastapi import FastAPI, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
from typing import Tuple, Dict, List, Optional

from logger import init_logging, LogBroadcaster

# 日志初始化须先于下方项目模块导入,导入期的日志才能进入文件与实时流
init_logging("proxy", level=logging.DEBUG)

# 【【【 同时修改这一行，从 proxy_cache 导入两个缓存实例 】】】
# api_cache 经 proxy_cache.api_cache 间接访问(TTL 可配置时会重建实例)
import proxy_cache
from proxy_cache import vlib_items_cache, vlib_metadata_cache, user_overlay_cache, vlib_base_cache
import config_manager
import db_manager
from services import tmdb_index_service
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
    # 记录主事件循环,后台工作线程产生的日志经此回投进实时广播
    LogBroadcaster.set_loop(asyncio.get_running_loop())
    app.state.aiohttp_session = aiohttp.ClientSession(cookie_jar=aiohttp.DummyCookieJar()); logger.info("Global AIOHTTP ClientSession created.")
    # 按配置应用 API 响应缓存 TTL 与合并结果缓存开关
    _startup_config = config_manager.load_config()
    proxy_cache.set_api_cache_ttl(_startup_config.api_cache_ttl)
    proxy_cache.merge_cache_enabled = bool(getattr(_startup_config, "enable_merge_cache", True))
    # 启动预热:延迟到服务开始监听后(回填缓存/补封面/按配置全量刷新)
    prewarm_task = asyncio.create_task(_startup_warmup())
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
    优先读内存,未命中回退 SQLite 持久层(重启不丢);缓存功能关闭时直接返回未命中。
    """
    config = config_manager.load_config()
    if not config.enable_vlib_cache:
        raise HTTPException(status_code=404, detail="虚拟库缓存功能已禁用,封面素材可改用上传图片或自定义图片目录。")

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


async def _list_emby_users(config) -> List[str]:
    """从 Emby 获取全部用户 ID(封顶 10 个,家庭场景足够)。"""
    try:
        async with proxy_app.state.aiohttp_session.get(
            f"{config.emby_url.rstrip('/')}/emby/Users",
            headers={"X-Emby-Token": config.emby_api_key or ""},
            timeout=aiohttp.ClientTimeout(total=15),
            proxy=emby_proxy_from_config(config),
        ) as resp:
            if resp.status == 200:
                users = await resp.json(content_type=None)
                if isinstance(users, list):
                    return [u.get("Id") for u in users if u.get("Id")][:10]
    except Exception as e:
        logger.warning(f"解析 Emby 用户列表失败: {e}")
    return []


async def _resolve_default_user_id(config) -> Optional[str]:
    """从 Emby 解析一个可用用户(取第一个),供内部请求模拟客户端身份。"""
    users = await _list_emby_users(config)
    return users[0] if users else None


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


async def _refresh_vlib_items_internal(config, library_id: str) -> list:
    """主动向 Emby 拉取虚拟库项目并双写缓存(内存 + SQLite);失败抛 HTTPException。

    供内部端点与启动预热复用;RSS 库同样适用(经 RssHandler 读取其持久数据)。
    """
    vlib = next((v for v in config.virtual_libraries if v.id == library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="虚拟库不存在")

    user_id = await _resolve_default_user_id(config)
    if not user_id:
        raise HTTPException(status_code=502, detail="无法从 Emby 解析可用用户,请检查 emby_url / emby_api_key 配置")

    internal_headers = {"Accept": "application/json", "X-Emby-Token": config.emby_api_key or ""}
    items: List[dict] = []

    if vlib.resource_type != "rsshub" and config.enable_vlib_local_serving:
        # 规则库:所有库共享同一份「全库基础数据源」,各库只在本地派生自己的结果;
        # 各用户仅做轻量 UserData 叠加层(按数据源一份,多库共用)
        source_key = handler_items.base_source_key(vlib)
        # 整轮刷新里只重拉一次数据源:第一轮拉完后 60 秒内的后续库直接复用,
        # 避免 N 个虚拟库把同一份全库数据源拉 N 遍。
        base_entry = handler_items.get_base_items(source_key)
        base_fresh_enough = bool(base_entry) and (int(time.time()) - int(base_entry["updated_at"])) <= 60
        items = await handler_items.resolve_shared_metadata(
            config, vlib, user_id, proxy_app.state.aiohttp_session,
            config.emby_url.rstrip('/'), internal_headers,
            proxy=emby_proxy_from_config(config),
            force_base=not base_fresh_enough,
        )
        if not items:
            raise HTTPException(status_code=502, detail="未能从 Emby 获取到该虚拟库的任何项目,请检查库规则")

        if config.enable_vlib_cache:
            handler_items.store_shared_metadata(vlib, items)
            # 封面素材缓存沿用元数据前 100 项
            cover_items = items[:100]
            vlib_items_cache[library_id] = cover_items
            db_manager.save_vlib_items_cache(library_id, cover_items)

            all_users = await _list_emby_users(config)
            if user_id not in all_users:
                all_users.insert(0, user_id)
            for uid in all_users:
                # 叠加层按数据源共享:同一轮预热里若有别的库已建过,直接跳过
                if handler_items.overlay_is_fresh(source_key, uid):
                    continue
                try:
                    count = await handler_items.build_and_store_user_overlay(
                        config, vlib, uid, proxy_app.state.aiohttp_session,
                        config.emby_url.rstrip('/'), internal_headers,
                        proxy=emby_proxy_from_config(config),
                    )
                    if count is None:
                        logger.warning(f"叠加层预热失败:库 '{vlib.name}' 用户 {uid}。")
                    else:
                        logger.info(f"叠加层预热:数据源 '{source_key}' 用户 {uid} 已叠加 {count} 项用户状态。")
                except Exception as e:
                    logger.warning(f"叠加层预热失败 ('{vlib.name}' 用户 {uid}): {e}")

        logger.info(f"主动预热完成:库 '{vlib.name}' ({library_id}) 解析 {len(items)} 个项目。")
        return items

    # RSS 库(或直出关闭):沿用内部自调用拉取,仅维护封面素材缓存
    items = await _fetch_vlib_items_via_self(config, user_id, library_id)
    if not items:
        raise HTTPException(status_code=502, detail="未能从 Emby 获取到该虚拟库的任何项目,请检查库规则或 RSS 数据")

    # 缓存功能关闭时只返回结果供封面生成按需取材,不落任何缓存
    if config.enable_vlib_cache:
        vlib_items_cache[library_id] = items
        db_manager.save_vlib_items_cache(library_id, items)
        logger.info(f"主动预热完成:库 '{vlib.name}' ({library_id}) 已缓存 {len(items)} 个项目。")
    else:
        logger.info(f"缓存功能已禁用:库 '{vlib.name}' ({library_id}) 按需获取 {len(items)} 个项目(未缓存)。")

    return items


@proxy_app.post("/api/internal/refresh-vlib-items/{library_id}")
async def refresh_vlib_items(library_id: str):
    """主动预热虚拟库项目缓存(内存 + SQLite),不依赖客户端访问。"""
    config = config_manager.load_config()
    items = await _refresh_vlib_items_internal(config, library_id)
    return JSONResponse(content={"Items": items, "TotalRecordCount": len(items)})


@proxy_app.get("/api/internal/vlib-source-hash/{library_id}")
async def get_vlib_source_hash(library_id: str):
    """计算虚拟库当前内容指纹(条目 Id 集合 md5),供手动生成封面时锚定自动刷新基线。"""
    config = config_manager.load_config()
    vlib = next((v for v in config.virtual_libraries if v.id == library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="虚拟库不存在")

    user_id = await _resolve_default_user_id(config)
    if not user_id:
        raise HTTPException(status_code=502, detail="无法从 Emby 解析可用用户,请检查 emby_url / emby_api_key 配置")

    items = await handler_autogen.load_cover_source_items(library_id, vlib, user_id, config.emby_api_key)
    if not items:
        raise HTTPException(status_code=502, detail="未能从 Emby 获取到该虚拟库的任何项目,无法计算内容指纹")

    return {"source_hash": handler_autogen.compute_source_hash(items), "item_count": len(items)}


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


@proxy_app.post("/api/internal/rebuild-vlib/{library_id}")
async def rebuild_vlib(library_id: str):
    """改库后的一次性重建:失效旧缓存 → 重新解析数据源/元数据/叠加层 → 触发封面刷新。

    供 admin 在「保存虚拟库」后调用,保证规则改动立即生效(不必等软过期)。
    """
    await invalidate_vlib_cache(library_id)

    config = config_manager.load_config()
    vlib = next((v for v in config.virtual_libraries if v.id == library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="虚拟库不存在")

    items = await _refresh_vlib_items_internal(config, library_id)
    logger.info(f"重建完成:库 '{vlib.name}' 共 {len(items)} 项,封面将按新内容指纹刷新。")

    try:
        await _trigger_autogen_cover(library_id)
    except Exception as e:
        logger.warning(f"重建后触发封面刷新失败 ({library_id}): {e}")

    return {"success": True, "library_id": library_id, "item_count": len(items)}


@proxy_app.post("/api/internal/invalidate-vlib/{library_id}")
async def invalidate_vlib_cache(library_id: str):
    """精准失效某个虚拟库的缓存(改库规则/删库后由 admin 调用)。

    清掉:该库的派生元数据、封面素材、结果层缓存 + 对应 SQLite 行,
    以及全库数据源(规则变化后其派生结果不再适用)与合并结果缓存;
    同时清空 API 响应缓存,避免旧列表在 TTL 内继续被返回。
    """
    proxy_cache.drop_vlib_memory(library_id)
    vlib_base_cache.clear()
    proxy_cache.merge_result_cache.clear()
    proxy_cache.api_cache.clear()

    db_manager.delete_vlib_items_cache(library_id)
    db_manager.delete_vlib_metadata(library_id)
    db_manager.delete_vlib_user_overlay(library_id)
    db_manager.delete_vlib_base(handler_items.base_source_key())

    logger.info(f"已失效虚拟库 {library_id} 的全部缓存(内存 + SQLite + 响应缓存)。")
    return {"success": True, "library_id": library_id}


@proxy_app.post("/api/internal/clear-vlib-cache")
async def clear_vlib_cache():
    """清空代理核心内存中的虚拟库缓存(admin 侧同时清 SQLite 持久层)。"""
    proxy_cache.clear_vlib_caches()
    # 响应缓存里可能存的是直出结果,不清就会在 TTL 内继续返回旧列表
    proxy_cache.api_cache.clear()
    logger.info("已清空内存中的全库数据源、共享元数据、用户叠加层、结果层与响应缓存。")
    return {"success": True}


@proxy_app.post("/api/internal/apply-cache-ttl/{ttl_seconds:int}")
async def apply_cache_ttl(ttl_seconds: int):
    """按配置重建 API 响应缓存(保存设置时由 admin 调用,即时生效)。"""
    proxy_cache.set_api_cache_ttl(ttl_seconds)
    logger.info(f"API 响应缓存 TTL 已应用: {ttl_seconds}s")
    return {"success": True}


@proxy_app.post("/api/internal/rebuild-tmdb-index")
async def rebuild_tmdb_index():
    """按配置重建 TMDB 索引(定时任务/预热调用;内部自判开关与新鲜度,仍新鲜则跳过)。"""
    config = config_manager.load_config()
    result = await tmdb_index_service.rebuild_if_stale(
        proxy_app.state.aiohttp_session, config.emby_url.rstrip('/'),
        {"Accept": "application/json", "X-Emby-Token": config.emby_api_key or ""},
    )
    return {"success": True, **result}


async def _startup_warmup():
    """启动预热,三段式:

    1. SQLite -> 内存回填(零 Emby 开销,重启后缓存即刻可用);
    2. 为缺少封面文件的虚拟库补触发自动生成;
    3. 启用 vlib_cache_refresh_interval 时刷新缓存,但只刷已超过该间隔的库(不做无谓的 Emby 压力)。
    """
    await asyncio.sleep(10)
    try:
        config = config_manager.load_config()
    except Exception as e:
        logger.warning(f"启动预热:读取配置失败,跳过: {e}")
        return

    # 1. 持久缓存回填内存(缓存功能关闭时跳过)
    if config.enable_vlib_cache:
        for vlib in config.virtual_libraries:
            if vlib_items_cache.get(vlib.id):
                continue
            persisted = db_manager.load_vlib_items_cache(vlib.id)
            if persisted:
                vlib_items_cache[vlib.id] = persisted
                logger.info(f"启动预热:已从 SQLite 恢复库 '{vlib.name}' 的 {len(persisted)} 条项目缓存。")
        if config.enable_vlib_local_serving:
            # 全库数据源:所有虚拟库共享,回填后各库派生都不必等 Emby
            source_key = handler_items.base_source_key()
            base = handler_items.get_base_items(source_key)
            if base:
                logger.info(f"启动预热:已从 SQLite 恢复全库数据源({len(base['items'])} 项),各虚拟库共享复用。")
            for vlib in config.virtual_libraries:
                if vlib.id in vlib_metadata_cache:
                    continue
                persisted_meta = db_manager.load_vlib_metadata(vlib.id)
                if persisted_meta:
                    vlib_metadata_cache[vlib.id] = {"items": persisted_meta[0], "updated_at": persisted_meta[1]}
                    logger.info(f"启动预热:已从 SQLite 恢复库 '{vlib.name}' 的共享元数据({len(persisted_meta[0])} 项)。")

    # 2. 缺封面的库补生成
    for vlib in config.virtual_libraries:
        try:
            if handler_images.cover_file_exists(vlib.id):
                continue
            logger.info(f"启动预热:发现虚拟库 '{vlib.name}' ({vlib.id}) 缺少封面,尝试自动生成。")
            await _trigger_autogen_cover(vlib.id)
        except Exception as e:
            logger.warning(f"启动预热:为库 {vlib.id} 触发生成失败: {e}")

    # 3. 启用定时预热时,启动先全量刷新一轮(缓存功能关闭时跳过)
    if config.enable_vlib_cache and config.vlib_cache_refresh_interval and config.vlib_cache_refresh_interval > 0:
        interval_seconds = int(config.vlib_cache_refresh_interval) * 3600
        logger.info(f"启动预热:开始刷新虚拟库缓存(仅过期库,阈值 {config.vlib_cache_refresh_interval}h)...")
        for vlib in config.virtual_libraries:
            try:
                meta = handler_items.get_shared_metadata(vlib.id)
                if meta and (int(time.time()) - int(meta["updated_at"])) < interval_seconds:
                    age_minutes = (int(time.time()) - int(meta["updated_at"])) // 60
                    logger.info(f"启动预热:库 '{vlib.name}' 缓存仍新鲜({age_minutes} 分钟前),跳过刷新。")
                    continue
                items = await _refresh_vlib_items_internal(config, vlib.id)
                logger.info(f"启动预热:库 '{vlib.name}' 已刷新 {len(items)} 个项目。")
                # 数据刷新后顺势触发封面自动刷新(内容指纹未变化时内部会跳过)
                await _trigger_autogen_cover(vlib.id)
            except HTTPException as e:
                logger.warning(f"启动预热:刷新库 '{vlib.name}' 失败: {e.detail}")
            except Exception as e:
                logger.warning(f"启动预热:刷新库 '{vlib.id}' 出错: {e}")

    # 4. TMDB 索引:按配置判定(为空/超重建间隔时重建;开关关闭则跳过)
    try:
        result = await tmdb_index_service.rebuild_if_stale(
            proxy_app.state.aiohttp_session, config.emby_url.rstrip('/'),
            {"Accept": "application/json", "X-Emby-Token": config.emby_api_key or ""},
        )
        if result.get("rebuilt"):
            logger.info(f"启动预热:TMDB 索引已重建,共 {result['count']} 条。")
        elif result.get("reason") == "disabled":
            logger.info("启动预热:TMDB 索引功能已禁用,跳过。")
        else:
            logger.info(f"启动预热:TMDB 索引仍然新鲜({result['count']} 条),跳过重建。")
    except Exception as e:
        logger.warning(f"启动预热:TMDB 索引重建失败: {e}")
# --- 【【【 核心修复：重写 WebSocket 代理 】】】 ---
@proxy_app.websocket("/{full_path:path}")
async def websocket_proxy(client_ws: WebSocket, full_path: str):
    await client_ws.accept()
    config = config_manager.load_config()
    target_url = config.emby_url.replace("http", "ws", 1).rstrip('/') + "/" + full_path
    
    session = proxy_app.state.aiohttp_session
    try:
        headers = {k: v for k, v in client_ws.headers.items() if k.lower() not in ['host', 'connection', 'upgrade', 'sec-websocket-key', 'sec-websocket-version']}

        async with session.ws_connect(target_url, params=client_ws.query_params, headers=headers,
                                      proxy=emby_proxy_from_config(config)) as server_ws:

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

# 用户状态型写操作(标记已看/收藏)路径标记:命中后失效该用户的状态缓存
_USER_STATE_WRITE_MARKERS = ("PlayedItems", "FavoriteItems")


def _maybe_invalidate_user_state(request: Request, full_path: str, response) -> None:
    """播放/收藏状态变更后,丢弃该用户的叠加层与响应缓存(下次浏览即重新拉取真实状态)。

    只处理"状态型"写操作;播放进度上报(PlayingItems)频率极高且影响很小,故意不在失效范围。
    """
    if request.method == "GET" or response is None:
        return
    if not any(marker in full_path for marker in _USER_STATE_WRITE_MARKERS):
        return
    if not (200 <= int(getattr(response, "status_code", 500)) < 300):
        return

    user_id = request.query_params.get("UserId")
    if not user_id:
        parts = full_path.split('/')
        if 'Users' in parts:
            try:
                idx = parts.index('Users') + 1
                if idx < len(parts):
                    user_id = parts[idx]
            except (ValueError, IndexError):
                pass
    if not user_id:
        return

    handler_items.invalidate_user_overlay(user_id)
    # 列表响应里的 TotalRecordCount / UserData 已不可信,一并清掉响应缓存
    proxy_cache.api_cache.clear()
    logger.info(f"用户 {user_id[:8]}… 的播放/收藏状态已变更,其叠加层与响应缓存已失效。")


@proxy_app.api_route("/{full_path:path}", methods=["GET", "POST", "DELETE", "PUT"])
async def reverse_proxy(request: Request, full_path: str):
    config = config_manager.load_config()
    real_emby_url = config.emby_url.rstrip('/')
    # 本请求访问 Emby 上游应使用的代理(留空直连);handler 内通过 emby_proxy_of(request) 读取
    request.state.emby_proxy = emby_proxy_from_config(config)
    request.state.real_emby_url = real_emby_url
    # 运行时开关随配置同步(handler 里不再为此深拷贝一份配置)
    proxy_cache.merge_cache_enabled = bool(getattr(config, "enable_merge_cache", True))
    
    cache_key = None
    if config.enable_cache and not proxy_cache.api_cache_disabled:
        cache_key = get_cache_key(request, full_path)
        if cache_key and cache_key in proxy_cache.api_cache:
            cached_response_data = proxy_cache.api_cache.get(cache_key)
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

    # 播放/收藏状态写操作成功后,立即失效该用户的叠加层(否则"已看/未看"最长要等 10 分钟)
    _maybe_invalidate_user_state(request, full_path, response)

    if config.enable_cache and not proxy_cache.api_cache_disabled and cache_key and response and response.status_code == 200 and not isinstance(response, StreamingResponse):
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            # For aiohttp responses, we need to read the body before caching
            if hasattr(response, 'body'):
                response_body = response.body
            else: # For regular FastAPI responses
                response_body = await response.body()

            response_to_cache: Tuple[bytes, int, Dict] = (response_body, response.status_code, dict(response.headers))
            proxy_cache.api_cache[cache_key] = response_to_cache
            logger.info(f"📝 Cache SET for key: {cache_key}")
            
            # Since body is already read, return a new Response object
            return Response(content=response_body, status_code=response.status_code, headers=response.headers)

    return response
