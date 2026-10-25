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

        <v-btn icon variant="text" color="error" @click="clearView">
          <v-icon>mdi-delete-outline</v-icon>
          <v-tooltip activator="parent" location="bottom">清空日志</v-tooltip>
        </v-btn>
      </div>

      <v-divider />

      <div ref="viewportRef" class="log-viewport" @scroll.passive="handleScroll">
        <div v-if="loadingHistory" class="log-loading">
          <v-progress-circular indeterminate size="16" width="2" />
          <span>加载更早日志…</span>
        </div>

        <div v-if="groupedLogs.length === 0" class="log-empty">
          <v-icon size="20">mdi-console-line</v-icon>
          <span>{{ historyLines.length === 0 && rawLines.length === 0 ? '暂无日志' : '没有匹配的日志' }}</span>
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
 *
 * 数据分两路:
 *   - 历史:REST /system/logs/history 分页(首屏只取最新 100 行,向上滚动加载更早,来源为按天日志文件);
 *   - 实时:WS /ws/system/logs(backfill=0,只推连接之后的新行,避免与 REST 首屏重叠)。
 * 此处负责展示、筛选、跟随滚动与向上翻页的滚动位置补偿。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'

import SectionHeader from '@/components/common/SectionHeader.vue'
import { getLogHistory, getLogTail } from '@/api/logs'
import { useLogStream } from '@/composables/useLogStream'
import { useNotification } from '@/composables/useNotification'

const DEFAULT_TAIL = 100 // 首屏只拉最新 100 行,避免刷屏
const PAGE_SIZE = 100 // 每次向上翻页加载的行数
const LOAD_MORE_THRESHOLD = 80 // 距顶部多少像素内触发加载更早
const MAX_LOADED_HISTORY = 3000 // 单次会话最多加载的历史行数,防止无限翻页拖垮渲染
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

// --- 历史分页状态 ---
const historyLines = ref<string[]>([]) // REST 加载的历史(含首屏,时间升序)
const nextStartIndex = ref<number | null>(null) // 下一页结束游标;null = 已翻到最早
const loadingHistory = ref(false)
const prependingHistory = ref(false) // 前插历史期间,屏蔽"新日志到达"的滚动/计数逻辑

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

// 暂停时冻结快照,恢复后重新同步最新数据;正常态 = 已加载历史 + 实时增量
const displayedLines = computed(() => {
  const source = isPaused.value ? frozenLines.value : [...historyLines.value, ...rawLines.value]
  return source
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

// 翻页只由"用户主动向上滚动"触发:程序化滚动(跟随底部/前插补偿)方向向下,
// 不会误触发;否则加载->补偿滚动->再触发会形成无人操作的连环加载
let lastScrollTop = 0
let zeroVisibleLoads = 0 // 本手势内连续"无可见新增"的翻页次数(内容全被筛选滤掉时高度不变)
const MAX_ZERO_VISIBLE_LOADS = 3

function handleScroll() {
  const el = viewportRef.value
  if (!el) return
  const current = el.scrollTop
  const scrollingUp = current < lastScrollTop
  lastScrollTop = current

  followTail.value = isNearBottom()
  if (followTail.value) pendingLogCount.value = 0

  // !followTail:排除内容变矮后浏览器钳制 scrollTop 造成的"假向上"滚动
  if (scrollingUp && !followTail.value && current <= LOAD_MORE_THRESHOLD) {
    zeroVisibleLoads = 0
    void loadOlder()
  }
}

// 新日志到达:跟随则滚底,否则累计待查看条数(前插历史不算新日志)
watch(visibleEntryCount, (cur, prev) => {
  if (prev === undefined || cur <= prev) return
  if (prependingHistory.value) return
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
    frozenLines.value = [...historyLines.value, ...rawLines.value]
    isPaused.value = true
  } else {
    isPaused.value = false
    frozenLines.value = []
    enableFollow()
  }
}

// --- 历史分页 ---
async function loadOlder() {
  if (loadingHistory.value || nextStartIndex.value === null) return
  if (historyLines.value.length >= MAX_LOADED_HISTORY) {
    nextStartIndex.value = null
    notify.info('本次会话加载的历史日志已达上限,刷新页面可重新从头浏览')
    return
  }

  loadingHistory.value = true
  const el = viewportRef.value
  const anchorHeight = el?.scrollHeight ?? 0
  prependingHistory.value = true
  let addedVisible = false
  try {
    const res = await getLogHistory(nextStartIndex.value, PAGE_SIZE)
    if (res.lines.length === 0) {
      nextStartIndex.value = null // 已翻到最早(或索引越界),终止翻页
      return
    }
    historyLines.value = [...res.lines, ...historyLines.value]
    nextStartIndex.value = res.next_start_index
    await nextTick()
    // 前插内容撑高容器后,按高度差补偿 scrollTop,视觉位置不跳动
    if (el) {
      const added = el.scrollHeight - anchorHeight
      if (added > 0) {
        el.scrollTop += added
        addedVisible = true
      }
    }
  } catch {
    notify.error('加载更早日志失败')
    return
  } finally {
    loadingHistory.value = false
    await nextTick()
    prependingHistory.value = false
  }

  // 后续补页只发生在两种受控场景,避免无人操作时的连环请求:
  //   1. 内容不足一屏(无法产生滚动事件)时继续补,直到可滚动;
  //   2. 整页内容都被筛选滤掉(高度不变、scrollTop 出不了顶部区)时有限补几页,
  //      超限后停下提示,等用户再次主动向上滚动才继续。
  if (nextStartIndex.value === null) return
  if (addedVisible) {
    zeroVisibleLoads = 0
    const vp = viewportRef.value
    if (vp && vp.scrollHeight <= vp.clientHeight) void loadOlder()
  } else if (zeroVisibleLoads < MAX_ZERO_VISIBLE_LOADS) {
    zeroVisibleLoads += 1
    void loadOlder()
  } else {
    zeroVisibleLoads = 0
    notify.info('连续多页日志均被当前筛选条件隐藏,如需查看更早日志请调整筛选后重试')
  }
}

/** 清空当前视图;保留翻页游标,向上滚动仍可重新拉取历史 */
function clearView() {
  clear()
  historyLines.value = []
}

// 首屏:先 REST 拉最新 100 行落定,再连 WS 只收实时增量(时间窗极小,期间的日志可能缺失但不会重复)
async function initialize() {
  try {
    const res = await getLogTail(DEFAULT_TAIL, PAGE_SIZE)
    historyLines.value = res.lines
    nextStartIndex.value = res.next_start_index
  } catch {
    notify.error('加载历史日志失败')
  }
  connect({ backfill: false })
  await nextTick()
  scrollToBottom()
  // 内容不足一屏时主动补页,否则滚动条都不会出现,永远无法触发向上加载
  const el = viewportRef.value
  if (el && el.scrollHeight <= el.clientHeight && nextStartIndex.value !== null) void loadOlder()
}

onMounted(initialize)

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
  /* 前插历史时由代码自行补偿 scrollTop,禁用浏览器滚动锚定避免双重补偿 */
  overflow-anchor: none;
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

/* 顶部加载更早日志的悬浮指示(绝对定位,不影响滚动高度计算) */
.log-loading {
  position: absolute;
  top: 8px;
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  border-radius: 999px;
  background: rgba(var(--v-theme-surface), 0.92);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.18);
  font-size: 0.78rem;
  color: rgba(var(--v-theme-on-surface), var(--v-medium-emphasis-opacity));
  z-index: 2;
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
