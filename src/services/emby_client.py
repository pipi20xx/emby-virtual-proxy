# src/services/emby_client.py —— Emby API 统一访问(管理侧)
"""管理后台所有对 Emby 服务器的查询都经过本模块,统一处理:
URL 拼接、认证头、超时、错误到 HTTPException 的转换。

注意:proxy_handlers 中面向客户端的播放/转发请求属于代理核心,
有各自独立的转发实现,刻意不在本模块管辖范围内,请勿合并。
"""

import logging
from typing import Dict, List, Optional

import aiohttp
from fastapi import HTTPException

import config_manager
from emby_net import emby_proxy_from_config
from models import AppConfig
from services import credential_service

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15


def _get_validated_config(config: Optional[AppConfig] = None) -> AppConfig:
    cfg = config or config_manager.load_config()
    if not cfg.emby_url:
        raise HTTPException(status_code=400, detail="请在系统设置中配置Emby服务器地址。")
    if credential_service.auth_mode(cfg) == "none":
        raise HTTPException(status_code=400, detail="请在系统设置中配置Emby凭证:API 密钥,或账号 + 密码。")
    return cfg


async def fetch_items(endpoint: str, params: Dict = None, config: Optional[AppConfig] = None) -> List:
    """GET {emby_url}/emby{endpoint}。

    响应为 {"Items": [...]} 时返回 Items 列表;
    响应本身是数组(如 /Users)或单个对象(如条目详情)时原样返回。
    凭证由 credential_service 解析(API 密钥优先,账号密码登录兜底,401 自愈重登)。
    """
    cfg = _get_validated_config(config)
    url = f"{cfg.emby_url.rstrip('/')}/emby{endpoint}"

    try:
        async with aiohttp.ClientSession() as session:
            status, data = await credential_service.fetch_json_with_auth(
                session, url, config=cfg, params=params,
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS),
                proxy=emby_proxy_from_config(cfg))
            if status != 200:
                body = data if isinstance(data, str) else str(data)
                msg = f"从Emby获取数据失败 (Endpoint: {endpoint}, Status: {status}): {body}"
                logger.error(msg)
                raise HTTPException(status_code=status, detail=msg)
    except credential_service.CredentialError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except aiohttp.ClientError as e:
        msg = f"连接到Emby时发生网络错误 (Endpoint: {endpoint}): {e}"
        logger.error(msg)
        raise HTTPException(status_code=502, detail=msg)

    if isinstance(data, dict):
        return data.get("Items", data)
    if isinstance(data, list):
        return data
    return []


async def get_real_libraries() -> List[dict]:
    """获取真实媒体库列表:/Library/MediaFolders 与首个用户的 /Views 取并集。

    Emby 的部分三方库只出现在其中之一,因此两个来源都要查。
    """
    all_real_libs = {}
    try:
        media_folders = await fetch_items("/Library/MediaFolders")
        for lib in media_folders:
            lib_id = lib.get("Id")
            if lib_id:
                all_real_libs[lib_id] = {
                    "Id": lib_id, "Name": lib.get("Name"), "CollectionType": lib.get("CollectionType")
                }
    except HTTPException as e:
        logger.warning(f"从 /Library/MediaFolders 获取数据失败: {e.detail}")

    try:
        user_items = await fetch_items("/Users")
        if user_items:
            ref_user_id = user_items[0].get("Id")
            if ref_user_id:
                views = await fetch_items(f"/Users/{ref_user_id}/Views")
                for lib in views:
                    lib_id = lib.get("Id")
                    if lib_id and lib_id not in all_real_libs:
                        all_real_libs[lib_id] = {
                            "Id": lib_id, "Name": lib.get("Name"), "CollectionType": lib.get("CollectionType")
                        }
    except HTTPException as e:
        logger.warning(f"从 /Users/.../Views 获取数据失败: {e.detail}")

    return list(all_real_libs.values())
