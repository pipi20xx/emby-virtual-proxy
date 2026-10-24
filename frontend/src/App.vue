<template>
  <LoginView v-if="isLoginPage" />
  <DefaultLayout v-else />
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'

import DefaultLayout from '@/layouts/DefaultLayout.vue'
import LoginView from '@/views/LoginView.vue'
import { useAuthStore } from '@/stores/useAuthStore'
import { useConfigStore } from '@/stores/useConfigStore'
import { useEmbyDataStore } from '@/stores/useEmbyDataStore'

const route = useRoute()
const isLoginPage = computed(() => route.name === 'login')

const authStore = useAuthStore()
const configStore = useConfigStore()
const embyStore = useEmbyDataStore()

// 进入主界面时才拉取数据;登录页/未认证时不请求(401 由拦截器带回登录页)
watch(
  isLoginPage,
  async (onLogin) => {
    if (onLogin) return
    await authStore.ensureChecked().catch(() => undefined)
    if (!authStore.canEnter) return
    await configStore.load()
    await embyStore.load()
  },
  { immediate: true },
)
</script>
