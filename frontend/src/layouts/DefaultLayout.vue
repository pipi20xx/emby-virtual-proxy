<template>
  <v-app>
    <v-app-bar flat border>
      <v-app-bar-nav-icon
        v-if="display.mdAndDown.value"
        icon="mdi-menu"
        @click="drawer = !drawer"
      />
      <v-app-bar-title class="font-weight-bold">Emby Virtual Proxy</v-app-bar-title>

      <template #append>
        <v-chip v-if="embyStore.loadState === 'loading'" size="small" variant="tonal" class="mr-1">
          <v-progress-circular indeterminate size="12" width="2" class="mr-1" />
          正在连接 Emby…
        </v-chip>
        <v-chip v-else-if="embyStore.loadState === 'ok'" color="success" size="small" variant="tonal" class="mr-1">
          <v-icon icon="mdi-check-circle" size="small" start />
          Emby 已连接
        </v-chip>
        <v-chip v-else-if="embyStore.loadState === 'error'" color="error" size="small" variant="tonal" class="mr-1">
          <v-icon icon="mdi-alert-circle" size="small" start />
          Emby 连接失败
        </v-chip>

        <v-btn
          v-if="authStore.authRequired"
          icon="mdi-logout"
          variant="text"
          title="退出登录"
          @click="signOut"
        />
        <v-btn
          :icon="isDark ? 'mdi-weather-night' : 'mdi-weather-sunny'"
          variant="text"
          aria-label="切换主题"
          @click="toggle"
        />
      </template>
    </v-app-bar>

    <v-navigation-drawer v-model="drawer" :permanent="display.mdAndUp.value">
      <v-list nav>
        <v-list-item
          v-for="item in navItems"
          :key="item.routeName"
          :to="{ name: item.routeName }"
          :prepend-icon="item.icon"
          :title="item.title"
        />
      </v-list>

      <template #append>
        <div class="pa-4 text-caption text-medium-emphasis text-center">
          v{{ appVersion }}
        </div>
      </template>
    </v-navigation-drawer>

    <v-main>
      <router-view v-slot="{ Component }">
        <transition name="fade-slide" mode="out-in">
          <component :is="Component" />
        </transition>
      </router-view>
    </v-main>

    <AppSnackbar />
    <ConfirmDialog />
  </v-app>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useDisplay } from 'vuetify'
import { useRouter } from 'vue-router'

import AppSnackbar from '@/components/common/AppSnackbar.vue'
import ConfirmDialog from '@/components/common/ConfirmDialog.vue'
import { useThemeControl } from '@/composables/useTheme'
import { useAuthStore } from '@/stores/useAuthStore'
import { useEmbyDataStore } from '@/stores/useEmbyDataStore'

const display = useDisplay()
const router = useRouter()
const embyStore = useEmbyDataStore()
const authStore = useAuthStore()
const { isDark, toggle } = useThemeControl()

const appVersion = __APP_VERSION__

const drawer = ref(display.mdAndUp.value)

const navItems = [
  { routeName: 'libraries', icon: 'mdi-library-shelves', title: '虚拟媒体库' },
  { routeName: 'filters', icon: 'mdi-filter-cog-outline', title: '高级筛选器' },
  { routeName: 'layout', icon: 'mdi-view-dashboard-edit-outline', title: '主页布局' },
  { routeName: 'logs', icon: 'mdi-console-line', title: '运行日志' },
  { routeName: 'settings', icon: 'mdi-cog-outline', title: '系统设置' },
]

function signOut() {
  authStore.logout()
  router.push({ name: 'login' })
}
</script>
