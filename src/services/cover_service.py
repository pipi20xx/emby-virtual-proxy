# src/services/cover_service.py —— 封面生成服务
"""封面生成的完整业务逻辑,与 admin 的 HTTP 路由解耦。

素材来源四级回退(优先级从高到低):
  1. 前端临时上传的图片        (temp_image_paths)
  2. 虚拟库级自定义图片目录    (vlib.cover_custom_image_path)
  3. 全局自定义图片目录        (config.custom_image_path)
  4. 从代理核心缓存的虚拟库项目封面中随机抽取 (需 PROXY_CORE_URL 环境变量)

样式插件契约(cover_generator/ 下的每个 style_*.py):
  - 必须提供入口函数 create_<模块名>(title, font_path, ...) -> base64 字符串
  - 可选声明 MATERIAL_MODE = "multi" | "single":
      "multi"  接收 library_dir=<素材目录>,自行从目录中取多图拼接
      "single" 接收 image_path=<单张主图路径>
    未声明时按本模块的内置注册表兼容三个内置样式。
  新增样式 = 在 cover_generator/ 新增一个文件,无需改动任何现有代码。
"""

import asyncio
import base64
import hashlib
import importlib
import logging
import os
import random
import re
import shutil
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
from fastapi import HTTPException, UploadFile
from PIL import Image

import app_paths
import config_manager
from models import AppConfig

logger = logging.getLogger(__name__)

MAX_MATERIALS = 9
SUPPORTED_IMAGE_FORMATS = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")

# 内置样式的素材模式注册表:兼容未声明 MATERIAL_MODE 的旧样式模块
_BUILTIN_STYLE_MATERIAL_MODES = {
    "style_multi_1": "multi",
    "style_single_1": "single",
    "style_single_2": "single",
}
MATERIAL_PARAM_BY_MODE = {"multi": "library_dir", "single": "image_path"}


# --- 临时素材上传 ---

