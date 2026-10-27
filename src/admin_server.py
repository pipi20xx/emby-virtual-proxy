# src/admin_server.py —— 管理后台 API(纯薄路由层)
"""职责边界:
    本文件只做 HTTP 路由声明:解析请求 -> 调用 services/ 中的服务 -> 返回响应。
    业务逻辑全部在服务层,改动业务请去对应模块:
      - 封面生成       -> services/cover_service.py   (样式插件契约见 cover_generator/__init__.py)
      - RSS 刷新/调度  -> services/rss_service.py
      - 虚拟库 CRUD    -> services/library_service.py
      - Emby API 访问  -> services/emby_client.py
      - 容器重启       -> services/system_service.py
      - 高级筛选器     -> advanced_filters/ (规则翻译) + config.json (存储)
    所有路由的路径/方法/状态码/请求响应结构是前端 (frontend/src/api/index.js)
    的调用契约,保持与重构前一致,勿随意改动。
"""

import asyncio
import logging
import os
import sys
from typing import List, Optional

import aiohttp
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request, Response, Query, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from logger import init_logging, LogBroadcaster

# 日志初始化须先于下方项目模块导入,导入期的日志才能进入文件与实时流
init_logging("admin", level=logging.INFO, console_format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

import app_paths
import config_manager
from models import AppConfig, VirtualLibrary, AdvancedFilter
from services import auth_service, cover_service, library_service, log_service, rss_service, system_service, vlib_cache_service
from services.emby_client import fetch_items

logger = logging.getLogger(__name__)

admin_app = FastAPI(title="Emby Virtual Proxy - Admin API")


async def _auth_guard(request: Request):
    """面板访问控制:config.admin_password 留空时放行;/api/auth/* 自身放行。

    只作用于 /api/* 路由;静态页面与 /covers 由应用级挂载,不经过此守卫。
    代理核心(8999)是独立进程,完全不受影响。
    """
    if request.url.path.startswith("/api/auth/"):
        return
    config = config_manager.load_config()
    if not auth_service.is_enabled(config):
        return
    token = request.headers.get("X-Auth-Token") or ""
    if not auth_service.verify(token):
        raise HTTPException(status_code=401, detail="未登录或会话已过期")


api_router = APIRouter(prefix="/api", dependencies=[Depends(_auth_guard)])


class CoverRequest(BaseModel):
    library_id: str
    title_zh: str
    title_en: Optional[str] = None
    style_name: str
    temp_image_paths: Optional[List[str]] = None
    animation_format: Optional[str] = None  # 动图样式的输出格式: gif | webp | png


class LoginRequest(BaseModel):
    password: str = ""


# --- 登录(守卫自身对 /api/auth/* 放行) ---

@api_router.post("/auth/login", tags=["Auth"])
async def auth_login(body: LoginRequest):
    config = config_manager.load_config()
    if not auth_service.is_enabled(config):
        return {"required": False, "token": None}
    token = auth_service.login(config, body.password)
    if not token:
        raise HTTPException(status_code=401, detail="密码错误")
    return {"required": True, "token": token}


@api_router.get("/auth/status", tags=["Auth"])
async def auth_status(request: Request):
    """前端进入面板前先查此接口决定是否需要登录。"""
    config = config_manager.load_config()
    required = auth_service.is_enabled(config)
    token = request.headers.get("X-Auth-Token") or ""
    return {"required": required, "authenticated": (not required) or auth_service.verify(token)}


@api_router.post("/auth/logout", status_code=204, tags=["Auth"])
async def auth_logout(request: Request):
    auth_service.logout(request.headers.get("X-Auth-Token") or "")
    return Response(status_code=204)


# --- 配置 ---

@api_router.get("/config", response_model=AppConfig, response_model_by_alias=True, tags=["Configuration"])
async def get_config():
    return config_manager.load_config()


@api_router.post("/config", response_model=AppConfig, response_model_by_alias=True, tags=["Configuration"])
async def update_config(config: AppConfig):
    config_manager.save_config(config)
    # 配置变化可能影响各定时刷新间隔,重建定时任务
    rss_service.reschedule_refresh_job(config)
    vlib_cache_service.reschedule_refresh_job(config)
    vlib_cache_service.reschedule_tmdb_index_job(config)
    # 响应缓存 TTL 变化需即时应用到代理核心
    await _apply_api_cache_ttl(config)
    return config


async def _apply_api_cache_ttl(config: AppConfig) -> None:
    proxy_core_url = os.getenv("PROXY_CORE_URL")
    if not proxy_core_url:
        return
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{proxy_core_url.rstrip('/')}/api/internal/apply-cache-ttl/{int(config.api_cache_ttl)}",
                timeout=aiohttp.ClientTimeout(total=10),
            ):
                pass
    except Exception as e:
        logger.warning(f"应用响应缓存 TTL 失败: {e}")


