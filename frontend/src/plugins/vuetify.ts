import { createVuetify } from 'vuetify'
import { aliases, mdi } from 'vuetify/iconsets/mdi'

// 主题色板与 Anime-Manager 保持一致,两个项目观感统一
const sharedColors = {
  primary: '#8D51F9',
  'primary-darken-1': '#7C3AED',
  secondary: '#6b7280',
  accent: '#4ecdc4',
  error: '#ef4444',
  info: '#1565C0',
  success: '#1B8134',
  warning: '#E65100',
}

const lightTheme = {
  dark: false,
  colors: {
    background: '#FFFFFF',
    surface: '#FFFFFF',
    'surface-variant': '#F5F5F5',
    'on-surface-variant': '#64748B',
    ...sharedColors,
  },
}

const darkTheme = {
  dark: true,
  colors: {
    background: '#121212',
    surface: '#1E1E1E',
    'surface-variant': '#2C2C2C',
    'on-surface-variant': '#BFC2CE',
    ...sharedColors,
  },
}

function readStoredTheme(): 'light' | 'dark' {
  try {
    const stored = localStorage.getItem('evp-theme')
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // localStorage 不可用时忽略
  }
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export default createVuetify({
  icons: {
    defaultSet: 'mdi',
    aliases,
    sets: { mdi },
  },
  theme: {
    defaultTheme: readStoredTheme(),
    themes: {
      light: lightTheme,
      dark: darkTheme,
    },
  },
  defaults: {
    VBtn: { variant: 'tonal' },
    VTextField: { variant: 'outlined', density: 'compact', hideDetails: 'auto' },
    VSelect: { variant: 'outlined', density: 'compact', hideDetails: 'auto' },
    VAutocomplete: { variant: 'outlined', density: 'compact', hideDetails: 'auto' },
    VCombobox: { variant: 'outlined', density: 'compact', hideDetails: 'auto' },
    VTextarea: { variant: 'outlined', density: 'compact', hideDetails: 'auto' },
    VSwitch: { density: 'compact', hideDetails: true },
    VChip: { label: true, size: 'small' },
  },
})
