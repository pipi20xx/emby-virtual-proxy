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
from fastapi import FastAPI, APIRouter, Depends, HTTPException, Request, Response, Query, File, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app_paths
import config_manager
from models import AppConfig, VirtualLibrary, AdvancedFilter
from services import auth_service, cover_service, library_service, rss_service, system_service, vlib_cache_service
from services.emby_client import fetch_items

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
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

    # 生成成功后把 image_tag / cover_format 写回对应虚拟库
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == body.library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="未找到要更新封面的虚拟库。")
    vlib.image_tag = image_tag
    vlib.cover_format = cover_format
    config_manager.save_config(config)
    return {
        "success": True,
        "image_tag": image_tag,
        "image_url": f"/covers/{body.library_id}.{cover_format}?t={image_tag}",
    }


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


def _fire_autogen_cover(library_id: str) -> None:
    """保存虚拟库后,通知代理核心后台补生成封面(尽力而为,不阻塞请求)。"""
    proxy_core_url = os.getenv("PROXY_CORE_URL")
    if not proxy_core_url:
        logger.info(f"未配置 PROXY_CORE_URL,跳过库 {library_id} 的封面自动生成触发。")
        return

    async def _task():
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{proxy_core_url.rstrip('/')}/api/internal/autogen-cover/{library_id}",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    logger.info(f"封面自动生成触发完成 ({library_id}): Status {resp.status}")
        except Exception as e:
            logger.warning(f"触发封面自动生成失败 ({library_id}): {e}")

    asyncio.create_task(_task())


@api_router.post("/libraries", response_model=VirtualLibrary, tags=["Libraries"])
async def create_library(library: VirtualLibrary):
    saved = library_service.create_library(library)
    # 新建库没有封面,主动触发一次后台生成(素材也由代理核心自行预热)
    if not saved.image_tag:
        _fire_autogen_cover(saved.id)
    return saved


@api_router.put("/libraries/{library_id}", response_model=VirtualLibrary, tags=["Libraries"])
async def update_library(library_id: str, updated_library_data: VirtualLibrary):
    saved = library_service.update_library(library_id, updated_library_data)
    # 仅对从未生成过封面的库触发,避免每次保存都重绘
    if not saved.image_tag:
        _fire_autogen_cover(saved.id)
    return saved


@api_router.delete("/libraries/{library_id}", status_code=204, tags=["Libraries"])
async def delete_library(library_id: str):
    library_service.delete_library(library_id)
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
    rss_service.start_scheduler()
    rss_service.reschedule_refresh_job(config_manager.load_config())
    config = config_manager.load_config()
    vlib_cache_service.start_scheduler()
    vlib_cache_service.reschedule_refresh_job(config)
    vlib_cache_service.reschedule_tmdb_index_job(config)


@admin_app.on_event("shutdown")
async def shutdown_event():
    rss_service.shutdown_scheduler()
    vlib_cache_service.shutdown_scheduler()
