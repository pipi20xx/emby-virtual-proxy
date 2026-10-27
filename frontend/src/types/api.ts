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
}

/** 用户状态层(叠加层) */
export interface CacheStatsOverlay {
  user_id: string
  source: string
  entries: number
  age_seconds: number
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
}