# --- 高级筛选器(存储于 config.json;规则翻译见 advanced_filters/) ---

@api_router.get("/advanced-filters", response_model=List[AdvancedFilter], tags=["Advanced Filters"])
async def get_advanced_filters():
    """获取所有高级筛选器规则"""
    return config_manager.load_config().advanced_filters


@api_router.post("/advanced-filters", status_code=204, tags=["Advanced Filters"])
async def save_advanced_filters(filters: List[AdvancedFilter]):
    """保存所有高级筛选器规则"""
    try:
        config = config_manager.load_config()
        config.advanced_filters = filters
        config_manager.save_config(config)
        return Response(status_code=204)
    except Exception as e:
        logger.error(f"保存高级筛选器时发生严重错误: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# --- 封面生成 ---

@api_router.post("/upload_temp_image", tags=["Cover Generator"])
async def upload_temp_image(file: UploadFile = File(...)):
    path = cover_service.save_temp_image(file)
    return {"path": str(path)}


@api_router.post("/generate-cover", tags=["Cover Generator"])
async def generate_cover(body: CoverRequest):
    try:
        image_tag, cover_format = await cover_service.generate_cover(
            library_id=body.library_id,
            title_zh=body.title_zh,
            title_en=body.title_en,
            style_name=body.style_name,
            temp_image_paths=body.temp_image_paths,
            animation_format=body.animation_format,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[COVER-GEN-ERROR] 封面生成过程中发生异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    if not image_tag:
        raise HTTPException(status_code=500, detail="封面生成失败，详见后端日志。")

    # 生成成功后把生成配方与 image_tag / cover_format 写回对应虚拟库:
    # 配方(样式/标题/动图格式)供内容变化后的自动重绘沿用同样外观
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == body.library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="未找到要更新封面的虚拟库。")
    vlib.image_tag = image_tag
    vlib.cover_format = cover_format
    vlib.cover_style = body.style_name
    vlib.cover_title_zh = body.title_zh
    vlib.cover_title_en = body.title_en
    vlib.cover_animation_format = body.animation_format
    vlib.cover_source_hash = await _fetch_vlib_source_hash(body.library_id)
    config_manager.save_config(config)
    return {
        "success": True,
        "image_tag": image_tag,
        "image_url": f"/covers/{body.library_id}.{cover_format}?t={image_tag}",
    }


async def _fetch_vlib_source_hash(library_id: str) -> Optional[str]:
    """向代理核心查询虚拟库当前内容指纹(失败返回 None,下次自动刷新会重建基线)。"""
    proxy_core_url = os.getenv("PROXY_CORE_URL")
    if not proxy_core_url:
        return None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{proxy_core_url.rstrip('/')}/api/internal/vlib-source-hash/{library_id}",
                timeout=aiohttp.ClientTimeout(total=90),
            ) as resp:
                if resp.status == 200:
                    return (await resp.json(content_type=None)).get("source_hash")
                logger.warning(f"获取虚拟库 {library_id} 内容指纹失败 (Status: {resp.status})")
    except Exception as e:
        logger.warning(f"获取虚拟库 {library_id} 内容指纹时出错: {e}")
    return None


