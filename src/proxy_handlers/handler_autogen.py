# src/proxy_handlers/handler_autogen.py (自动刷新版)
"""虚拟库封面自动生成/刷新。

触发来源:启动预热(缺封面)、定时缓存刷新(admin 调度器)、库保存后补生成、
首页视图发现缺封面时的即时触发。本模块只负责"取条目 -> 判断是否需要重绘 ->
委托 cover_service 生成 -> 回写配置"这一条链,素材抓取与样式调度全部复用
services/cover_service.py,与 admin 手动生成共用同一套四级素材回退逻辑。

智能跳过:以条目 Id 集合的 md5 为内容指纹,指纹未变化且封面文件仍在时跳过,
定时刷新不会反复重绘;手动生成时锚定指纹,用户手工调校的封面在库内容不变期间
不会被自动任务覆盖。
"""

import hashlib
import logging

import aiohttp
from fastapi import HTTPException

import config_manager
from proxy_cache import vlib_items_cache
from services import cover_service
from .handler_images import cover_file_exists

logger = logging.getLogger(__name__)

GENERATION_IN_PROGRESS = set()

# 指纹与素材取材向内部代理索取的条目上限(仅缓存不可用时才走这条回退路径)
_INTERNAL_FETCH_LIMIT = 1000


async def load_cover_source_items(library_id: str, vlib, user_id: str, api_key: str) -> list:
    """取封面「内容指纹 + 素材」所需的条目。

    项目缓存开启时优先读本地缓存(零 Emby 开销、与浏览直出看到的内容完全一致):
    共享元数据 → 封面素材缓存 → 都没有才回退内部自调用。
    **项目缓存关闭时必须忽略残留缓存**,否则会拿旧数据画封面(开关关掉后并不会
    自动删除 SQLite 里的历史行,这里必须显式绕过)。
    """
    # 延迟导入:handler_views → handler_autogen → handler_items 会形成循环导入
    from . import handler_items

    config = config_manager.load_config_readonly()
    if not getattr(config, "enable_vlib_cache", True):
        return await fetch_vlib_items_internal(vlib, user_id, api_key)

    meta = handler_items.get_shared_metadata(library_id)
    if meta and meta.get("items"):
        return meta["items"]

    cached = vlib_items_cache.get(library_id)
    if cached:
        return cached

    return await fetch_vlib_items_internal(vlib, user_id, api_key)


async def fetch_vlib_items_internal(vlib, user_id: str, api_key: str) -> list:
    """以模拟客户端身份自调用代理 Items 接口,获取虚拟库当前条目列表。"""
    internal_proxy_url = f"http://localhost:8999/emby/Users/{user_id}/Items"

    params = {
        "ParentId": vlib.id,
        "Limit": str(_INTERNAL_FETCH_LIMIT),
        "Fields": "ImageTags,ProviderIds",
        "Recursive": "true",
        "IncludeItemTypes": "Movie,Series,Video",
        "X-Emby-Token": api_key,
        "X-Emby-Client": "Proxy (AutoGen)",
        "X-Emby-Device-Name": "ProxyAutoGen",
        "X-Emby-Device-Id": "proxy-autogen-device-id",
        "X-Emby-Client-Version": "4.8.11.0",
    }
    internal_headers = {
        'Accept': 'application/json',
        'X-Emby-Authorization': f'Emby UserId="{user_id}", Client="Proxy (AutoGen)", Device="ProxyAutoGen", DeviceId="proxy-autogen-device-id", Version="4.8.11.0", Token="{api_key}"'
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(internal_proxy_url, params=params, headers=internal_headers,
                                   timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status == 200:
                    data = await response.json(content_type=None)
                    if isinstance(data, dict):
                        return data.get("Items", []) or []
                else:
                    logger.error(f"内部代理请求失败，状态码: {response.status}, 响应: {await response.text()}")
    except Exception as e:
        logger.error(f"连接内部代理时出错: {e}")
    return []


def compute_source_hash(items: list) -> str:
    """内容指纹:条目 Id 集合的 md5(与顺序无关),用于判断库内容是否变化。"""
    return hashlib.md5(",".join(sorted(str(i.get("Id")) for i in items)).encode()).hexdigest()


async def generate_poster_in_background(library_id: str, user_id: str, api_key: str):
    """后台自动生成/刷新封面(带运行中去重与内容指纹跳过)。"""
    if library_id in GENERATION_IN_PROGRESS:
        return

    GENERATION_IN_PROGRESS.add(library_id)
    logger.info(f"✅ 已启动库 {library_id} (用户: {user_id}) 的封面自动生成后台任务。")

    try:
        config = config_manager.load_config_readonly()
        vlib = next((v for v in config.virtual_libraries if v.id == library_id), None)
        if not vlib:
            logger.error(f"后台任务：配置中未找到 vlib {library_id}。")
            return

        items = await load_cover_source_items(library_id, vlib, user_id, api_key)
        if not items:
            logger.warning(f"后台任务：根据虚拟库 '{vlib.name}' 的规则，未从代理获取到任何项目。")
            return

        source_hash = compute_source_hash(items)
        if vlib.cover_source_hash == source_hash and cover_file_exists(library_id):
            logger.info(f"后台任务：'{vlib.name}' 内容未变化且封面已存在，跳过本次生成。")
            return

        # 生成配方:手动生成时固化的外观优先,从未生成过则用全局默认样式 + 库名
        style_name = vlib.cover_style or config.default_cover_style
        title_zh = vlib.cover_title_zh or vlib.name
        title_en = vlib.cover_title_en if vlib.cover_title_en is not None else ""
        logger.info(f"后台任务：使用样式 '{style_name}' 为 '{vlib.name}' 生成封面...")

        try:
            image_tag, cover_format = await cover_service.generate_cover(
                library_id=library_id,
                title_zh=title_zh,
                title_en=title_en,
                style_name=style_name,
                animation_format=vlib.cover_animation_format,
            )
        except HTTPException as e:
            logger.error(f"后台任务：为库 {library_id} 准备素材/生成封面失败: {e.detail}")
            return

        if not image_tag:
            logger.error(f"后台任务：为库 {library_id} 生成封面失败，详见上方日志。")
            return

        current_config = config_manager.load_config()
        for vlib_in_config in current_config.virtual_libraries:
            if vlib_in_config.id == library_id:
                vlib_in_config.image_tag = image_tag
                vlib_in_config.cover_format = cover_format
                vlib_in_config.cover_source_hash = source_hash
                config_manager.save_config(current_config)
                logger.info(f"🎉 封面自动生成成功！ImageTag={image_tag}，内容指纹 {source_hash[:8]}… 已锚定。")
                break

    except Exception as e:
        logger.error(f"封面自动生成后台任务发生未知错误: {e}", exc_info=True)
    finally:
        GENERATION_IN_PROGRESS.remove(library_id)
        logger.info(f"后台任务结束，已释放库 {library_id} 的生成锁。")
