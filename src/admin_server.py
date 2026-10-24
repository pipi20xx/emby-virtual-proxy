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
import sys
from typing import List, Optional

from fastapi import FastAPI, APIRouter, HTTPException, Response, Query, File, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import app_paths
import config_manager
from models import AppConfig, VirtualLibrary, AdvancedFilter
from services import cover_service, library_service, rss_service, system_service
from services.emby_client import fetch_items

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

admin_app = FastAPI(title="Emby Virtual Proxy - Admin API")
api_router = APIRouter(prefix="/api")


class CoverRequest(BaseModel):
    library_id: str
    title_zh: str
    title_en: Optional[str] = None
    style_name: str
    temp_image_paths: Optional[List[str]] = None


# --- 配置 ---

@api_router.get("/config", response_model=AppConfig, response_model_by_alias=True, tags=["Configuration"])
async def get_config():
    return config_manager.load_config()


@api_router.post("/config", response_model=AppConfig, response_model_by_alias=True, tags=["Configuration"])
async def update_config(config: AppConfig):
    config_manager.save_config(config)
    # 配置变化可能影响 RSS 定时刷新间隔,重建定时任务
    rss_service.reschedule_refresh_job(config)
    return config


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
        image_tag = await cover_service.generate_cover(
            library_id=body.library_id,
            title_zh=body.title_zh,
            title_en=body.title_en,
            style_name=body.style_name,
            temp_image_paths=body.temp_image_paths,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[COVER-GEN-ERROR] 封面生成过程中发生异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

    if not image_tag:
        raise HTTPException(status_code=500, detail="封面生成失败，详见后端日志。")

    # 生成成功后把 image_tag 写回对应虚拟库
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == body.library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="未找到要更新封面的虚拟库。")
    vlib.image_tag = image_tag
    config_manager.save_config(config)
    return {"success": True, "image_tag": image_tag}


@api_router.post("/covers/clear", status_code=204, tags=["Cover Generator"])
async def clear_all_covers():
    """清空所有生成的封面图并重置配置中的 image_tag"""
    try:
        cover_service.clear_cover_files()
        config = config_manager.load_config()
        for vlib in config.virtual_libraries:
            vlib.image_tag = None
        config_manager.save_config(config)
        logger.info("所有封面及配置已成功清除。")
        return Response(status_code=204)
    except Exception as e:
        logger.error(f"清空封面时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"清空封面时发生内部错误: {e}")


# --- RSS 虚拟库刷新 ---

@api_router.post("/libraries/{library_id}/refresh", status_code=202, tags=["Libraries"])
async def refresh_rss_library(library_id: str):
    """手动触发 RSS 虚拟库的刷新"""
    config = config_manager.load_config()
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)

    if not vlib:
        raise HTTPException(status_code=404, detail="Virtual library not found")
    if vlib.resource_type != "rsshub":
        raise HTTPException(status_code=400, detail="This library is not an RSSHUB library")

    # 在后台任务中运行刷新,避免阻塞 API
    asyncio.create_task(rss_service.refresh_library(vlib))

    return {"message": "RSS library refresh process has been started."}


# --- 虚拟库 CRUD / 显示顺序 ---

@api_router.get("/all-libraries", tags=["Display Management"])
async def get_all_libraries():
    return await library_service.get_all_libraries()


@api_router.post("/display-order", status_code=204, tags=["Display Management"])
async def save_display_order(ordered_ids: List[str]):
    library_service.save_display_order(ordered_ids)
    return Response(status_code=204)


@api_router.post("/libraries", response_model=VirtualLibrary, tags=["Libraries"])
async def create_library(library: VirtualLibrary):
    return library_service.create_library(library)


@api_router.put("/libraries/{library_id}", response_model=VirtualLibrary, tags=["Libraries"])
async def update_library(library_id: str, updated_library_data: VirtualLibrary):
    return library_service.update_library(library_id, updated_library_data)


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


@admin_app.on_event("shutdown")
async def shutdown_event():
    rss_service.shutdown_scheduler()
