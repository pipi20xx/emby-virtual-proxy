import { computed, watchEffect } from 'vue'
import { useTheme } from 'vuetify'
import { useStorage } from '@vueuse/core'

export type ThemeName = 'light' | 'dark'

/** 主题切换:持久化到 localStorage,同步给 Vuetify 全局主题 */
export function useThemeControl() {
  const vuetifyTheme = useTheme()
  const initial: ThemeName = vuetifyTheme.global.name.value === 'dark' ? 'dark' : 'light'
  const stored = useStorage<ThemeName>('evp-theme', initial)
  const isDark = computed(() => stored.value === 'dark')

  watchEffect(() => {
    vuetifyTheme.global.name.value = stored.value
  })

  function toggle() {
    stored.value = stored.value === 'dark' ? 'light' : 'dark'
  }

  return { isDark, toggle }
}
