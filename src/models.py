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
    rules: List[AdvancedFilterRule] = Field(default_factory=list)

class VirtualLibrary(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    resource_type: Literal["collection", "tag", "genre", "studio", "person", "all", "rsshub"]
    resource_id: Optional[str] = None
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

class AppConfig(BaseModel):
    emby_url: str = Field(default="http://127.0.0.1:8096")
    emby_api_key: Optional[str] = Field(default="")
    emby_server_id: Optional[str] = Field(default=None) # 新增：用于TMDB缓存占位符的备用服务器ID
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