@api_router.post("/covers/clear", status_code=204, tags=["Cover Generator"])
async def clear_all_covers():
    """清空所有生成的封面图并重置配置中的 image_tag"""
    try:
        cover_service.clear_cover_files()
        config = config_manager.load_config()
        for vlib in config.virtual_libraries:
            vlib.image_tag = None
            vlib.cover_format = None
        config_manager.save_config(config)
        logger.info("所有封面及配置已成功清除。")
        return Response(status_code=204)
    except Exception as e:
        logger.error(f"清空封面时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"清空封面时发生内部错误: {e}")


@api_router.post("/vlib-cache/clear", tags=["Libraries"])
async def clear_vlib_cache():
    """清空虚拟库项目缓存与直出解析缓存(SQLite 持久层 + 代理核心内存)"""
    try:
        counts = await vlib_cache_service.clear_all_caches()
        return {"success": True, **counts}
    except Exception as e:
        logger.error(f"清空虚拟库缓存时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"清空虚拟库缓存时发生内部错误: {e}")


# --- 缓存状态(转发代理核心的快照,供面板展示命中率/体积) ---

@api_router.get("/cache-stats", tags=["System"])
async def get_cache_stats():
    """返回代理核心的缓存状态与命中率;核心不可达时返回 available=False,不抛错。"""
    proxy_core_url = _proxy_core_url()
    if not proxy_core_url:
        return {"available": False, "reason": "未配置 PROXY_CORE_URL"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{proxy_core_url.rstrip('/')}/api/internal/cache-stats",
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return {"available": False, "reason": f"代理核心返回 {resp.status}"}
                data = await resp.json(content_type=None)
                data["available"] = True
                return data
    except Exception as e:
        return {"available": False, "reason": str(e)}


# --- RSS 虚拟库刷新 ---

@api_router.post("/libraries/{library_id}/refresh", status_code=202, tags=["Libraries"])
async def refresh_library_cache(library_id: str):
    """手动触发虚拟库刷新:RSS 库刷新源数据,其余库预热项目缓存(顺带补封面)"""
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)

    if not vlib:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    if vlib.resource_type == "rsshub":
        # 在后台任务中运行刷新,避免阻塞 API
        asyncio.create_task(rss_service.refresh_library(vlib))
        return {"message": "RSS library refresh process has been started."}

    asyncio.create_task(vlib_cache_service.refresh_vlib_cache(library_id))
    return {"message": "Vlib cache refresh process has been started."}


# --- 虚拟库 CRUD / 显示顺序 ---

@api_router.get("/all-libraries", tags=["Display Management"])
async def get_all_libraries():
    return await library_service.get_all_libraries()


@api_router.post("/display-order", status_code=204, tags=["Display Management"])
async def save_display_order(ordered_ids: List[str]):
    library_service.save_display_order(ordered_ids)
    return Response(status_code=204)


def _proxy_core_url():
    return os.getenv("PROXY_CORE_URL")


def _fire_vlib_rebuild(library_id: str) -> None:
    """保存虚拟库后,通知代理核心「失效旧缓存 + 按新规则重建 + 刷新封面」。

    关键:代理核心的直出缓存若不失效,改完库规则后最长 6 小时仍返回旧内容,
    封面也会按旧内容指纹判定"未变化"而跳过重绘。
    """
    proxy_core_url = _proxy_core_url()
    if not proxy_core_url:
        logger.info(f"未配置 PROXY_CORE_URL,跳过库 {library_id} 的缓存重建。")
        return

    async def _task():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_core_url.rstrip('/')}/api/internal/rebuild-vlib/{library_id}",
                    timeout=aiohttp.ClientTimeout(total=600),
                ) as resp:
                    logger.info(f"虚拟库缓存重建完成 ({library_id}): Status {resp.status}")
        except Exception as e:
            logger.warning(f"重建虚拟库缓存失败 ({library_id}): {e}")

    asyncio.create_task(_task())


