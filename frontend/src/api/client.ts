import axios from 'axios'

/** 后端返回的 detail 统一转成带状态码的错误,业务层直接展示 message 即可 */
export class ApiError extends Error {
  status?: number

  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

const client = axios.create({
  baseURL: '/api',
  timeout: 30_000,
})

const AUTH_TOKEN_KEY = 'evp-auth-token'

// 登录态:所有 /api 请求自动附带 Token
client.interceptors.request.use((config) => {
  const token = localStorage.getItem(AUTH_TOKEN_KEY)
  if (token) {
    config.headers['X-Auth-Token'] = token
  }
  return config
})

client.interceptors.response.use(
  (response) => response,
  (error) => {
    // 会话过期/未登录:清除本地 Token 并回到登录页。
    // 登录接口自身的 401(密码错误)不触发跳转,由调用方展示错误。
    const url = String(error?.config?.url ?? '')
    if (error?.response?.status === 401 && !url.includes('/auth/')) {
      localStorage.removeItem(AUTH_TOKEN_KEY)
      if (window.location.hash !== '#/login') {
        window.location.hash = '#/login'
      }
    }
    const detail = error?.response?.data?.detail ?? error?.response?.data ?? error?.message
    const message =
      typeof detail === 'string' && detail ? detail : `请求失败${error?.response?.status ? ` (HTTP ${error.response.status})` : ''}`
    return Promise.reject(new ApiError(message, error?.response?.status))
  },
)

export default client
