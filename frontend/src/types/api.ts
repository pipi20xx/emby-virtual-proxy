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
}
