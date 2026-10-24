/**
 * 与后端 Pydantic 模型(src/models.py)一一对应的类型定义。
 * 注意:后端序列化 AppConfig 时 virtual_libraries 使用别名 'library'。
 */

import type { Classifications } from './api'

export type ResourceType = 'collection' | 'tag' | 'genre' | 'studio' | 'person' | 'all' | 'rsshub'
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
  rules: AdvancedFilterRule[]
}

export interface VirtualLibrary {
  id: string
  name: string
  resource_type: ResourceType
  resource_id: string | null
  image_tag: string | null
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
  emby_server_id: string | null
  log_level: string
  display_order: string[]
  hide: string[]
  /** 后端别名:virtual_libraries 序列化为 'library' */
  library: VirtualLibrary[]
  advanced_filters: AdvancedFilter[]
  enable_cache: boolean
  default_cover_style: string
  show_missing_episodes: boolean
  tmdb_api_key: string
  tmdb_proxy: string
  rss_refresh_interval: number | null
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

export const COVER_STYLES = [
  { value: 'style_multi_1', title: '样式一(多图拼接)' },
  { value: 'style_single_1', title: '样式二(单图)' },
  { value: 'style_single_2', title: '样式三(单图)' },
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
    image_tag: null,
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
    emby_server_id: null,
    log_level: 'info',
    display_order: [],
    hide: [],
    library: [],
    advanced_filters: [],
    enable_cache: true,
    default_cover_style: 'style_multi_1',
    show_missing_episodes: false,
    tmdb_api_key: '',
    tmdb_proxy: '',
    rss_refresh_interval: 0,
    force_merge_by_tmdb_id: false,
    custom_zh_font_path: '',
    custom_en_font_path: '',
    custom_image_path: '',
    admin_password: '',
  }
}
