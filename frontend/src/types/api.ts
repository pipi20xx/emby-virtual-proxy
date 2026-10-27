/** 后端管理 API 的辅助类型 */

export interface EmbyItemBrief {
  name: string
  id: string
}

export interface Classifications {
  collections: EmbyItemBrief[]
  genres: EmbyItemBrief[]
  tags: EmbyItemBrief[]
  studios: EmbyItemBrief[]
  persons: EmbyItemBrief[]
}

export interface LibraryBrief {
  id: string
  name: string
  type: 'real' | 'virtual'
  collectionType?: string | null
}

export interface CoverGenerateResult {
  success: boolean
  image_tag: string
  /** 生成封面的可直接预览地址(/covers/{id}.{ext}?t={tag}) */
  image_url: string
}

/** 全库数据源快照(所有虚拟库共享的那一份) */
export interface CacheStatsBase {
  source: string
  items: number
  age_seconds: number | null
  sig: string | null
}

/** 单个虚拟库的派生结果 */
export interface CacheStatsLibrary {
  id: string
  name: string
  resource_type: string
  items: number
  age_seconds: number | null
  /** 绑定的高级筛选器名称(null = 未绑定) */
  filter_name?: string | null
  /** 筛选器执行模式:local = 本地全量,native = 交给 Emby */
  filter_mode?: string | null
  /** 是否能从共享全库数据源本地派生(否则该库仍逐库向 Emby 拉取) */
  local_capable?: boolean
}

/** 用户状态层(叠加层) */
export interface CacheStatsOverlay {
  user_id: string
  source: string
  entries: number
  age_seconds: number
}

/** 一个定时任务的下次运行时间 */
export interface CacheScheduleJob {
  id: string
  next_run_at: string | null
  next_run_in_seconds: number | null
}

export interface CacheSchedule {
  vlib_refresh?: CacheScheduleJob | null
  tmdb_index?: CacheScheduleJob | null
  rss_refresh?: CacheScheduleJob | null
}

export interface CacheStatsConfig {
  vlib_cache_enabled: boolean
  local_serving_enabled: boolean
  refresh_interval_hours: number
  api_cache_enabled: boolean
  api_cache_ttl: number
  merge_cache_enabled: boolean
  enable_tmdb_index: boolean
  tmdb_index_refresh_interval: number
  image_cache_enabled: boolean
  image_cache_max_mb: number
}

export interface CacheStatsTtls {
  meta_soft: number
  meta_hard: number
  overlay_soft: number
  overlay_hard: number
  base_soft: number
  merge: number
}

export interface CacheStats {
  available: boolean
  reason?: string
  base?: CacheStatsBase
  libraries?: CacheStatsLibrary[]
  overlays?: CacheStatsOverlay[]
  counters?: Record<string, number>
  caches?: Record<string, number>
  images?: { files?: number; bytes?: number; max_bytes?: number }
  /** 通用响应缓存(所有 GET JSON,含首页最新/分区/视图) */
  api_cache?: { enabled: boolean; ttl_seconds: number; entries: number }
  /** 封面素材缓存(每库前若干项,供封面生成取材) */
  cover_materials?: { libraries: number }
  /** TMDB 索引(条目 → TMDB ID) */
  tmdb_index?: { count: number; age_seconds: number | null; enabled: boolean }
  schedule?: CacheSchedule
  config?: CacheStatsConfig
  ttls?: CacheStatsTtls
}
