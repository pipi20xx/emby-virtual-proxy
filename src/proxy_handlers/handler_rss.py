from db_manager import DBManager
from emby_net import emby_proxy_from_config
from pathlib import Path
import requests
import json
import config_manager

DB_DIR = Path(__file__).parent.parent.parent / "config"
RSS_LIBRARY_DB = DB_DIR / "rss_library_items.db"
TMDB_CACHE_DB = DB_DIR / "tmdb_cache.db"

class RssHandler:
    def __init__(self):
        self.rss_library_db = DBManager(RSS_LIBRARY_DB)
        self.tmdb_cache_db = DBManager(TMDB_CACHE_DB)
        self.config = config_manager.load_config()

    async def handle(self, request_path: str, vlib_id: str, request_params, user_id: str, session, real_emby_url: str, request_headers):
        all_items_from_db = self.rss_library_db.fetchall(
            "SELECT tmdb_id, media_type, emby_item_id FROM rss_library_items WHERE library_id = ?",
            (vlib_id,)
        )
        if not all_items_from_db:
            return {"Items": [], "TotalRecordCount": 0}

        existing_emby_ids = [str(item['emby_item_id']) for item in all_items_from_db if item['emby_item_id']]
        missing_items_info = [{'tmdb_id': item['tmdb_id'], 'media_type': item['media_type']} for item in all_items_from_db if not item['emby_item_id']]

        # 1. 获取已存在项目的数据，并从中提取真实的 ServerId
        existing_items_data = []
        server_id = None
        if existing_emby_ids:
            # 关键：传入 request_params 以继承 Fields
            existing_items_data = await self._get_emby_items_by_ids_async(existing_emby_ids, request_params, user_id, session, real_emby_url, request_headers)
            if existing_items_data:
                server_id = existing_items_data[0].get("ServerId")

        # 2. 如果没有已存在的项目，则从配置中获取备用 ServerId
        if not server_id:
            server_id = self.config.emby_server_id or "emby"

        # 3. 使用真实的 ServerId 创建不存在项目的占位符
        missing_items_placeholders = []
        for item_info in missing_items_info:
            # 注意：_get_item_from_tmdb 是同步的，但内部的数据库和网络请求是阻塞的
            # 在异步函数中直接调用它在 aiohttp 环境下是安全的
            item = self._get_item_from_tmdb(item_info['tmdb_id'], item_info['media_type'], server_id)
            if item:
                missing_items_placeholders.append(item)

        final_items = existing_items_data + missing_items_placeholders
        return {"Items": final_items, "TotalRecordCount": len(final_items)}

    async def _get_emby_items_by_ids_async(self, item_ids: list, request_params, user_id: str, session, real_emby_url: str, request_headers):
        if not item_ids: return []
        
        ids_str = ",".join(item_ids)
        url = f"{real_emby_url}/emby/Users/{user_id}/Items"
        
        headers = {k: v for k, v in request_headers.items() if k.lower() in ['accept', 'accept-language', 'user-agent', 'x-emby-authorization', 'x-emby-client', 'x-emby-device-name', 'x-emby-device-id', 'x-emby-client-version', 'x-emby-language', 'x-emby-token']}
        
        params = {"Ids": ids_str}
        if request_params and "Fields" in request_params:
            params["Fields"] = request_params.get("Fields")
        if request_params and "X-Emby-Token" in request_params:
            headers["X-Emby-Token"] = request_params.get("X-Emby-Token")

        try:
            async with session.get(url, params=params, headers=headers, proxy=emby_proxy_from_config(self.config)) as resp:
                if resp.status == 200:
                    return (await resp.json()).get("Items", [])
                return []
        except Exception as e:
            print(f"通过 ID 查询 Emby 项目失败 (async): {e}")
            return []

    def _get_item_from_tmdb(self, tmdb_id, media_type, server_id): # 新增 server_id 参数
        cached = self.tmdb_cache_db.fetchone("SELECT data FROM tmdb_cache WHERE tmdb_id = ? AND media_type = ?", (tmdb_id, media_type))
        if cached:
            # 关键：即使是从缓存加载，也要用最新的真实 ServerId 覆盖
            cached_data = json.loads(cached['data'])
            cached_data["ServerId"] = server_id
            return cached_data

        if not self.config.tmdb_api_key: return None

        item_type_path = 'movie' if media_type == 'movie' else 'tv'
        url = f"https://api.themoviedb.org/3/{item_type_path}/{tmdb_id}?api_key={self.config.tmdb_api_key}&language=zh-CN"
        proxies = {"http": self.config.tmdb_proxy, "https": self.config.tmdb_proxy} if self.config.tmdb_proxy else None

        try:
            response = requests.get(url, proxies=proxies, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            emby_item = self._format_tmdb_to_emby(data, media_type, tmdb_id, server_id) # 传递 server_id
            
            self.tmdb_cache_db.execute(
                "INSERT OR REPLACE INTO tmdb_cache (tmdb_id, media_type, data) VALUES (?, ?, ?)",
                (tmdb_id, media_type, json.dumps(emby_item, ensure_ascii=False)),
                commit=True
            )
            return emby_item
        except Exception as e:
            print(f"Error fetching from TMDB for {tmdb_id}: {e}")
            return None

    def _format_tmdb_to_emby(self, tmdb_data, media_type, tmdb_id, server_id): # 新增 server_id 参数
        is_movie = media_type == 'movie'
        item_type = 'Movie' if is_movie else 'Series'
        
        return {
            "Name": tmdb_data.get('title') if is_movie else tmdb_data.get('name'),
            "ProductionYear": int((tmdb_data.get('release_date') or '0').split('-')[0]) if is_movie else int((tmdb_data.get('first_air_date') or '0').split('-')[0]),
            "Id": f"tmdb-{tmdb_id}",
            "Type": item_type,
            "IsFolder": False,
            "MediaType": "Video" if is_movie else "Series",
            "ServerId": server_id, # 使用真实的 ServerId
            "ImageTags": {"Primary": "placeholder"},
            "HasPrimaryImage": True,
            "PrimaryImageAspectRatio": 0.6666666666666666,
            "ProviderIds": {"Tmdb": str(tmdb_id)},
            "UserData": {"Played": False, "PlayCount": 0, "IsFavorite": False, "PlaybackPositionTicks": 0},
            "Overview": tmdb_data.get("overview"),
            "PremiereDate": tmdb_data.get("release_date") if is_movie else tmdb_data.get("first_air_date"),
        }
