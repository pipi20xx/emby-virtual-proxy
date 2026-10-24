import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'

import * as embyApi from '@/api/emby'
import { getAllLibraries } from '@/api/libraries'
import type { Classifications, EmbyItemBrief, LibraryBrief } from '@/types/api'
import { RESOURCE_PLURAL_KEY } from '@/types/config'

export type EmbyLoadState = 'idle' | 'loading' | 'ok' | 'error'

/** 来自 Emby 的辅助数据:分类/库列表/人名缓存 */
export const useEmbyDataStore = defineStore('embyData', () => {
  const classifications = ref<Classifications>({
    collections: [],
    genres: [],
    tags: [],
    studios: [],
    persons: [],
  })

  const allLibraries = ref<LibraryBrief[]>([])

  /** personId -> 姓名('' 表示尚未解析,失败时存 '未知') */
  const personNameCache = reactive<Record<string, string>>({})

  const loadState = ref<EmbyLoadState>('idle')

  /** 拉取分类与库列表;单边失败不影响另一边 */
  async function load(): Promise<void> {
    loadState.value = 'loading'
    const [clsResult, libsResult] = await Promise.allSettled([
      embyApi.getClassifications(),
      getAllLibraries(),
    ])
    let failed = false

    if (clsResult.status === 'fulfilled') {
      const cls = clsResult.value
      const next: Classifications = {
        collections: cls.collections ?? [],
        genres: cls.genres ?? [],
        tags: cls.tags ?? [],
        studios: cls.studios ?? [],
        persons: cls.persons ?? [],
      }
      classifications.value = next
    } else {
      failed = true
    }
    if (libsResult.status === 'fulfilled') {
      allLibraries.value = libsResult.value
    } else {
      failed = true
    }

    loadState.value = failed ? 'error' : 'ok'
  }

  /** 从 classifications 里取资源显示名 */
  function findResourceName(resourceType: string, resourceId: string | null): EmbyItemBrief | null {
    if (!resourceId) return null
    const key = RESOURCE_PLURAL_KEY[resourceType]
    if (!key) return null
    const list = classifications.value[key] ?? []
    return list.find((item) => item.id === resourceId) ?? null
  }

  /** person 库的资源名:优先缓存,否则后台解析 */
  function personName(personId: string): string {
    return personNameCache[personId] ?? ''
  }

  async function resolvePersonName(personId: string): Promise<string> {
    if (personNameCache[personId]) return personNameCache[personId]
    personNameCache[personId] = ''
    try {
      const item = await embyApi.resolveItem(personId)
      personNameCache[personId] = item.name || '未知'
    } catch {
      personNameCache[personId] = '未知'
    }
    return personNameCache[personId]
  }

  return {
    classifications,
    allLibraries,
    personNameCache,
    loadState,
    load,
    findResourceName,
    personName,
    resolvePersonName,
  }
})