def _fire_vlib_invalidate(library_id: str) -> None:
    """删库后通知代理核心释放内存缓存(admin 侧已清该库的 SQLite 行)。"""
    proxy_core_url = _proxy_core_url()
    if not proxy_core_url:
        return

    async def _task():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_core_url.rstrip('/')}/api/internal/invalidate-vlib/{library_id}",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    logger.info(f"虚拟库缓存已失效 ({library_id}): Status {resp.status}")
        except Exception as e:
            logger.warning(f"失效虚拟库缓存失败 ({library_id}): {e}")

    asyncio.create_task(_task())


@api_router.post("/libraries", response_model=VirtualLibrary, tags=["Libraries"])
async def create_library(library: VirtualLibrary):
    saved = library_service.create_library(library)
    # 新建库没有封面:重建会顺带解析数据源并触发生成
    if not saved.image_tag:
        _fire_vlib_rebuild(saved.id)
    return saved


@api_router.put("/libraries/{library_id}", response_model=VirtualLibrary, tags=["Libraries"])
async def update_library(library_id: str, updated_library_data: VirtualLibrary):
    saved = library_service.update_library(library_id, updated_library_data)
    # 规则变化会改变库内容:先失效旧缓存再按新规则重建(封面按新指纹判断是否重绘)
    _fire_vlib_rebuild(saved.id)
    return saved


@api_router.delete("/libraries/{library_id}", status_code=204, tags=["Libraries"])
async def delete_library(library_id: str):
    library_service.delete_library(library_id)
    # 释放代理核心内存里的该库缓存(数据源与叠加层由其他库共享,不动)
    _fire_vlib_invalidate(library_id)
    return Response(status_code=204)


# --- Emby 辅助查询(转发到 services/emby_client) ---

@api_router.get("/emby/classifications", tags=["Emby Helper"])
async def get_emby_classifications():
    def format_items(items_list: List) -> List:
        return [{"name": item.get("Name", 'N/A'), "id": item.get("Id", 'N/A')} for item in items_list]

    tasks = {
        "collections": fetch_items("/Items", params={"IncludeItemTypes": "BoxSet", "Recursive": "true"}),
        "genres": fetch_items("/Genres"),
        "tags": fetch_items("/Tags"),
        "studios": fetch_items("/Studios"),
    }
    results_list = await asyncio.gather(*tasks.values())
    results_dict = dict(zip(tasks.keys(), results_list))
    results_dict["persons"] = []
    return {key: format_items(value) for key, value in results_dict.items()}


@api_router.get("/emby/persons/search", tags=["Emby Helper"])
async def search_emby_persons(query: str = Query(None), page: int = Query(1)):
    PAGE_SIZE = 100
    start_index = (page - 1) * PAGE_SIZE
    if query:
        params = {"SearchTerm": query, "IncludeItemTypes": "Person", "Recursive": "true",
                  "StartIndex": start_index, "Limit": PAGE_SIZE}
        persons = await fetch_items("/Items", params=params)
    else:
        params = {"StartIndex": start_index, "Limit": PAGE_SIZE}
        persons = await fetch_items("/Persons", params=params)
    return [{"name": item.get("Name", 'N/A'), "id": item.get("Id", 'N/A')} for item in persons]


@api_router.get("/emby/resolve-item/{item_id}", tags=["Emby Helper"])
async def resolve_emby_item(item_id: str):
    users = await fetch_items("/Users")
    if not users:
        raise HTTPException(status_code=500, detail="无法获取任何Emby用户用于查询")

    ref_user_id = users[0]['Id']
    item_details = await fetch_items(f"/Users/{ref_user_id}/Items/{item_id}")

    if not item_details:
        raise HTTPException(status_code=404, detail="在Emby中未找到指定的项目ID")

    return {"name": item_details.get("Name", "N/A"), "id": item_details.get("Id")}


# --- 系统管理 ---

@api_router.post("/proxy/restart", status_code=204, tags=["System Management"])
async def restart_proxy_container():
    """通过 Docker API 重启代理服务器容器,自动清除其内存中的所有缓存。"""
    return await system_service.restart_proxy_container()


