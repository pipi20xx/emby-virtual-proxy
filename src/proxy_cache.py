# src/proxy_cache.py

from cachetools import TTLCache, LRUCache

# 创建一个全局的、线程安全的缓存实例
# - maxsize=500: 最多缓存 500 个不同的 API 响应
# - ttl 可由配置 api_cache_ttl 动态调整(set_api_cache_ttl 重建实例)
# 注意必须用 LRUCache/TTLCache(有淘汰策略);基础 Cache 满容会直接抛 ValueError
api_cache = TTLCache(maxsize=500, ttl=300)
api_cache_disabled = False

# 【【【 新增 】】】
# 虚拟库项目列表缓存 (用于封面生成)
# - maxsize=100: 最多缓存100个虚拟库的项目列表
# 这个缓存不需要时间过期，因为它只在用户浏览时更新
vlib_items_cache = LRUCache(maxsize=100)

# 本地直出分层缓存(访问加速):
# - 共享元数据:每库一份、所有用户共用(已剥离 UserData)
vlib_metadata_cache = LRUCache(maxsize=32)   # library_id -> {"items": [...], "updated_at": unix秒}
# - 用户叠加层:每 用户+库 一份,只存 {条目ID: UserData}(体积约为元数据的 5%)
user_overlay_cache = LRUCache(maxsize=64)    # (user_id, library_id) -> {"data": {...}, "updated_at": unix秒}


def set_api_cache_ttl(ttl_seconds: int) -> None:
    """按配置重建 api_cache 以应用新 TTL;ttl<=0 视为禁用(读写全部短路)。"""
    global api_cache, api_cache_disabled
    ttl = max(0, int(ttl_seconds))
    api_cache_disabled = ttl <= 0
    if ttl > 0:
        api_cache = TTLCache(maxsize=500, ttl=ttl)
