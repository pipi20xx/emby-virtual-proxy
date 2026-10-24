# src/db_manager.py —— SQLite 缓存库管理
"""各功能缓存库(RSS/豆瓣/Bangumi/TMDB)的统一访问入口。

- 每个 db_path 一个 DBManager 单例,内含一把写锁串行化查询。
- 每线程持有自己的持久连接(threading.local)。
- 所有库文件都存放在 config 目录下。
"""

import sqlite3
import threading
import datetime
from pathlib import Path

# 数据库文件都存放在 config 目录下
DB_DIR = Path(__file__).parent.parent / "config"
DB_DIR.mkdir(exist_ok=True)

# 为每个功能定义独立的数据库文件
RSS_CACHE_DB = DB_DIR / "rss_cache.db"
DOUBAN_CACHE_DB = DB_DIR / "douban_cache.db"
BANGUMI_CACHE_DB = DB_DIR / "bangumi_cache.db"
TMDB_CACHE_DB = DB_DIR / "tmdb_cache.db"
RSS_LIBRARY_ITEMS_DB = DB_DIR / "rss_library_items.db"


class DBManager:
    """SQLite 连接管理器:每 db_path 单例 + 每线程独立连接。"""

    _instances = {}
    _locks = {}

    def __new__(cls, db_path):
        if db_path not in cls._instances:
            cls._instances[db_path] = super(DBManager, cls).__new__(cls)
            cls._locks[db_path] = threading.Lock()
        return cls._instances[db_path]

    def __init__(self, db_path):
        self.db_path = db_path
        # 单例会被反复构造,线程本地存储只初始化一次
        if not hasattr(self, "_local"):
            self._local = threading.local()

    def get_conn(self):
        """返回当前线程的持久连接。

        (修复:旧实现每次调用都新建 threading.local 和连接,
        等于每条查询开一个新连接,存在连接泄漏。)
        """
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    def execute(self, query, params=(), commit=False):
        with self._locks[self.db_path]:
            conn = self.get_conn()
            cursor = conn.cursor()
            cursor.execute(query, params)
            if commit:
                conn.commit()
            return cursor

    def fetchall(self, query, params=()):
        cursor = self.execute(query, params)
        return cursor.fetchall()

    def fetchone(self, query, params=()):
        cursor = self.execute(query, params)
        return cursor.fetchone()

    def close(self):
        """关闭当前线程的连接(下次 get_conn 会自动重开)。"""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def init_databases():
    # 初始化 RSS 缓存数据库
    rss_db = DBManager(RSS_CACHE_DB)
    rss_db.execute("""
    CREATE TABLE IF NOT EXISTS rss_cache (
        url TEXT PRIMARY KEY,
        content TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """, commit=True)

    # 初始化豆瓣缓存和映射数据库
    douban_db = DBManager(DOUBAN_CACHE_DB)
    douban_db.execute("""
    CREATE TABLE IF NOT EXISTS douban_api_cache (
        douban_id TEXT PRIMARY KEY,
        api_response TEXT,
        name TEXT, -- 新增：存储条目名称
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """, commit=True)
    douban_db.execute("""
    CREATE TABLE IF NOT EXISTS douban_tmdb_mapping (
        douban_id TEXT PRIMARY KEY,
        tmdb_id TEXT,
        media_type TEXT,
        match_method TEXT, -- 新增：存储匹配方法
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """, commit=True)

    # --- 豆瓣数据库迁移 ---
    try:
        cursor = douban_db.execute("PRAGMA table_info(douban_api_cache)")
        columns = [row['name'] for row in cursor.fetchall()]
        if 'name' not in columns:
             print("Adding 'name' column to 'douban_api_cache' table.")
             douban_db.execute("ALTER TABLE douban_api_cache ADD COLUMN name TEXT", commit=True)

        cursor = douban_db.execute("PRAGMA table_info(douban_tmdb_mapping)")
        columns = [row['name'] for row in cursor.fetchall()]
        if 'match_method' not in columns:
             print("Adding 'match_method' column to 'douban_tmdb_mapping' table.")
             douban_db.execute("ALTER TABLE douban_tmdb_mapping ADD COLUMN match_method TEXT", commit=True)
    except Exception as e:
        print(f"Error updating douban tables schema: {e}")

    # 初始化 Bangumi 缓存和映射数据库
    bangumi_db = DBManager(BANGUMI_CACHE_DB)
    bangumi_db.execute("""
    CREATE TABLE IF NOT EXISTS bangumi_api_cache (
        bangumi_id TEXT PRIMARY KEY,
        api_response TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """, commit=True)
    bangumi_db.execute("""
    CREATE TABLE IF NOT EXISTS bangumi_tmdb_mapping (
        bangumi_id TEXT PRIMARY KEY,
        tmdb_id TEXT,
        media_type TEXT,
        match_method TEXT,
        score REAL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """, commit=True)

    # 初始化 TMDB 缓存数据库
    tmdb_db = DBManager(TMDB_CACHE_DB)
    tmdb_db.execute("""
    CREATE TABLE IF NOT EXISTS tmdb_cache (
        tmdb_id TEXT PRIMARY KEY,
        media_type TEXT,
        data TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """, commit=True)

    # 初始化 RSS 虚拟库项目数据库
    rss_library_db = DBManager(RSS_LIBRARY_ITEMS_DB)
    rss_library_db.execute("""
    CREATE TABLE IF NOT EXISTS rss_library_items (
        library_id TEXT,
        tmdb_id TEXT,
        media_type TEXT,
        emby_item_id TEXT, -- 新增：用于存储在 Emby 中匹配到的 Item ID
        added_at DATETIME DEFAULT CURRENT_TIMESTAMP, -- 新增：添加时间，用于过期清理
        PRIMARY KEY (library_id, tmdb_id)
    )
    """, commit=True)

    # --- 安全地为旧表添加新列 ---
    try:
        cursor = rss_library_db.execute("PRAGMA table_info(rss_library_items)")
        columns = [row['name'] for row in cursor.fetchall()]

        if 'emby_item_id' not in columns:
            print("Adding 'emby_item_id' column to 'rss_library_items' table.")
            rss_library_db.execute("ALTER TABLE rss_library_items ADD COLUMN emby_item_id TEXT", commit=True)

        if 'added_at' not in columns:
            print("Adding 'added_at' column to 'rss_library_items' table.")
            # 为旧数据设置当前时间，避免被误删。使用固定时间字符串以兼容 SQLite 限制。
            now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            rss_library_db.execute(f"ALTER TABLE rss_library_items ADD COLUMN added_at DATETIME DEFAULT '{now_str}'", commit=True)

    except Exception as e:
        print(f"Error updating rss_library_items table schema: {e}")


# 在模块加载时执行初始化
init_databases()
