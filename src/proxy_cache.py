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
# - 全库基础数据源:裸拉(不筛选、不合并)的原始条目,【所有虚拟库共享同一份】
#   这是缓存的最底层,虚拟库的规则/资源过滤都在此之上本地派生,不再各自向 Emby 拉一遍
vlib_base_cache = LRUCache(maxsize=8)        # source_key -> {"items": [...], "updated_at": 秒, "sig": 指纹}
# - 共享元数据:每库一份派生结果(已过滤/已合并/已剥离 UserData),所有用户共用
vlib_metadata_cache = LRUCache(maxsize=32)   # library_id -> {"items": [...], "updated_at": unix秒}
# - 用户叠加层:每 用户+数据源 一份,只存 {条目ID: UserData}(体积约为元数据的 5%)
#   key 用数据源而非虚拟库:同一批内容的多个虚拟库共用一份叠加层
user_overlay_cache = LRUCache(maxsize=64)    # (user_id, source_key) -> {"data": {...}, "updated_at": unix秒}
# - 直出结果层:已组装(叠加 UserData)+ 已排序的可见条目列表,翻页只需切片
serving_result_cache = LRUCache(maxsize=16)  # (库,用户,元数据版本,叠加层版本,类型,排序...) -> {items,total}
# - 季/集合并结果短时缓存(详情页每次进入都要合并,元素含用户自己的 UserData,按用户隔离)
merge_result_cache = TTLCache(maxsize=256, ttl=900)
# 合并结果缓存开关(随配置刷新;由 proxy_server 每请求按 config 同步,避免 handler 里再深拷贝配置)
merge_cache_enabled = True


def clear_vlib_caches() -> None:
    """清空全部虚拟库相关内存缓存(base / 元数据 / 叠加层 / 结果层 / 合并结果)。

    注意:不含 api_cache(响应缓存),由调用方按需决定是否一并清空。
    """
    vlib_base_cache.clear()
    vlib_metadata_cache.clear()
    user_overlay_cache.clear()
    serving_result_cache.clear()
    merge_result_cache.clear()


def drop_vlib_memory(library_id: str) -> None:
    """精准移除某个虚拟库的内存缓存(数据源与叠加层按数据源共享,不在此处清除)。"""
    vlib_items_cache.pop(library_id, None)
    vlib_metadata_cache.pop(library_id, None)
    for key in [k for k in list(serving_result_cache.keys()) if k and k[0] == library_id]:
        serving_result_cache.pop(key, None)


def drop_source_overlays(source_key: str) -> None:
    """数据源内容变化后,丢弃其全部用户叠加层(叠加层按条目 Id 建,内容变了就失效)。"""
    for key in [k for k in list(user_overlay_cache.keys()) if k and k[1] == source_key]:
        user_overlay_cache.pop(key, None)


def set_api_cache_ttl(ttl_seconds: int) -> None:
    """按配置重建 api_cache 以应用新 TTL;ttl<=0 视为禁用(读写全部短路)。"""
    global api_cache, api_cache_disabled
    ttl = max(0, int(ttl_seconds))
    api_cache_disabled = ttl <= 0
    if ttl > 0:
        api_cache = TTLCache(maxsize=500, ttl=ttl)
