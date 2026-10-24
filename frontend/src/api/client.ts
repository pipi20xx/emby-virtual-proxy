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

client.interceptors.response.use(
  (response) => response,
  (error) => {
    const detail = error?.response?.data?.detail ?? error?.response?.data ?? error?.message
    const message =
      typeof detail === 'string' && detail ? detail : `请求失败${error?.response?.status ? ` (HTTP ${error.response.status})` : ''}`
    return Promise.reject(new ApiError(message, error?.response?.status))
  },
)

export default client
