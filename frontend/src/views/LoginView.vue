<template>
  <v-app>
    <v-main class="login-page">
      <v-btn
        class="theme-toggle"
        :icon="isDark ? 'mdi-weather-night' : 'mdi-weather-sunny'"
        variant="text"
        aria-label="切换主题"
        @click="toggle"
      />

      <v-card class="login-card elevation-8">
        <div class="text-center mb-6">
          <v-icon icon="mdi-library-shelves" size="40" color="primary" class="mb-3" />
          <div class="text-h5 font-weight-bold">
            Emby Virtual Proxy
            <span class="app-version text-caption text-medium-emphasis">v{{ appVersion }}</span>
          </div>
          <div class="text-body-2 text-medium-emphasis mt-1">管理面板</div>
        </div>

        <v-form @submit.prevent="submit">
          <SecretField
            v-model="password"
            label="管理密码"
            placeholder="请输入管理密码"
            :rules="[rules.required]"
          />
          <v-btn
            block
            type="submit"
            color="primary"
            variant="flat"
            size="large"
            class="mt-2"
            :loading="loading"
          >
            登 录
          </v-btn>
        </v-form>

        <div class="text-caption text-medium-emphasis text-center mt-6">
          密码可在「系统设置 → 访问安全」中设置;留空时无需登录
        </div>
      </v-card>
    </v-main>
  </v-app>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import SecretField from '@/components/common/SecretField.vue'
import { useThemeControl } from '@/composables/useTheme'
import { useNotification } from '@/composables/useNotification'
import { useAuthStore } from '@/stores/useAuthStore'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const notify = useNotification()
const { isDark, toggle } = useThemeControl()

const password = ref('')
const loading = ref(false)

const appVersion = __APP_VERSION__

const rules = {
  required: (value: string) => Boolean(value && String(value).trim()) || '请输入管理密码',
}

async function submit() {
  if (!password.value.trim()) return
  loading.value = true
  try {
    await authStore.login(password.value)
    const redirect = (route.query.redirect as string) || ''
    await router.replace(redirect.startsWith('/') ? redirect : { name: 'libraries' })
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '登录失败')
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await authStore.ensureChecked().catch(() => undefined)
  // 未设置管理密码时,登录页没有意义,直接进入面板
  if (!authStore.authRequired) {
    await router.replace({ name: 'libraries' })
  }
})
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    radial-gradient(ellipse at 20% 0%, rgba(var(--v-theme-primary), 0.14), transparent 55%),
    radial-gradient(ellipse at 85% 100%, rgba(var(--v-theme-primary), 0.1), transparent 50%),
    rgb(var(--v-theme-background));
}

.theme-toggle {
  position: absolute;
  top: 16px;
  right: 16px;
}

.login-card {
  width: 100%;
  max-width: 420px;
  padding: 40px 36px 28px;
  border-radius: var(--evp-login-radius);
}

/* 标题右侧的版本号小字 */
.app-version {
  font-weight: 400;
  white-space: nowrap;
}
</style>
