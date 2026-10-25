/**
 * useLogStream —— 运行日志实时流(参考 Anime-Manager 的 /ws/system/logs 方案)
 *
 * - WS 地址 /ws/system/logs,鉴权 token 走 ?token=(浏览器握手无法带自定义 header)
 * - 断线指数退避重连 1s→15s;服务端 4401(鉴权失败)不重试
 * - 原始行环形缓冲(2000 条);连接建立时服务端先回填历史,再做实时转发
 */
import { computed, onScopeDispose, ref } from 'vue'

const AUTH_TOKEN_KEY = 'evp-auth-token'
const MAX_BUFFER_LINES = 2000
const MAX_RETRY_DELAY_MS = 15_000
const CLOSE_CODE_UNAUTHORIZED = 4401

export type LogStreamState = 'connecting' | 'live' | 'closed'

export function useLogStream() {
  const rawLines = ref<string[]>([])
  const streamState = ref<LogStreamState>('connecting')

  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let retryCount = 0
  let intentionalClose = false

  function buildWsUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = localStorage.getItem(AUTH_TOKEN_KEY) ?? ''
    return `${protocol}//${window.location.host}/ws/system/logs?token=${encodeURIComponent(token)}`
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    const delay = Math.min(1000 * 2 ** retryCount, MAX_RETRY_DELAY_MS)
    retryCount += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, delay)
  }

  function connect() {
    if (socket || reconnectTimer) return
    streamState.value = 'connecting'
    try {
      socket = new WebSocket(buildWsUrl())
    } catch {
      socket = null
      scheduleReconnect()
      return
    }

    socket.onmessage = (event) => {
      const message = String(event.data)
      if (!message) return
      rawLines.value.push(message)
      if (rawLines.value.length > MAX_BUFFER_LINES) {
        rawLines.value.splice(0, rawLines.value.length - MAX_BUFFER_LINES)
      }
    }

    socket.onopen = () => {
      retryCount = 0
      streamState.value = 'live'
    }

    socket.onclose = (event) => {
      socket = null
      streamState.value = 'closed'
      if (intentionalClose) return
      // 鉴权失败重试无意义,等待用户重新登录后再次进入本页
      if (event.code === CLOSE_CODE_UNAUTHORIZED) return
      scheduleReconnect()
    }

    socket.onerror = () => {
      // 重连交给 onclose 处理
    }
  }

  function disconnect() {
    intentionalClose = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    socket?.close()
    socket = null
    streamState.value = 'closed'
    intentionalClose = false
  }

  function clear() {
    rawLines.value = []
  }

  const isConnected = computed(() => streamState.value === 'live')

  onScopeDispose(disconnect)

  return {
    rawLines,
    streamState,
    isConnected,
    connect,
    disconnect,
    clear,
  }
}
