# src/models.py (Final Corrected Version)

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Literal, Optional
import uuid

class AdvancedFilterRule(BaseModel):
    field: str
    operator: Literal[
        "equals", "not_equals",
        "contains", "not_contains",
        "greater_than", "less_than",
        "is_empty", "is_not_empty"
    ]
    value: Optional[str] = None
    relative_days: Optional[int] = None # 新增：用于存储相对日期（例如 30 天）

class AdvancedFilter(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    match_all: bool = Field(default=True)
    # 执行模式:native = 混合执行(能翻译的走 Emby 原生参数,其余后筛选兜底,与历史行为一致);
    # local = 本地全量(裸拉全量数据,全部规则由代理端评估,支持同字段 AND 与 OR 匹配)。
    # 存量配置无此字段,默认 native 保证行为不变;前端新建筛选器默认 local。
    execution_mode: Literal["native", "local"] = Field(default="native")
    rules: List[AdvancedFilterRule] = Field(default_factory=list)

class VirtualLibrary(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    resource_type: Literal["collection", "tag", "genre", "studio", "person", "all", "rsshub"]
    resource_id: Optional[str] = None
    # 展示类型:客户端按哪种媒体库界面渲染(tvshows=电视剧, movies=电影, mixed=混合)。
    # None = 自动推导:全库(all)按混合呈现,其余沿用电视剧(兼容存量配置),见 handler_views.get_vlib_collection_type
    collection_type: Optional[Literal["tvshows", "movies", "mixed"]] = None
    # image: Optional[str] = None  <-- 我们不再需要这个字段了，可以删除或注释掉
    image_tag: Optional[str] = None # <-- 【新增】用于存储图片的唯一标签
    cover_format: Optional[str] = None # <-- 【新增】封面文件格式(jpg/gif/webp/png),用于定位封面文件
    rsshub_url: Optional[str] = None # <-- 【新增】RSSHUB链接
    rss_type: Optional[Literal["douban", "bangumi"]] = None # <-- 【新增】RSS类型
    fallback_tmdb_id: Optional[str] = None # <-- 【新增】RSS库的兜底TMDB ID
    fallback_tmdb_type: Optional[Literal["Movie", "TV"]] = None # <-- 【新增】RSS库的兜底TMDB类型
    enable_retention: bool = Field(default=False) # <-- 【新增】是否开启数据保留功能
    retention_days: int = Field(default=7) # <-- 【新增】RSS项目保留天数，默认7天
    advanced_filter_id: Optional[str] = None
    merge_by_tmdb_id: bool = Field(default=False)
    order: int = 0
    source_library: Optional[str] = None
    conditions: Optional[list] = None
    cover_custom_zh_font_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义中文字体
    cover_custom_en_font_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义英文字体
    cover_custom_image_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义图片目录
    # --- 封面自动刷新配方:手动生成时固化,内容变化后自动重绘沿用同样外观 ---
    cover_style: Optional[str] = Field(default=None) # <-- 【新增】上次生成使用的样式
    cover_title_zh: Optional[str] = Field(default=None) # <-- 【新增】上次生成的中文标题
    cover_title_en: Optional[str] = Field(default=None) # <-- 【新增】上次生成的英文标题
    cover_animation_format: Optional[str] = Field(default=None) # <-- 【新增】上次生成的动图格式(gif/webp/png)
    cover_source_hash: Optional[str] = Field(default=None) # <-- 【新增】上次生成时的内容指纹(条目 Id 集合),未变化则不重绘

class AppConfig(BaseModel):
    emby_url: str = Field(default="http://127.0.0.1:8096")
    emby_api_key: Optional[str] = Field(default="")
    emby_server_id: Optional[str] = Field(default=None) # 新增：用于TMDB缓存占位符的备用服务器ID
    emby_proxy: Optional[str] = Field(default="") # 新增：访问 Emby 上游的 HTTP 代理(留空直连)
    log_level: Literal["debug", "info", "warn", "error"] = Field(default="info")
    display_order: List[str] = Field(default_factory=list)
    hide: List[str] = Field(default_factory=list)
    
    # 使用别名 'library' 来兼容旧的 config.json
    virtual_libraries: List[VirtualLibrary] = Field(
        default_factory=list, 
        alias="library",
        validation_alias="library" # <-- 【新增】确保加载时也优先用 'library'
    )
    
    # 明确定义 advanced_filters，不使用任何复杂的配置
    advanced_filters: List[AdvancedFilter] = Field(default_factory=list)

    # 新增：缓存开关
    enable_cache: bool = Field(default=True)

    # 新增：API 响应缓存 TTL(秒),0 为禁用响应缓存
    api_cache_ttl: int = Field(default=300)

    # 新增：虚拟库列表本地直出开关(命中解析缓存时不请求 Emby,加速实际访问;过期后台刷新)
    enable_vlib_local_serving: bool = Field(default=True)

    # 新增：TMDB 全库索引开关(季/集合并加速);关闭后退回旧的全库遍历方案
    enable_tmdb_index: bool = Field(default=True)

    # 新增：季/集合并结果短时缓存开关(进详情页不再每次逐剧集拉季/集);关闭后回退为实时合并
    enable_merge_cache: bool = Field(default=True)

    # 新增：图片(海报/背景)本地化缓存开关与磁盘配额(MB);0 表示不限制
    enable_image_cache: bool = Field(default=True)
    image_cache_max_mb: int = Field(default=2048)

    # 新增：图片缓存是否要求请求携带凭证(Cookie/token)。
    # 默认 False:多数 Emby 的 /Items/{id}/Images 接口本身不鉴权(实测直连不带凭证也 200),
    # 而浏览器 <img> 请求通常不带任何可识别的凭证 —— 开启会命中率归零。
    # 只有当你的 Emby 确实对图片接口做了鉴权时,才需要打开。
    image_cache_require_credential: bool = Field(default=False)

    # 新增：TMDB 索引重建间隔(小时),0 表示仅启动时构建一次、之后靠查询自愈回写维持
    tmdb_index_refresh_interval: int = Field(default=24)
    
    # 新增：自动生成封面的默认样式
    default_cover_style: str = Field(default='style_multi_1')

    # 新增：显示缺失剧集的开关
    show_missing_episodes: bool = Field(default=False)

    # 新增：TMDB API Key
    tmdb_api_key: Optional[str] = Field(default="")

    # 新增：TMDB HTTP 代理
    tmdb_proxy: Optional[str] = Field(default="")

    # 新增：RSS 定时刷新间隔（小时），0为禁用
    rss_refresh_interval: Optional[int] = Field(default=0)

    # 新增：虚拟库项目缓存总开关(内存 + SQLite 持久化 + 预热/定时刷新);关闭后封面生成按需实时向 Emby 取材
    enable_vlib_cache: bool = Field(default=True)

    # 新增：虚拟库项目缓存定时预热间隔（小时），0为禁用；启用后启动时也会全量预热一轮
    vlib_cache_refresh_interval: Optional[int] = Field(default=0)

    # 新增：全局强制 TMDB ID 合并
    force_merge_by_tmdb_id: bool = Field(default=False)

    # 新增：自定义字体路径
    custom_zh_font_path: Optional[str] = Field(default="")
    custom_en_font_path: Optional[str] = Field(default="")
    custom_image_path: Optional[str] = Field(default="") # <-- 【新增】全局自定义图片目录

    # 新增：管理面板登录密码。留空 = 面板无需登录(局域网工具默认)
    admin_password: Optional[str] = Field(default="")

    class Config:
        # 允许从别名填充模型
        populate_by_name = True
