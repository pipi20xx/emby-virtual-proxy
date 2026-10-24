<template>
  <DefaultLayout />
</template>

<script setup lang="ts">
import { onMounted } from 'vue'

import DefaultLayout from '@/layouts/DefaultLayout.vue'
import { useConfigStore } from '@/stores/useConfigStore'
import { useEmbyDataStore } from '@/stores/useEmbyDataStore'

const configStore = useConfigStore()
const embyStore = useEmbyDataStore()

onMounted(async () => {
  // 配置与 Emby 数据并行加载;Emby 不可达时页面仍可打开
  await configStore.load()
  await embyStore.load()
})
</script>