def save_temp_image(file: UploadFile) -> Path:
    """保存前端上传的临时素材图片,返回其在容器内的路径。"""
    app_paths.TEMP_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    filename = re.sub(r'[^a-zA-Z0-9._-]', '', file.filename or "")
    unique_filename = f"{uuid.uuid4()}_{filename}"
    file_path = app_paths.TEMP_IMAGE_DIR / unique_filename
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        return file_path
    except Exception as e:
        logger.error(f"上传临时图片失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="文件上传处理失败。")


# --- 素材抓取(四级回退) ---

async def _download_materials(selected_items: List[dict], temp_dir: Path, config: AppConfig, headers: Dict) -> None:
    """并发下载项目主封面到临时目录,全部失败则抛错。"""

    async def download_image(session: aiohttp.ClientSession, item: dict, index: int) -> bool:
        image_url = f"{config.emby_url.rstrip('/')}/emby/Items/{item['Id']}/Images/Primary"
        try:
            async with session.get(image_url, headers=headers, timeout=20) as response:
                if response.status == 200:
                    content = await response.read()
                    with open(temp_dir / f"{index}.jpg", "wb") as f:
                        f.write(content)
                    return True
        except Exception:
            return False
        return False

    async with aiohttp.ClientSession() as session:
        tasks = [download_image(session, item, i + 1) for i, item in enumerate(selected_items)]
        results = await asyncio.gather(*tasks)

    if not any(results):
        raise HTTPException(status_code=500, detail="所有封面素材下载失败，无法生成海报。")


async def _fetch_images_from_vlib(library_id: str, temp_dir: Path, config: AppConfig) -> None:
    """来源4:从 proxy-core 的内部缓存接口获取项目列表并下载封面素材。"""
    logger.info(f"开始从内部缓存为虚拟库 {library_id} 获取封面素材...")

    proxy_core_url = os.getenv("PROXY_CORE_URL")
    if not proxy_core_url:
        raise HTTPException(status_code=500, detail="环境变量 PROXY_CORE_URL 未设置。")

    target_url = f"{proxy_core_url.rstrip('/')}/api/internal/get-cached-items/{library_id}"
    logger.info(f"正在向内部代理服务 {target_url} 请求缓存数据...")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(target_url, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    items = data.get("Items", [])
                else:
                    error_detail = (await response.json()).get("detail")
                    logger.error(f"从内部代理获取缓存失败 (Status: {response.status}): {error_detail}")
                    raise HTTPException(status_code=response.status, detail=error_detail)
    except aiohttp.ClientError as e:
        logger.error(f"连接到内部代理服务时出错: {e}")
        raise HTTPException(status_code=502, detail=f"无法连接到内部代理服务: {e}")

    items_with_images = [item for item in items if item.get("ImageTags", {}).get("Primary")]
    if not items_with_images:
        raise HTTPException(status_code=404, detail="缓存数据中不包含任何带有主封面的项目。")

    selected_items = random.sample(items_with_images, min(MAX_MATERIALS, len(items_with_images)))
    await _download_materials(selected_items, temp_dir, config, headers={'X-Emby-Token': config.emby_api_key})


async def _fetch_images_from_custom_path(custom_path: str, temp_dir: Path) -> None:
    """来源2/3:从自定义目录随机复制图片到临时目录。"""
    logger.info(f"开始从自定义目录 {custom_path} 获取封面素材...")

    source_dir = Path(custom_path)
    if not source_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"自定义图片目录不存在: {custom_path}")

    image_files = [f for f in source_dir.iterdir() if f.is_file() and f.suffix.lower() in SUPPORTED_IMAGE_FORMATS]
    if not image_files:
        raise HTTPException(status_code=404, detail=f"自定义图片目录中未找到支持的图片文件: {custom_path}")

    selected_files = random.sample(image_files, min(MAX_MATERIALS, len(image_files)))
    for i, file_path in enumerate(selected_files):
        try:
            shutil.copy(file_path, temp_dir / f"{i + 1}{file_path.suffix}")
        except Exception as e:
            logger.error(f"复制文件 {file_path} 时出错: {e}")
            continue

    if not any(temp_dir.iterdir()):
        raise HTTPException(status_code=500, detail="从自定义目录复制图片素材失败。")


async def _prepare_materials(library_id: str, image_gen_dir: Path, config: AppConfig,
                             temp_image_paths: Optional[List[str]]) -> None:
    """按四级回退顺序准备素材(见模块 docstring)。"""
    if temp_image_paths:
        logger.info(f"从 {len(temp_image_paths)} 张上传的临时图片中随机选择素材...")
        selected_paths = random.sample(temp_image_paths, min(MAX_MATERIALS, len(temp_image_paths)))
        for i, src_path_str in enumerate(selected_paths):
            src_path = Path(src_path_str)
            if src_path.is_file():
                shutil.copy(src_path, image_gen_dir / f"{i + 1}{src_path.suffix}")
        return

    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
    if vlib and vlib.cover_custom_image_path:
        await _fetch_images_from_custom_path(vlib.cover_custom_image_path, image_gen_dir)
    elif config.custom_image_path:
        await _fetch_images_from_custom_path(config.custom_image_path, image_gen_dir)
    else:
        await _fetch_images_from_vlib(library_id, image_gen_dir, config)


# --- 样式插件调度 ---

def _load_style_function(style_name: str):
    try:
        style_module = importlib.import_module(f"cover_generator.{style_name}")
        create_function = getattr(style_module, f"create_{style_name}")
    except (ImportError, AttributeError) as e:
        logger.error(f"无法加载或找到样式生成函数: {style_name} -> {e}")
        raise HTTPException(status_code=400, detail=f"无效的样式名称: {style_name}")
    return style_module, create_function


def _resolve_material_mode(style_name: str, style_module) -> str:
    """素材传递方式:优先读样式模块声明的 MATERIAL_MODE,未声明则查内置注册表。"""
    mode = getattr(style_module, "MATERIAL_MODE", None)
    if mode in MATERIAL_PARAM_BY_MODE:
        return mode
    builtin = _BUILTIN_STYLE_MATERIAL_MODES.get(style_name)
    if builtin:
        return builtin
    raise HTTPException(status_code=400, detail=f"未知的样式名称: {style_name}")


def _resolve_font_paths(library_id: str, config: AppConfig) -> Tuple[str, str]:
    """字体选择:虚拟库级 -> 全局 -> 内置默认。"""
    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)

    if vlib and vlib.cover_custom_zh_font_path:
        zh_font_path = vlib.cover_custom_zh_font_path
    elif config.custom_zh_font_path:
        zh_font_path = config.custom_zh_font_path
    else:
        zh_font_path = str(app_paths.FONTS_DIR / "multi_1_zh.ttf")

    if vlib and vlib.cover_custom_en_font_path:
        en_font_path = vlib.cover_custom_en_font_path
    elif config.custom_en_font_path:
        en_font_path = config.custom_en_font_path
    else:
        en_font_path = str(app_paths.FONTS_DIR / "multi_1_en.otf")

    return zh_font_path, en_font_path


