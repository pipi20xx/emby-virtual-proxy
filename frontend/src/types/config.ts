/**
 * 与后端 Pydantic 模型(src/models.py)一一对应的类型定义。
 * 注意:后端序列化 AppConfig 时 virtual_libraries 使用别名 'library'。
 */

import type { Classifications } from './api'

export type ResourceType = 'collection' | 'tag' | 'genre' | 'studio' | 'person' | 'all' | 'rsshub'
export type CollectionType = 'tvshows' | 'movies' | 'mixed'
export type RssType = 'douban' | 'bangumi'
export type TmdbType = 'Movie' | 'TV'
export type FilterOperator =
  | 'equals'
  | 'not_equals'
  | 'contains'
  | 'not_contains'
  | 'greater_than'
  | 'less_than'
  | 'is_empty'
  | 'is_not_empty'

export interface AdvancedFilterRule {
  field: string
  operator: FilterOperator | string
  value: string | null
  relative_days: number | null
}

export interface AdvancedFilter {
  id: string
  name: string
  match_all: boolean
  /** 执行模式:native = Emby 原生参数优先(历史行为);local = 代理裸拉全量后本地评估(新默认) */
  execution_mode?: 'native' | 'local'
  rules: AdvancedFilterRule[]
}

export interface VirtualLibrary {
  id: string
  name: string
  resource_type: ResourceType
  resource_id: string | null
  /** 展示类型:客户端按哪种媒体库界面渲染;null = 自动(全库→混合,其余→电视剧) */
  collection_type: CollectionType | null
  image_tag: string | null
  /** 封面文件格式(jpg/gif/webp/png),动图样式生成后为非 jpg */
  cover_format: string | null
  rsshub_url: string | null
  rss_type: RssType | null
  fallback_tmdb_id: string | null
  fallback_tmdb_type: TmdbType | null
  enable_retention: boolean
  retention_days: number
  advanced_filter_id: string | null
  merge_by_tmdb_id: boolean
  order: number
  source_library: string | null
  conditions: unknown[] | null
  cover_custom_zh_font_path: string | null
  cover_custom_en_font_path: string | null
  cover_custom_image_path: string | null
}

export interface AppConfig {
  emby_url: string
  emby_api_key: string
  /** 访问 Emby 上游的 HTTP 代理(http://[user:pass@]host:port),留空直连 */
  emby_proxy: string
  emby_server_id: string | null
  log_level: string
  display_order: string[]
  hide: string[]
  /** 后端别名:virtual_libraries 序列化为 'library' */
  library: VirtualLibrary[]
  advanced_filters: AdvancedFilter[]
  enable_cache: boolean
  /** 虚拟库项目缓存总开关(内存 + SQLite + 预热/定时);关闭后封面生成按需实时向 Emby 取材 */
  enable_vlib_cache: boolean
  /** 虚拟库列表本地直出:命中解析缓存时不请求 Emby,加速实际访问 */
  enable_vlib_local_serving: boolean
  /** TMDB 全库索引开关(季/集合并加速);关闭后退回全库遍历方案 */
  enable_tmdb_index: boolean
  /** 季/集合并结果短时缓存:进详情页不再每次逐剧集拉季/集 */
  enable_merge_cache: boolean
  /** 海报/背景图本地化缓存;关闭后图片全部直连 Emby */
  enable_image_cache: boolean
  /** 图片缓存磁盘上限(MB);0 = 不限制 */
  image_cache_max_mb: number
  /** TMDB 索引重建间隔(小时),0 = 仅启动时构建一次 */
  tmdb_index_refresh_interval: number
  /** API 响应缓存 TTL(秒),0 = 禁用 */
  api_cache_ttl: number
  default_cover_style: string
  show_missing_episodes: boolean
  tmdb_api_key: string
  tmdb_proxy: string
  rss_refresh_interval: number | null
  /** 虚拟库项目缓存定时预热间隔(小时),0 = 禁用 */
  vlib_cache_refresh_interval: number | null
  force_merge_by_tmdb_id: boolean
  custom_zh_font_path: string
  custom_en_font_path: string
  custom_image_path: string
  /** 管理面板登录密码;空字符串 = 无需登录 */
  admin_password: string
}

