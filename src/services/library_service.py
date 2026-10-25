# src/services/library_service.py —— 虚拟库 CRUD 与显示顺序管理
"""虚拟库的增删改查、显示顺序保存,以及 RSS 库删除时的数据清理。

所有写操作最终都落在 config.json(config_manager),
RSS 库的条目/源缓存落在 SQLite(db_manager)。
"""

import logging
import uuid
from typing import List

from fastapi import HTTPException

import config_manager
from db_manager import DBManager, RSS_CACHE_DB, RSS_LIBRARY_ITEMS_DB, delete_vlib_items_cache, delete_vlib_metadata, delete_vlib_user_overlay
from models import VirtualLibrary
from services.emby_client import get_real_libraries

logger = logging.getLogger(__name__)


async def get_all_libraries() -> List[dict]:
    """真实库 + 虚拟库的合并列表,供前端排序与资源选择使用。"""
    all_libs = []
    real_libs_from_emby = await get_real_libraries()
    for lib in real_libs_from_emby:
        all_libs.append({
            "id": lib.get("Id"), "name": lib.get("Name"), "type": "real",
            "collectionType": lib.get("CollectionType")
        })

    config = config_manager.load_config()
    for lib in config.virtual_libraries:
        all_libs.append({
            "id": lib.id, "name": lib.name, "type": "virtual",
        })

    return all_libs


def save_display_order(ordered_ids: List[str]) -> None:
    config = config_manager.load_config()
    config.display_order = ordered_ids
    config_manager.save_config(config)


def create_library(library: VirtualLibrary) -> VirtualLibrary:
    config = config_manager.load_config()
    library.id = str(uuid.uuid4())
    config.virtual_libraries.append(library)
    if library.id not in config.display_order:
        config.display_order.append(library.id)
    config_manager.save_config(config)
    return library


def update_library(library_id: str, updated_library_data: VirtualLibrary) -> VirtualLibrary:
    config = config_manager.load_config()
    lib_to_update = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
    if not lib_to_update:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    # 不完全替换,只更新客户端实际发来的字段(exclude_unset)
    update_data = updated_library_data.model_dump(exclude_unset=True)

    # 客户端未携带/传空 image_tag 时保留旧值,避免更新设置导致封面信息丢失
    if lib_to_update.image_tag and not update_data.get('image_tag'):
        update_data['image_tag'] = lib_to_update.image_tag

    # 封面生成配方由生成动作维护;编辑表单不携带时保留,避免自动刷新外观回退到默认样式
    for cover_field in ("cover_format", "cover_style", "cover_title_zh", "cover_title_en",
                        "cover_animation_format", "cover_source_hash"):
        if not update_data.get(cover_field):
            old_value = getattr(lib_to_update, cover_field, None)
            if old_value is not None:
                update_data[cover_field] = old_value

    updated_lib = lib_to_update.model_copy(update=update_data)

    for i, lib in enumerate(config.virtual_libraries):
        if lib.id == library_id:
            config.virtual_libraries[i] = updated_lib
            break

    config_manager.save_config(config)
    return updated_lib


def delete_library(library_id: str) -> None:
    config = config_manager.load_config()
    lib_to_delete = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
    if not lib_to_delete:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    # RSS 库:删除前先清理其源缓存与条目数据
    if lib_to_delete.resource_type == "rsshub":
        _cleanup_rss_library_data(lib_to_delete)

    # 清理虚拟库持久化缓存(封面素材 + 共享元数据 + 用户叠加层)
    delete_vlib_items_cache(library_id)
    delete_vlib_metadata(library_id)
    delete_vlib_user_overlay(library_id)

    config.virtual_libraries = [lib for lib in config.virtual_libraries if lib.id != library_id]
    if library_id in config.display_order:
        config.display_order.remove(library_id)
    config_manager.save_config(config)


def _cleanup_rss_library_data(vlib: VirtualLibrary) -> None:
    logger.info(f"Deleting RSS library '{vlib.name}'. Performing cleanup...")

    # 1. 清理 RSS 源缓存
    if vlib.rsshub_url:
        try:
            DBManager(RSS_CACHE_DB).execute(
                "DELETE FROM rss_cache WHERE url = ?", (vlib.rsshub_url,), commit=True
            )
            logger.info(f"Removed RSS cache for URL: {vlib.rsshub_url}")
        except Exception as e:
            logger.error(f"Failed to clean RSS cache for {vlib.rsshub_url}: {e}")

    # 2. 清理 rss_library_items.db 中该库的所有条目
    try:
        if RSS_LIBRARY_ITEMS_DB.is_file():
            DBManager(RSS_LIBRARY_ITEMS_DB).execute(
                "DELETE FROM rss_library_items WHERE library_id = ?", (vlib.id,), commit=True
            )
            logger.info(f"Removed all items for library ID {vlib.id} from rss_library_items.db")
        else:
            logger.info(f"rss_library_items.db not found, skipping item cleanup for library ID {vlib.id}.")
    except Exception as e:
        logger.warning(f"Could not clean items for library ID {vlib.id} from rss_library_items.db: {e}")