# --- 主入口 ---

async def generate_cover(library_id: str, title_zh: str, title_en: Optional[str],
                         style_name: str, temp_image_paths: Optional[List[str]] = None) -> Optional[str]:
    """生成封面并落盘,返回新的 image_tag。

    HTTPException 原样上抛(携带具体错误);其余异常记录日志后返回 None,
    由调用方决定对外的错误响应。
    """
    config = config_manager.load_config()
    output_dir = app_paths.COVERS_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    image_gen_dir = output_dir / f"temp_{uuid.uuid4()}"
    image_gen_dir.mkdir()

    try:
        # 1. 素材准备(四级回退)
        await _prepare_materials(library_id, image_gen_dir, config, temp_image_paths)

        # 2. 加载样式插件并组装参数
        logger.info(f"素材准备完毕，开始使用样式 '{style_name}' 为 '{title_zh}' ({library_id}) 生成封面...")
        style_module, create_function = _load_style_function(style_name)
        material_mode = _resolve_material_mode(style_name, style_module)

        kwargs = {
            "title": (title_zh, title_en),
            "font_path": _resolve_font_paths(library_id, config),
        }
        if material_mode == "single":
            main_image_path = image_gen_dir / "1.jpg"
            if not main_image_path.is_file():
                raise HTTPException(status_code=404, detail="无法找到用于单图模式的主素材图片 (1.jpg)。")
            kwargs[MATERIAL_PARAM_BY_MODE[material_mode]] = str(main_image_path)
        else:
            kwargs[MATERIAL_PARAM_BY_MODE[material_mode]] = str(image_gen_dir)

        res = create_function(**kwargs)
        if not res:
            logger.error(f"样式函数 {style_name} 返回失败。")
            raise HTTPException(status_code=500, detail=f"封面生成函数 {style_name} 内部错误。")

        # 3. 解码、转换并以虚拟库 ID 为名保存
        image_data = base64.b64decode(res)
        img = Image.open(BytesIO(image_data))
        if img.mode != 'RGB':
            img = img.convert('RGB')

        output_path = output_dir / f"{library_id}.jpg"
        img.save(output_path, "JPEG", quality=90)

        image_tag = hashlib.md5(str(time.time()).encode()).hexdigest()
        logger.info(f"封面成功保存至: {output_path}, ImageTag: {image_tag}")
        return image_tag

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"封面生成过程中发生未知错误: {e}", exc_info=True)
        return None
    finally:
        if image_gen_dir.exists():
            shutil.rmtree(image_gen_dir)
            logger.info(f"已清理临时素材目录: {image_gen_dir}")


def clear_cover_files() -> None:
    """删除封面目录下的所有文件/子目录。

    只负责文件系统;配置中各虚拟库 image_tag 的重置由调用方处理。
    """
    covers_dir = app_paths.COVERS_DIR
    logger.info(f"开始清空封面目录: {covers_dir}")
    if covers_dir.is_dir():
        for item in covers_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