# --- 运行日志(实时流 WebSocket + 历史分页;必须注册在 "/" 静态挂载之前) ---

@api_router.get("/system/logs/history", tags=["System Management"])
async def get_log_history(
    start_index: Optional[int] = Query(default=None, ge=0, description="结束游标(不含):返回结束于该索引的最多 limit 行;首屏用 tail,翻页传上一次的 next_start_index"),
    tail: Optional[int] = Query(default=None, ge=1, description="首屏模式:返回最新 N 行"),
    limit: int = Query(default=100, ge=1, le=500, description="单次返回行数上限"),
):
    """历史日志分页(合并 proxy+admin 按天文件,时间升序);实时增量走 /ws/system/logs。"""
    if (start_index is None) == (tail is None):
        raise HTTPException(status_code=400, detail="start_index 与 tail 必须二选一")
    return log_service.read_history(start_index=start_index, tail=tail, limit=limit)


@admin_app.websocket("/ws/system/logs")
async def websocket_logs(websocket: WebSocket):
    """实时日志推送:连接后先回填内存历史,再持续转发广播。

    浏览器 WS 握手无法携带自定义 header,鉴权走 ?token=;
    管理密码未启用时直接放行;鉴权失败以 4401 关闭,前端据此不重试。
    ?backfill=0 跳过回填:前端自行用 /system/logs/history 拉首屏时使用,
    避免 REST 首屏与 WS 回填两份历史重叠。
    """
    await websocket.accept()
    config = config_manager.load_config()
    if auth_service.is_enabled(config) and not auth_service.verify(websocket.query_params.get("token") or ""):
        await websocket.close(code=4401)
        return

    skip_backfill = websocket.query_params.get("backfill") == "0"
    queue = LogBroadcaster.subscribe()

    async def _forward():
        try:
            if not skip_backfill:
                for message in LogBroadcaster.history_snapshot():
                    await websocket.send_text(message)
            while True:
                message = await queue.get()
                await websocket.send_text(message)
        except Exception:
            pass

    forward_task = asyncio.create_task(_forward())
    try:
        while True:
            await websocket.receive_text()  # 保活排空;客户端断开在此抛出
    except WebSocketDisconnect:
        pass
    finally:
        forward_task.cancel()
        LogBroadcaster.unsubscribe(queue)


admin_app.include_router(api_router)

# --- 静态资源:前端构建产物与生成的封面 ---

static_dir = app_paths.DOCKER_STATIC_DIR
if not static_dir.is_dir():
    if app_paths.FRONTEND_DIST_DIR.is_dir():
        static_dir = app_paths.FRONTEND_DIST_DIR
    else:
        logger.error("未找到前端构建产物(既无 /app/static 也无 frontend/dist),管理后台无法提供 Web UI。")
        sys.exit(1)
if not (static_dir / "index.html").is_file():
    sys.exit(1)

app_paths.COVERS_DIR.mkdir(parents=True, exist_ok=True)
admin_app.mount("/covers", StaticFiles(directory=str(app_paths.COVERS_DIR)), name="covers")
# 挂载 "/" 必须放在所有 API 路由与 /covers 之后,否则会拦截全部请求
admin_app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


@admin_app.on_event("startup")
async def startup_event():
    # 记录主事件循环(工作线程日志回投用),并启动 proxy 日志文件尾随转发
    LogBroadcaster.set_loop(asyncio.get_running_loop())
    admin_app.state.log_tail_task = asyncio.create_task(log_service.tail_proxy_logs())
    rss_service.start_scheduler()
    rss_service.reschedule_refresh_job(config_manager.load_config())
    config = config_manager.load_config()
    vlib_cache_service.start_scheduler()
    vlib_cache_service.reschedule_refresh_job(config)
    vlib_cache_service.reschedule_tmdb_index_job(config)


@admin_app.on_event("shutdown")
async def shutdown_event():
    admin_app.state.log_tail_task.cancel()
    rss_service.shutdown_scheduler()
    vlib_cache_service.shutdown_scheduler()
