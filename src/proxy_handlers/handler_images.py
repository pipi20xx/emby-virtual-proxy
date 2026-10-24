# src/proxy_handlers/handler_images.py (最终修复版)

import logging
import re
from pathlib import Path
from fastapi import Request, Response
from fastapi.responses import FileResponse
import asyncio

import config_manager

logger = logging.getLogger(__name__)

# 【修复 1】修正正则表达式，使其同时匹配 UUID, "tmdb-" (横杠), 和 "tmdb_" (下划线)
IMAGE_PATH_REGEX = re.compile(r"/Items/([a-f0-9\-]{36}|tmdb[-_]\d+)/Images/(\w+)")

COVERS_DIR = Path("/app/config/images")
# 封面解析顺序:静态 jpg 优先,其次动图 gif/webp/apng
COVER_EXTENSIONS = (".jpg", ".gif", ".webp", ".png")
MEDIA_TYPE_BY_SUFFIX = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".png": "image/png",
}
PLACEHOLDER_GENERATING_PATH = Path("/app/src/assets/images_placeholder/generating.jpg")  # 虚拟库 (UUID)
PLACEHOLDER_RSSHUB_PATH = Path("/app/src/assets/images_placeholder/rsshubpost.jpg")      # RSSHub (tmdb-)
# 【重要】请确保这个路径下存在一个为缺失剧集准备的占位图文件
PLACEHOLDER_EPISODE_PATH = Path("/app/src/assets/images_placeholder/placeholder.jpg")      # 缺失剧集 (tmdb_)


def cover_file_exists(item_id: str) -> bool:
    """检查某虚拟库的封面文件是否已生成(兼容静态 jpg 与动图 gif/webp/apng)。"""
    return any((COVERS_DIR / f"{item_id}{ext}").is_file() for ext in COVER_EXTENSIONS)


async def handle_virtual_library_image(request: Request, full_path: str) -> Response | None:
    match = IMAGE_PATH_REGEX.search(f"/{full_path}")
    if not match:
        return None

    item_id = match.group(1)
    image_type = match.group(2)

    if image_type != "Primary":
        return None

    # 检查实际的封面文件是否存在(兼容动图格式),如果存在则直接返回
    image_file = next(
        (
            COVERS_DIR / f"{item_id}{ext}"
            for ext in COVER_EXTENSIONS
            if (COVERS_DIR / f"{item_id}{ext}").is_file()
        ),
        None,
    )
    if image_file:
        return FileResponse(str(image_file), media_type=MEDIA_TYPE_BY_SUFFIX.get(image_file.suffix, "image/jpeg"))

    # --- 【修复 2】重构占位图返回逻辑 ---
    # 如果封面文件不存在，则根据 item_id 的格式返回对应的占位图
    # 这种方法比检查 URL 参数更可靠
    
    placeholder_to_serve = None
    
    if item_id.startswith("tmdb_"):  # 缺失剧集的 ID 以 "tmdb_" 开头
        logger.debug(f"IMAGE_HANDLER: Serving MISSING EPISODE placeholder for item '{item_id}'.")
        placeholder_to_serve = PLACEHOLDER_EPISODE_PATH
        
    elif item_id.startswith("tmdb-"):  # RSSHub 项目的 ID 以 "tmdb-" 开头
        logger.debug(f"IMAGE_HANDLER: Serving RSSHUB placeholder for item '{item_id}'.")
        placeholder_to_serve = PLACEHOLDER_RSSHUB_PATH
        
    else:  # 其他情况（标准的 UUID）被认为是常规虚拟库
        logger.debug(f"IMAGE_HANDLER: Serving 'GENERATING' placeholder for virtual library '{item_id}'.")
        placeholder_to_serve = PLACEHOLDER_GENERATING_PATH

    if placeholder_to_serve and placeholder_to_serve.is_file():
        return FileResponse(str(placeholder_to_serve), media_type="image/jpeg")
    
    # 如果连占位图文件本身都不存在，则返回 404
    logger.error(f"IMAGE_HANDLER: Placeholder file not found for item '{item_id}' at path: {placeholder_to_serve}")
    return Response(status_code=404)