export const RESOURCE_TYPE_LABELS: Record<ResourceType, string> = {
  collection: '合集',
  tag: '标签',
  genre: '类型',
  studio: '工作室',
  person: '人员',
  all: '全库',
  rsshub: 'RSSHUB',
}

/** 资源类型 -> classifications 数据里的复数键 */
export const RESOURCE_PLURAL_KEY: Record<string, keyof Classifications> = {
  collection: 'collections',
  tag: 'tags',
  genre: 'genres',
  studio: 'studios',
  person: 'persons',
}

/** 展示类型中文标签(null = 自动推导,由后端按资源类型决定) */
export const COLLECTION_TYPE_LABELS: Record<CollectionType, string> = {
  tvshows: '电视剧库',
  movies: '电影库',
  mixed: '混合(电影+剧集)',
}

export interface CoverStyleOption {
  value: string
  title: string
  /** 动图样式(GIF/WebP/APNG 输出) */
  animated?: boolean
}

export const COVER_STYLES: CoverStyleOption[] = [
  { value: 'style_multi_1', title: '样式一(多图拼接)' },
  { value: 'style_single_1', title: '样式二(单图)' },
  { value: 'style_single_2', title: '样式三(单图)' },
  { value: 'style_multi_animated', title: '多图九宫格(动态)', animated: true },
  { value: 'style_6_plus_1', title: '背景轮播(动态)', animated: true },
  { value: 'style_9_plus_1', title: '背景斜播(动态)', animated: true },
  { value: 'style_5_horizontal', title: '横向轮播(动态)', animated: true },
]

/** 动图样式的输出格式选项 */
export const ANIMATION_FORMATS = [
  { value: 'gif', title: 'GIF(兼容性最好)' },
  { value: 'webp', title: 'WebP(体积最小)' },
  { value: 'png', title: 'APNG(画质最好)' },
]

/** 首页可以隐藏的真实库类型 */
export const HIDE_TYPE_PRESETS = [
  'movies',
  'tvshows',
  'music',
  'playlists',
  'musicvideos',
  'livetv',
  'boxsets',
  'photos',
  'homevideos',
  'books',
]

export function createEmptyLibrary(): VirtualLibrary {  return {
    id: '',
    name: '',
    resource_type: 'collection',
    resource_id: null,
    collection_type: null,
    image_tag: null,
    cover_format: null,
    rsshub_url: null,
    rss_type: null,
    fallback_tmdb_id: null,
    fallback_tmdb_type: null,
    enable_retention: false,
    retention_days: 7,
    advanced_filter_id: null,
    merge_by_tmdb_id: false,
    order: 0,
    source_library: null,
    conditions: null,
    cover_custom_zh_font_path: null,
    cover_custom_en_font_path: null,
    cover_custom_image_path: null,
  }
}

/** 后端 AppConfig 的默认值(与 Pydantic 字段默认一致),用于表单初始化占位 */
export function createDefaultConfig(): AppConfig {
  return {
    emby_url: '',
    emby_api_key: '',
    emby_proxy: '',
    emby_server_id: null,
    log_level: 'info',
    display_order: [],
    hide: [],
    library: [],
    advanced_filters: [],
    enable_cache: true,
    enable_vlib_cache: true,
    enable_vlib_local_serving: true,
    enable_tmdb_index: true,
    enable_merge_cache: true,
    enable_image_cache: true,
    image_cache_max_mb: 2048,
    tmdb_index_refresh_interval: 24,
    api_cache_ttl: 300,
    default_cover_style: 'style_multi_1',
    show_missing_episodes: false,
    tmdb_api_key: '',
    tmdb_proxy: '',
    rss_refresh_interval: 0,
    vlib_cache_refresh_interval: 0,
    force_merge_by_tmdb_id: false,
    custom_zh_font_path: '',
    custom_en_font_path: '',
    custom_image_path: '',
    admin_password: '',
  }
}
