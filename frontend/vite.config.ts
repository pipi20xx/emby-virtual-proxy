import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'
import AutoImport from 'unplugin-auto-import/vite'

// 开发代理目标:NAS 上管理后台容器端口(8011 -> 容器 8001)
// 可用环境变量 EVP_DEV_PROXY 覆盖
const DEV_PROXY_TARGET = process.env.EVP_DEV_PROXY ?? 'http://192.168.50.252:8011'

export default defineConfig({
  plugins: [
    vue(),
    vuetify({ autoImport: true }),
    AutoImport({
      imports: ['vue', 'vue-router', 'pinia', '@vueuse/core'],
      dts: 'src/auto-imports.d.ts',
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': { target: DEV_PROXY_TARGET, changeOrigin: true },
      '/covers': { target: DEV_PROXY_TARGET, changeOrigin: true },
    },
  },
  build: {
    chunkSizeWarningLimit: 1600,
  },
})
