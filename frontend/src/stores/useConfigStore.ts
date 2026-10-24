import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import * as configApi from '@/api/config'
import { ApiError } from '@/api/client'
import type { AppConfig } from '@/types/config'

/** 全局配置(config.json)的读写,唯一数据源 */
export const useConfigStore = defineStore('config', () => {
  const config = ref<AppConfig | null>(null)
  const loaded = ref(false)
  const loadError = ref<string | null>(null)
  const saving = ref(false)

  const virtualLibraries = computed(() => config.value?.library ?? [])
  const advancedFilters = computed(() => config.value?.advanced_filters ?? [])

  async function load(force = false): Promise<AppConfig | null> {
    if (loaded.value && !force && config.value) return config.value
    loadError.value = null
    try {
      config.value = await configApi.getConfig()
      loaded.value = true
    } catch (error) {
      loadError.value = error instanceof ApiError ? error.message : '配置加载失败'
    }
    return config.value
  }

  /** 保存整个配置并以后端返回值覆盖本地 */
  async function save(next: AppConfig): Promise<AppConfig> {
    saving.value = true
    try {
      const saved = await configApi.saveConfig(next)
      config.value = saved
      loaded.value = true
      return saved
    } finally {
      saving.value = false
    }
  }

  async function reload(): Promise<AppConfig | null> {
    loaded.value = false
    return load(true)
  }

  function getLibraryById(id: string) {
    return config.value?.library.find((lib) => lib.id === id) ?? null
  }

  function getFilterById(id: string | null) {
    if (!id) return null
    return config.value?.advanced_filters.find((f) => f.id === id) ?? null
  }

  return {
    config,
    loaded,
    loadError,
    saving,
    virtualLibraries,
    advancedFilters,
    load,
    save,
    reload,
    getLibraryById,
    getFilterById,
  }
})
