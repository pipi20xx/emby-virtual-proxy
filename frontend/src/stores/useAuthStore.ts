import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import * as authApi from '@/api/auth'

const AUTH_TOKEN_KEY = 'evp-auth-token'

/** 管理面板登录态:Token 持久化在 localStorage,过期后由 401 拦截器自动清理 */
export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem(AUTH_TOKEN_KEY))
  /** null = 尚未向后台确认;true = 已设置密码,需要登录 */
  const required = ref<boolean | null>(null)

  const authRequired = computed(() => required.value === true)
  const canEnter = computed(() => required.value !== true || Boolean(token.value))

  /** 向后台确认是否启用了密码(整个会话只确认一次) */
  async function ensureChecked(): Promise<void> {
    if (required.value !== null) return
    try {
      const status = await authApi.getStatus()
      required.value = status.required
      if (!status.required) return
      // 启用了密码:本地可能有未过期 Token,静默验证一次
      const recheck = await authApi.getStatus()
      if (!recheck.authenticated) {
        token.value = null
        localStorage.removeItem(AUTH_TOKEN_KEY)
      }
    } catch {
      // 后端不可达:放行到页面,由各页面自己的错误状态展示
      required.value = false
    }
  }

  async function login(password: string): Promise<void> {
    const result = await authApi.login(password)
    required.value = result.required
    if (result.token) {
      token.value = result.token
      localStorage.setItem(AUTH_TOKEN_KEY, result.token)
    }
  }

  function setToken(value: string | null) {
    token.value = value
    if (value) {
      localStorage.setItem(AUTH_TOKEN_KEY, value)
    } else {
      localStorage.removeItem(AUTH_TOKEN_KEY)
    }
  }

  function logout(): void {
    authApi.logout().catch(() => {})
    setToken(null)
  }

  /** Token 被拦截器判定失效后调用,避免死循环重定向 */
  function invalidate() {
    token.value = null
  }

  return { token, required, authRequired, canEnter, ensureChecked, login, setToken, logout, invalidate }
})
