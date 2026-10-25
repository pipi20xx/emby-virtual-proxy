<template>
  <div class="evp-page">
    <SectionHeader title="运行日志" subtitle="proxy 与 admin 双进程实时日志,按天保留 7 天(config/logs/)">
      <template #actions>
        <v-chip v-if="streamState === 'live'" color="success" size="small" variant="tonal">
          <span class="log-live-dot" />
          实时接收中
        </v-chip>
        <v-chip v-else-if="streamState === 'connecting'" size="small" variant="tonal" class="mr-1">
          <v-progress-circular indeterminate size="12" width="2" class="mr-2" />
          正在连接…
        </v-chip>
        <v-chip v-else color="warning" size="small" variant="tonal">
          连接断开,自动重试中
        </v-chip>
      </template>
    </SectionHeader>

    <v-card>
      <div class="log-toolbar pa-3">
        <v-btn-toggle v-model="selectedLevel" mandatory divided density="compact" variant="tonal" class="log-level-toggle">
          <v-btn v-for="opt in levelOptions" :key="opt.value" :value="opt.value" :color="opt.color">
            {{ opt.title }}
          </v-btn>
        </v-btn-toggle>

        <v-text-field
          v-model="searchQuery"
          class="log-search"
          density="compact"
          variant="outlined"
          hide-details
          clearable
          prepend-inner-icon="mdi-magnify"
          placeholder="搜索日志..."
        />

        <v-btn icon variant="text" :color="isPaused ? 'warning' : 'success'" @click="togglePause">
          <v-icon>{{ isPaused ? 'mdi-play' : 'mdi-pause' }}</v-icon>
          <v-tooltip activator="parent" location="bottom">{{ isPaused ? '恢复实时滚动' : '暂停实时滚动' }}</v-tooltip>
        </v-btn>

        <v-btn icon variant="text" title="复制当前日志" @click="copyLogs">
          <v-icon>mdi-content-copy</v-icon>
        </v-btn>

        <v-btn icon variant="text" color="error" @click="clear">
          <v-icon>mdi-delete-outline</v-icon>
          <v-tooltip activator="parent" location="bottom">清空日志</v-tooltip>
        </v-btn>
      </div>

      <v-divider />

      <div ref="viewportRef" class="log-viewport" @scroll.passive="handleScroll">
        <div v-if="groupedLogs.length === 0" class="log-empty">
          <v-icon size="20">mdi-console-line</v-icon>
          <span>{{ rawLines.length === 0 ? '暂无日志' : '没有匹配的日志' }}</span>
        </div>

        <div v-else class="log-list">
          <div v-for="group in groupedLogs" :key="group.key" class="log-group">
            <div class="log-group__time">{{ group.time || '···' }}</div>
            <div class="log-group__panel">
              <div class="log-group__accent" :class="`level-${group.level.toLowerCase()}`" />
              <div class="log-group__lines">
                <div v-for="item in group.items" :key="item.id" class="log-line">
                  <span class="log-line__level" :class="`level-${item.level.toLowerCase()}`">{{ item.level }}</span>
                  <span class="log-line__msg">{{ item.message }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="pendingLogCount > 0 && !followTail" class="log-jump">
          <v-btn size="small" color="primary" variant="elevated" prepend-icon="mdi-arrow-down" @click="enableFollow('smooth')">
            跳到最新 ({{ pendingLogCount }})
          </v-btn>
        </div>
      </div>
    </v-card>
  </div>
</template>

<script setup lang="ts">
/**
 * LogsView —— 运行日志页面(参考 Anime-Manager LogTerminal 终端交互)
 * 数据来自 /ws/system/logs 实时流(useLogStream),此处只做展示、筛选与跟随滚动。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import SectionHeader from '@/components/common/SectionHeader.vue'
import { useLogStream } from '@/composables/useLogStream'
import { useNotification } from '@/composables/useNotification'

const MAX_DISPLAY_LINES = 1000 // 视图最多渲染的原始日志行数
const SCROLL_BOTTOM_THRESHOLD = 32 // 距底部多少像素内视为"贴着底部"

type LogEntry = { id: number; time: string; level: string; message: string }
type LogGroup = { key: string; time: string; level: string; items: LogEntry[] }

const { rawLines, streamState, connect, clear } = useLogStream()
const notify = useNotification()

const viewportRef = ref<HTMLElement>()

// --- 筛选与流控制状态 ---
const selectedLevel = ref('ALL')
const searchQuery = ref('')
const isPaused = ref(false)
const followTail = ref(true)
const pendingLogCount = ref(0)
const frozenLines = ref<string[]>([]) // 暂停时的显示快照

const levelOptions = [
  { title: '全部', value: 'ALL', color: 'primary' },
  { title: 'DEBUG', value: 'DEBUG', color: 'secondary' },
  { title: 'INFO', value: 'INFO', color: '#4ecdc4' },
  { title: 'WARNING', value: 'WARNING', color: '#FFB74D' },
  { title: 'ERROR', value: 'ERROR', color: '#EF5350' },
]

// --- 日志解析 ---
const LINE_PATTERN = /^(\d{2}:\d{2}:\d{2})\s*\|\s*([A-Za-z]+)\s*\|\s*([\s\S]*)$/

let logSequence = 0

function normalizeLevel(level: string): string {
  const upper = level.trim().toUpperCase()
  if (upper === 'WARN') return 'WARNING'
  if (upper === 'FATAL') return 'CRITICAL'
  return upper
}

/** 无结构前缀的行按关键字猜测级别 */
function sniffLevel(text: string): string {
  const upper = text.toUpperCase()
  if (upper.includes('CRITICAL')) return 'CRITICAL'
  if (upper.includes('ERROR')) return 'ERROR'
  if (upper.includes('WARNING')) return 'WARNING'
  if (upper.includes('DEBUG')) return 'DEBUG'
  return 'INFO'
}

/** 解析为展示条目;多行消息的续行(无 "时间 | 级别 |" 前缀)并入上一条 */
function parseEntries(raws: string[]): LogEntry[] {
  const entries: LogEntry[] = []
  for (const raw of raws) {
    for (const line of raw.split(/\r?\n/)) {
      const text = line.trimEnd()
      if (!text.trim()) continue

      const m = text.match(LINE_PATTERN)
      if (m) {
        entries.push({ id: ++logSequence, time: m[1], level: normalizeLevel(m[2]), message: m[3] })
      } else if (entries.length) {
        const prev = entries[entries.length - 1]
        prev.message += '\n' + text
      } else {
        entries.push({ id: ++logSequence, time: '', level: sniffLevel(text), message: text })
      }
    }
  }
  return entries
}

// 暂停时冻结快照,恢复后重新同步最新数据
const displayedLines = computed(() => {
  const source = isPaused.value ? frozenLines.value : rawLines.value
  return source.slice(-MAX_DISPLAY_LINES)
})

// --- 筛选 ---
const normalizedQuery = computed(() => searchQuery.value?.trim().toLowerCase() ?? '')

function matchesFilter(item: LogEntry): boolean {
  const levelOk =
    selectedLevel.value === 'ALL' ||
    item.level === selectedLevel.value ||
    (selectedLevel.value === 'ERROR' && item.level === 'CRITICAL')
  if (!levelOk) return false
  if (!normalizedQuery.value) return true
  return `${item.time} ${item.level} ${item.message}`.toLowerCase().includes(normalizedQuery.value)
}

// --- 分组:相邻且同秒同级别的条目合并 ---
const groupedLogs = computed<LogGroup[]>(() => {
  const groups: LogGroup[] = []
  for (const entry of parseEntries(displayedLines.value)) {
    if (!matchesFilter(entry)) continue
    const last = groups[groups.length - 1]
    if (last && last.time === entry.time && last.level === entry.level) {
      last.items.push(entry)
    } else {
      groups.push({ key: `g${entry.id}`, time: entry.time, level: entry.level, items: [entry] })
    }
  }
  return groups
})

const visibleEntryCount = computed(() => groupedLogs.value.reduce((n, g) => n + g.items.length, 0))

// --- 滚动跟随 ---
function isNearBottom(): boolean {
  const el = viewportRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_BOTTOM_THRESHOLD
}

function scrollToBottom(behavior: ScrollBehavior = 'auto') {
  viewportRef.value?.scrollTo({ top: viewportRef.value.scrollHeight, behavior })
}

function enableFollow(behavior: ScrollBehavior = 'auto') {
  followTail.value = true
  pendingLogCount.value = 0
  nextTick(() => scrollToBottom(behavior))
}

function handleScroll() {
  followTail.value = isNearBottom()
  if (followTail.value) pendingLogCount.value = 0
}

// 新日志到达:跟随则滚底,否则累计待查看条数
watch(visibleEntryCount, (cur, prev) => {
  if (prev === undefined || cur <= prev) return
  if (followTail.value) {
    nextTick(() => scrollToBottom())
  } else {
    pendingLogCount.value += cur - prev
  }
})

// 切换筛选后回到实时底部
watch([selectedLevel, searchQuery], () => enableFollow())

// --- 暂停 / 恢复 ---
function togglePause() {
  if (!isPaused.value) {
    frozenLines.value = [...rawLines.value]
    isPaused.value = true
  } else {
    isPaused.value = false
    frozenLines.value = []
    enableFollow()
  }
}

async function copyLogs() {
  const text = parseEntries(displayedLines.value)
    .filter(matchesFilter)
    .map((item) => `${item.time} ${item.level} ${item.message}`)
    .join('\n')
  if (!text) {
    notify.warning('没有可复制的日志')
    return
  }
  try {
    await navigator.clipboard.writeText(text)
    notify.success('日志已复制到剪贴板')
  } catch {
    notify.error('复制失败,浏览器可能未授权剪贴板')
  }
}

onMounted(connect)
</script>

<style scoped>
/* 实时状态小圆点 */
.log-live-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  margin-right: 6px;
  border-radius: 50%;
  background-color: rgb(var(--v-theme-success));
  box-shadow: 0 0 6px rgb(var(--v-theme-success));
  animation: log-live-pulse 2s ease-in-out infinite;
}
@keyframes log-live-pulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.35;
  }
}

