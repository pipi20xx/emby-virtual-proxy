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

// 重播防护阈值:实时流严格时间有序,某行时间比已见最新时间倒退超过 2 分钟,
// 只可能是服务端重播的历史(进程重启/尾随任务重开等),丢弃直到追上当前时间
const REPLAY_THRESHOLD_SECS = 120
const MIDNIGHT_WRAP_NEWEST = 23 * 3600 + 58 * 60 // 最新时间已过 23:58
const MIDNIGHT_WRAP_NEW = 2 * 60 // 新行时间在 00:02 之前,视为跨天而非重播

export type LogStreamState = 'connecting' | 'live' | 'closed'

interface ConnectOptions {
  /** false 时不让服务端回填内存历史(前端自行用 /system/logs/history 拉首屏,避免两份历史重叠) */
  backfill?: boolean
}

export function useLogStream() {
  const rawLines = ref<string[]>([])
  const streamState = ref<LogStreamState>('connecting')

  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let retryCount = 0
  let intentionalClose = false

  // 重播防护状态(每次连接建立时重置)
  let newestSecs: number | null = null
  let replayDropped = 0
  let lastLineDropped = false

  function buildWsUrl(): string {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = localStorage.getItem(AUTH_TOKEN_KEY) ?? ''
    const backfillParam = connectOptions.backfill === false ? '&backfill=0' : ''
    return `${protocol}//${window.location.host}/ws/system/logs?token=${encodeURIComponent(token)}${backfillParam}`
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    const delay = Math.min(1000 * 2 ** retryCount, MAX_RETRY_DELAY_MS)
    retryCount += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect(connectOptions)
    }, delay)
  }

  // 记住首次连接的选项,断线重连沿用(如 backfill=false)
  let connectOptions: ConnectOptions = {}

  function connect(options: ConnectOptions = {}) {
    if (socket || reconnectTimer) return
    connectOptions = options
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

      // 重播防护:比已见最新时间倒退超过阈值的历史行直接丢弃(含其无时间前缀的续行)
      const timeMatch = message.match(/^(\d{2}):(\d{2}):(\d{2})/)
      if (timeMatch) {
        const secs = Number(timeMatch[1]) * 3600 + Number(timeMatch[2]) * 60 + Number(timeMatch[3])
        if (newestSecs !== null) {
          const behind = newestSecs - secs
          const midnightWrap = newestSecs >= MIDNIGHT_WRAP_NEWEST && secs <= MIDNIGHT_WRAP_NEW
          if (behind > REPLAY_THRESHOLD_SECS && !midnightWrap) {
            replayDropped += 1
            lastLineDropped = true
            if (replayDropped === 1 || replayDropped % 1000 === 0) {
              console.warn(`[useLogStream] 检测到服务端日志重播,已丢弃 ${replayDropped} 条历史行`)
            }
            return
          }
        }
        newestSecs = secs
        lastLineDropped = false
      } else if (lastLineDropped) {
        return
      }

      rawLines.value.push(message)
      if (rawLines.value.length > MAX_BUFFER_LINES) {
        rawLines.value.splice(0, rawLines.value.length - MAX_BUFFER_LINES)
      }
    }

    socket.onopen = () => {
      retryCount = 0
      streamState.value = 'live'
      newestSecs = null
      replayDropped = 0
      lastLineDropped = false
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
