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

# 虚拟库完整解析列表缓存(本地直出,访问加速)
# - 键: (user_id, library_id),值: {"items": [...], "updated_at": unix秒}
# - 新鲜度由 handler_items 的软/硬 TTL 管理,这里只做容量上限(LRU 淘汰)
serving_items_cache = LRUCache(maxsize=32)


def set_api_cache_ttl(ttl_seconds: int) -> None:
    """按配置重建 api_cache 以应用新 TTL;ttl<=0 视为禁用(读写全部短路)。"""
    global api_cache, api_cache_disabled
    ttl = max(0, int(ttl_seconds))
    api_cache_disabled = ttl <= 0
    if ttl > 0:
        api_cache = TTLCache(maxsize=500, ttl=ttl)
