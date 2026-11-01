import re
import time
import requests
import json
import logging
import difflib
from pathlib import Path
from bs4 import BeautifulSoup
from db_manager import DBManager, RSS_CACHE_DB, TMDB_CACHE_DB
import config_manager
from services import credential_service

logger = logging.getLogger(__name__)

DB_DIR = Path(__file__).parent.parent.parent / "config"

class BaseRssProcessor:
    def __init__(self, vlib):
        self.vlib = vlib
        self.library_id = vlib.id
        self.rsshub_url = vlib.rsshub_url
        self.rss_db = DBManager(RSS_CACHE_DB)
        self.tmdb_cache_db = DBManager(TMDB_CACHE_DB)
        self.rss_library_db = DBManager(DB_DIR / "rss_library_items.db")
        self.config = config_manager.load_config()
        
        # 定义去后缀的正则列表 (用于通用 TMDB 搜索优化)
        self.truncation_patterns = [
            re.compile(r'\s*第[一二三四五六七八九十\d]+[季期].*$', re.IGNORECASE),
            re.compile(r'\s*Season\s*\d+.*$', re.IGNORECASE),
            re.compile(r'\s*Part\s*\d+.*$', re.IGNORECASE),
            re.compile(r'\s*Cour\s*\d+.*$', re.IGNORECASE),
            re.compile(r'\s*-\s*season\s*\d+.*$', re.IGNORECASE),
        ]

    def _fetch_rss_content(self):
        """获取并缓存 RSS 内容，缓存有效期为30分钟"""
        self.rss_db.execute(
            "CREATE TABLE IF NOT EXISTS rss_cache (url TEXT PRIMARY KEY, content TEXT, timestamp REAL)",
            commit=True
        )
        cache_duration = 30 * 60
        cached = self.rss_db.fetchone("SELECT content, timestamp FROM rss_cache WHERE url = ?", (self.rsshub_url,))
        if cached:
            try:
                is_valid = (time.time() - float(cached['timestamp'])) < cache_duration
                if is_valid:
                    logger.info(f"RSS源 {self.rsshub_url} 命中缓存。")
                    return cached['content']
            except (ValueError, TypeError):
                logger.warning(f"RSS源 {self.rsshub_url} 的缓存时间戳格式无效，将强制刷新。")
                pass

        logger.info(f"RSS源 {self.rsshub_url} 缓存未命中或已过期，正在获取新内容...")
        response = requests.get(self.rsshub_url)
        response.raise_for_status()
        content = response.text
        
        self.rss_db.execute(
            "INSERT OR REPLACE INTO rss_cache (url, content, timestamp) VALUES (?, ?, ?)",
            (self.rsshub_url, content, time.time()),
            commit=True
        )
        logger.info(f"RSS源 {self.rsshub_url} 的新内容已存入缓存。")
        return content

    def _parse_source_ids(self, xml_content):
        """从 RSS XML 中解析出源 ID。必须由子类实现。"""
        raise NotImplementedError

    def _get_tmdb_info(self, source_info):
        """根据源 ID 获取 TMDB 信息。必须由子类实现。"""
        raise NotImplementedError

    def _search_tmdb_by_name(self, title, year=None):
        """通用的 TMDB 搜索方法，作为兜底方案。支持处理'|'分隔的多个标题。"""
        logger.info(f"正在使用通用搜索为 '{title}' (年份: {year or 'N/A'}) 查找 TMDB ID...")
        if not self.config.tmdb_api_key:
            logger.error("TMDB API Key 未配置，无法执行通用搜索。")
            return []

        # 替换全角'｜'为半角'|'，然后拆分为多个标题进行尝试
        normalized_title = title.replace('｜', '|')
        raw_titles = [t.strip() for t in normalized_title.split('|') if t.strip()]
        
        # 构建搜索候选词列表：包含原始标题和去后缀后的标题
        search_queries = []
        for raw in raw_titles:
            search_queries.append(raw)
            # 尝试去后缀
            cleaned = raw
            for pattern in self.truncation_patterns:
                cleaned = pattern.sub('', cleaned)
            cleaned = cleaned.strip()
            if cleaned and cleaned != raw:
                search_queries.append(cleaned)
        
        # 去重并保持顺序
        search_queries = list(dict.fromkeys(search_queries))
        
        proxies = {"http": self.config.tmdb_proxy, "https": self.config.tmdb_proxy} if self.config.tmdb_proxy else None
        
        best_candidate = None
        best_score = 0
        THRESHOLD = 0.6 # 相似度阈值 (0-1)

        for current_title in search_queries:
            logger.info(f"正在尝试搜索标题: '{current_title}'...")
            # 注意：/search/multi 不支持 year 参数，我们必须在代码里过滤
            url = f"https://api.themoviedb.org/3/search/multi?api_key={self.config.tmdb_api_key}&query={requests.utils.quote(current_title)}&language=zh-CN"
            
            try:
                response = requests.get(url, proxies=proxies, timeout=10)
                response.raise_for_status()
                data = response.json()
                results = data.get("results", [])

                if not results:
                    continue

                for result in results:
                    media_type = result.get("media_type")
                    if media_type not in ["movie", "tv"]:
                        continue
                    
                    tmdb_id = result.get("id")
                    res_title = result.get("title") if media_type == "movie" else result.get("name")
                    res_orig_title = result.get("original_title") if media_type == "movie" else result.get("original_name")
                    
                    # 获取结果年份
                    res_date = result.get("release_date") if media_type == "movie" else result.get("first_air_date")
                    res_year = int(res_date.split('-')[0]) if res_date else 0

                    # --- 打分逻辑 ---
                    score = 0
                    
                    # 1. 文本相似度 (0.0 - 1.0)
                    sim_title = difflib.SequenceMatcher(None, current_title.lower(), (res_title or "").lower()).ratio()
                    sim_orig = difflib.SequenceMatcher(None, current_title.lower(), (res_orig_title or "").lower()).ratio()
                    text_score = max(sim_title, sim_orig)
                    
                    # 基础分
                    score += text_score * 10 

                    # 2. 年份匹配奖励
                    if year and res_year:
                        diff = abs(year - res_year)
                        if diff == 0:
                            score += 5 # 完美匹配
                        elif diff == 1:
                            score += 3 # 差一年也算不错
                        else:
                            score -= 2 # 年份差太多扣分
                    
                    # 3. 标题完全匹配奖励 (针对去后缀后的短标题)
                    if text_score == 1.0:
                        score += 2

                    logger.debug(f"  - 候选: {res_title} ({res_year}) [ID:{tmdb_id}] -得分: {score:.2f}")

                    if score > best_score:
                        best_score = score
                        best_candidate = (str(tmdb_id), media_type)

            except requests.RequestException as e:
                logger.error(f"通用 TMDB 搜索 API 请求失败 (标题: '{current_title}'): {e}")
                continue
        
        # 设定一个总分及格线，防止匹配到完全不相干的东西
        # 基础分如果完全匹配是10分，年份匹配是5分，满分约17分
        # 如果只有文字相似度高，也能有8-10分
        if best_candidate and best_score > 7:
            logger.info(f"通用搜索最佳匹配: {best_candidate} (得分: {best_score:.2f})")
            return [best_candidate]
        
        logger.warning(f"通用搜索未能找到得分为高 ({best_score:.2f}) 的结果。")
        return []

    def process(self):
        """处理整个流程，包含专用匹配和通用兜底匹配"""
        logger.info(f"开始处理 RSS 媒体库 {self.library_id} (URL: {self.rsshub_url})")
        xml_content = self._fetch_rss_content()
        
        # 【修改】根据 enable_retention 开关决定处理逻辑
        if not self.vlib.enable_retention:
            logger.info(f"数据保留功能未开启，正在为媒体库 {self.library_id} 清理旧的项目（全量刷新模式）...")
            self.rss_library_db.execute("DELETE FROM rss_library_items WHERE library_id = ?", (self.library_id,), commit=True)
        else:
            logger.info(f"数据保留功能已开启，将执行增量更新和过期清理。")

        source_items = self._parse_source_ids(xml_content)
        
        tmdb_ids_map = {}
        processed_count = 0
        total_items = len(source_items)
        current_time_str = time.strftime('%Y-%m-%d %H:%M:%S')

        for i, item_info in enumerate(source_items):
            source_id = item_info['id']
            title = item_info['title']
            year = item_info.get('year')
            
            logger.info(f"正在处理: {title} (ID: {source_id}) ({i+1}/{total_items})")
            
            # 1. 尝试专用方法匹配
            tmdb_results = self._get_tmdb_info(item_info)
            
            # 2. 如果专用方法失败，尝试通用兜底方法
            if not tmdb_results:
                logger.info(f"专用方法未能匹配 '{title}'，尝试通用搜索...")
                tmdb_results = self._search_tmdb_by_name(title, year)

            if not tmdb_results:
                logger.warning(f"所有方法都未能为 '{title}' (ID: {source_id}) 找到 TMDB 匹配，已跳过。")
                continue

            for tmdb_id, media_type in tmdb_results:
                if self.vlib.enable_retention:
                    # 【保留模式】使用 INSERT OR REPLACE 来更新时间戳，实现“续命”
                    existing = self.rss_library_db.fetchone(
                        "SELECT emby_item_id FROM rss_library_items WHERE library_id = ? AND tmdb_id = ?",
                        (self.library_id, tmdb_id)
                    )
                    emby_item_id = existing['emby_item_id'] if existing else None

                    self.rss_library_db.execute(
                        "INSERT OR REPLACE INTO rss_library_items (library_id, tmdb_id, media_type, emby_item_id, added_at) VALUES (?, ?, ?, ?, ?)",
                        (self.library_id, tmdb_id, media_type, emby_item_id, current_time_str),
                        commit=True
                    )
                    logger.info(f"成功映射 '{title}' -> TMDB ID {tmdb_id} ({media_type}) 并更新状态。")
                else:
                    # 【普通模式】直接插入，已存在则忽略（虽然因为前面已经清空了，基本都是新插入）
                    self.rss_library_db.execute(
                        "INSERT OR IGNORE INTO rss_library_items (library_id, tmdb_id, media_type, emby_item_id) VALUES (?, ?, ?, NULL)",
                        (self.library_id, tmdb_id, media_type),
                        commit=True
                    )
                    logger.info(f"成功映射 '{title}' -> TMDB ID {tmdb_id} ({media_type}) 并保存至媒体库 {self.library_id}。")

                if tmdb_id not in tmdb_ids_map:
                    tmdb_ids_map[tmdb_id] = media_type
            
            processed_count += 1
        
        logger.info(f"RSS 媒体库 {self.library_id} TMDB 匹配完成。共处理 {processed_count}/{total_items} 个项目。")

        # 【核心修改】检查并追加手动指定的 TMDB ID
        if self.vlib.fallback_tmdb_id and self.vlib.fallback_tmdb_type:
            logger.info(f"正在为媒体库 {self.library_id} 追加手动指定的 TMDB ID: {self.vlib.fallback_tmdb_id}")
            tmdb_id = self.vlib.fallback_tmdb_id
            media_type = self.vlib.fallback_tmdb_type
            
            if self.vlib.enable_retention:
                existing = self.rss_library_db.fetchone(
                    "SELECT emby_item_id FROM rss_library_items WHERE library_id = ? AND tmdb_id = ?",
                    (self.library_id, tmdb_id)
                )
                emby_item_id = existing['emby_item_id'] if existing else None
                
                self.rss_library_db.execute(
                    "INSERT OR REPLACE INTO rss_library_items (library_id, tmdb_id, media_type, emby_item_id, added_at) VALUES (?, ?, ?, ?, ?)",
                    (self.library_id, tmdb_id, media_type, emby_item_id, current_time_str),
                    commit=True
                )
            else:
                 self.rss_library_db.execute(
                    "INSERT OR IGNORE INTO rss_library_items (library_id, tmdb_id, media_type, emby_item_id) VALUES (?, ?, ?, NULL)",
                    (self.library_id, tmdb_id, media_type),
                    commit=True
                )

            if tmdb_id not in tmdb_ids_map:
                tmdb_ids_map[tmdb_id] = media_type
        
        # 【新增】仅在开启保留功能时执行过期清理逻辑
        if self.vlib.enable_retention and self.vlib.retention_days > 0:
            logger.info(f"正在清理超过 {self.vlib.retention_days} 天未更新的过期项目...")
            
            deleted = self.rss_library_db.execute(
                f"DELETE FROM rss_library_items WHERE library_id = ? AND added_at < datetime('now', 'localtime', '-{self.vlib.retention_days} days')",
                (self.library_id,),
                commit=True
            )
            logger.info(f"过期清理完成，共删除了 {deleted.rowcount} 个项目。")
        elif self.vlib.enable_retention and self.vlib.retention_days == 0:
            logger.info("保留天数设置为 0 (永久保留)，跳过过期清理。")
        
        if tmdb_ids_map:
            self._match_items_in_emby(tmdb_ids_map)

    def _find_items_in_emby(self, tmdb_ids_map):
        """通过调用 Emby API 查找已存在的项目，并根据每个项目的具体类型进行精确查询"""
        if not tmdb_ids_map or not self.config.emby_url:
            return {}

        try:
            headers = credential_service.get_backend_headers_sync(self.config)
        except credential_service.CredentialError as e:
            logger.warning(f"RSS 匹配跳过:后台凭证不可用: {e}")
            return {}

        logger.info(f"将逐个查询 {len(tmdb_ids_map)} 个 TMDB ID 在 Emby 中的存在状态...")

        emby_id_map = {}
        url = f"{self.config.emby_url.rstrip('/')}/Items"

        # 【核心修复】遍历 tmdb_ids_map 的所有项，以同时获取 tmdb_id 和 media_type
        for tmdb_id, media_type in tmdb_ids_map.items():
            # 根据 media_type 确定要查询的 Emby 项目类型
            # 注意：我们前端保存的是 'Movie'/'TV'，而 TMDB API 返回的是 'movie'/'tv'
            if media_type.lower() == 'movie':
                item_types = "Movie"
            elif media_type.lower() == 'tv':
                item_types = "Series"
            else:
                # 对于未知类型，默认同时查找两者以保持兼容性
                item_types = "Movie,Series"
                logger.warning(f"TMDB ID {tmdb_id} 的媒体类型 '{media_type}' 未知，将同时搜索电影和剧集。")

            params = {
                "Recursive": "true",
                "IncludeItemTypes": item_types,
                "Fields": "ProviderIds",
                "TmdbId": tmdb_id
            }
            try:
                logger.info(f"  - 正在为 TMDB ID {tmdb_id} 查询 Emby 中的 '{item_types}'...")
                status, data = credential_service.get_backend_json_sync(url, params, self.config)
                if status != 200:
                    logger.error(f"查询 Emby API 失败 (TMDB ID: {tmdb_id}, Status: {status})")
                    continue
                results = (data or {}).get("Items", []) if isinstance(data, dict) else []

                for item in results:
                    item_tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
                    if str(item_tmdb_id) == str(tmdb_id):
                        emby_id_map[tmdb_id] = item.get("Id")
                        logger.info(f"  - 匹配成功: TMDB ID {tmdb_id} -> Emby ID {item.get('Id')}")
                        break
            except requests.RequestException as e:
                logger.error(f"查询 Emby API 时发生错误 (TMDB ID: {tmdb_id}): {e}")
                continue
        
        logger.info(f"Emby API 查询完成，在您的库中找到了 {len(emby_id_map)} 个匹配的项目。")
        return emby_id_map

    def _match_items_in_emby(self, tmdb_ids_map):
        """获取所有项目的 TMDB ID，查询 Emby，并更新数据库"""
        # 【核心修复】移除子类中重写的 _find_items_in_emby 方法，因为现在基类方法已足够智能
        emby_id_map = self._find_items_in_emby(tmdb_ids_map)
        update_count = 0
        for tmdb_id, emby_item_id in emby_id_map.items():
            self.rss_library_db.execute(
                "UPDATE rss_library_items SET emby_item_id = ? WHERE library_id = ? AND tmdb_id = ?",
                (emby_item_id, self.library_id, tmdb_id),
                commit=True
            )
            update_count += 1
        logger.info(f"Emby 库匹配完成，已更新 {update_count} 个项目的存在状态。")

    def _format_tmdb_to_emby(self, tmdb_data, media_type, tmdb_id, server_id):
        """将从 TMDB API 获取的数据格式化为 Emby 项目所需的简化结构。"""
        is_movie = media_type == 'movie'
        item_type = 'Movie' if is_movie else 'Series'
        
        return {
            "Name": tmdb_data.get('title') if is_movie else tmdb_data.get('name'),
            "ProductionYear": int((tmdb_data.get('release_date') or '0').split('-')[0]) if is_movie else int((tmdb_data.get('first_air_date') or '0').split('-')[0]),
            "Id": f"tmdb-{tmdb_id}",
            "Type": item_type,
            "IsFolder": False,
            "MediaType": "Video" if is_movie else "Series",
            "ServerId": server_id,
            "ImageTags": {"Primary": "placeholder"},
            "HasPrimaryImage": True,
            "PrimaryImageAspectRatio": 0.6666666666666666,
            "ProviderIds": {"Tmdb": str(tmdb_id)},
            "UserData": {"Played": False, "PlayCount": 0, "IsFavorite": False, "PlaybackPositionTicks": 0},
            "Overview": tmdb_data.get("overview"),
            "PremiereDate": tmdb_data.get("release_date") if is_movie else tmdb_data.get("first_air_date"),
        }

    def _fetch_and_cache_tmdb_item(self, tmdb_id, media_type):
        """获取单个 TMDB 项目并将其存入缓存，如果缓存已存在则跳过。"""
        cached = self.tmdb_cache_db.fetchone("SELECT data FROM tmdb_cache WHERE tmdb_id = ? AND media_type = ?", (tmdb_id, media_type))
        if cached:
            return True

        if not self.config.tmdb_api_key: return False

        item_type_path = 'movie' if media_type == 'movie' else 'tv'
        url = f"https://api.themoviedb.org/3/{item_type_path}/{tmdb_id}?api_key={self.config.tmdb_api_key}&language=zh-CN"
        proxies = {"http": self.config.tmdb_proxy, "https": self.config.tmdb_proxy} if self.config.tmdb_proxy else None

        try:
            logger.info(f"正在为 TMDB ID {tmdb_id} ({media_type}) 获取信息并缓存...")
            response = requests.get(url, proxies=proxies, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            server_id = self.config.emby_server_id or "emby"
            emby_item = self._format_tmdb_to_emby(data, media_type, tmdb_id, server_id)
            
            self.tmdb_cache_db.execute(
                "INSERT OR REPLACE INTO tmdb_cache (tmdb_id, media_type, data) VALUES (?, ?, ?)",
                (tmdb_id, media_type, json.dumps(emby_item, ensure_ascii=False)),
                commit=True
            )
            return True
        except Exception as e:
            logger.error(f"从 TMDB 获取信息失败 (ID: {tmdb_id}): {e}")
            return False

    def _precache_tmdb_info(self):
        """为 RSS 库中不存在于 Emby 的项目预先缓存 TMDB 信息。"""
        logger.info(f"开始为媒体库 {self.library_id} 预缓存缺失项目的 TMDB 信息...")
        missing_items = self.rss_library_db.fetchall(
            "SELECT tmdb_id, media_type FROM rss_library_items WHERE library_id = ? AND emby_item_id IS NULL",
            (self.library_id,)
        )

        if not missing_items:
            logger.info("没有需要预缓存的 TMDB 项目。")
            return

        logger.info(f"发现 {len(missing_items)} 个需要预缓存信息的项目。")
        cached_count = 0
        for item in missing_items:
            cached = self._fetch_and_cache_tmdb_item(item['tmdb_id'], item['media_type'])
            if cached:
                cached_count += 1
        
        logger.info(f"TMDB 信息预缓存完成，成功缓存 {cached_count}/{len(missing_items)} 个项目。")