/* 工具栏 */
.log-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
.log-level-toggle {
  flex: 0 0 auto;
  text-transform: none;
}
.log-search {
  flex: 1 1 160px;
  min-width: 120px;
  max-width: 320px;
}

/* 日志视口 */
.log-viewport {
  position: relative;
  height: calc(100vh - 260px);
  min-height: 360px;
  overflow-y: auto;
  padding: 8px 12px;
  background: rgba(var(--v-theme-on-surface), 0.03);
}

.log-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  min-height: 200px;
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
}

/* 分组:左侧时间列 + 右侧内容 */
.log-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.log-group {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  align-items: start;
  gap: 8px;
}
.log-group__time {
  padding-top: 6px;
  color: rgb(var(--v-theme-primary));
  font-family: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
  font-size: 0.78rem;
  font-weight: 600;
  white-space: nowrap;
}
.log-group__panel {
  display: flex;
  align-items: stretch;
  gap: 8px;
  padding: 2px 0 2px 8px;
  min-width: 0;
}

/* 级别侧条 */
.log-group__accent {
  flex: 0 0 4px;
  align-self: stretch;
  border-radius: 999px;
  background-color: rgba(var(--v-theme-on-surface), 0.24);
}
.log-group__accent.level-debug {
  background-color: rgb(var(--v-theme-secondary));
}
.log-group__accent.level-info {
  background-color: #4ecdc4;
}
.log-group__accent.level-warning {
  background-color: #ffb74d;
}
.log-group__accent.level-error,
.log-group__accent.level-critical {
  background-color: #ef5350;
}

.log-group__lines {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

/* 单行日志 */
.log-line {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-family: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
  font-size: 0.8rem;
  line-height: 1.6;
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
  min-width: 0;
}
.log-line__level {
  flex: 0 0 56px;
  font-weight: 700;
}
.log-line__level.level-debug {
  color: rgb(var(--v-theme-secondary));
}
.log-line__level.level-info {
  color: #4ecdc4;
}
.log-line__level.level-warning {
  color: #ffb74d;
}
.log-line__level.level-error,
.log-line__level.level-critical {
  color: #ef5350;
}
.log-line__msg {
  flex: 1;
  min-width: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

/* 跳到最新浮动按钮 */
.log-jump {
  position: sticky;
  bottom: 12px;
  display: flex;
  justify-content: flex-end;
  pointer-events: none;
  margin-top: 8px;
}
.log-jump .v-btn {
  pointer-events: auto;
}
</style>
