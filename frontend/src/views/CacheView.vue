<template>
  <div class="evp-page">
    <SectionHeader
      title="缓存状态"
      subtitle="全库数据源 / 各库派生 / 用户状态层 / 图片缓存的实时快照,以及下一次自动刷新的时间"
    >
      <template #actions>
        <v-switch
          v-model="autoRefresh"
          density="compact"
          hide-details
          color="primary"
          label="每 10 秒自动刷新"
          class="mr-2"
        />
        <v-chip v-if="lastUpdated" size="small" variant="tonal" class="mr-2">
          更新于 {{ lastUpdated }}
        </v-chip>
        <v-btn :loading="loading" variant="tonal" prepend-icon="mdi-refresh" @click="load">刷新</v-btn>
      </template>
    </SectionHeader>

    <v-alert v-if="stats && !stats.available" type="warning" variant="tonal" class="mb-4">
      无法读取缓存状态:{{ stats.reason || '未知原因' }}
      <div class="text-caption mt-1">检查代理核心(8999 端口)是否在运行,以及 admin 的 PROXY_CORE_URL 是否配置正确。</div>
    </v-alert>

    <!-- 概览 -->
    <v-row dense class="mb-1">
      <v-col v-for="tile in tiles" :key="tile.label" cols="12" sm="6" md="3">
        <v-card class="fill-height">
          <v-card-text class="d-flex flex-column ga-1">
            <div class="text-caption text-medium-emphasis">{{ tile.label }}</div>
            <div class="text-h5 font-weight-bold">{{ tile.value }}</div>
            <div class="text-caption text-medium-emphasis">{{ tile.hint }}</div>
            <v-progress-linear
              v-if="tile.progress !== undefined"
              :model-value="tile.progress"
              :color="tile.progressColor || 'primary'"
              height="6"
              rounded
              class="mt-1"
            />
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <!-- 缓存清单:系统里到底有哪些缓存 -->
    <v-card class="mb-4">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-layers-outline" size="small" /> 缓存清单
        <v-spacer />
        <span class="text-caption text-medium-emphasis">自下而上:越往下越共享</span>
      </v-card-title>
      <v-table density="compact">
        <thead>
          <tr>
            <th>层</th>
            <th>存什么</th>
            <th>位置</th>
            <th>范围</th>
            <th>时效</th>
            <th class="text-right">当前</th>
            <th class="text-right">命中 / 未命中</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="layer in layers" :key="layer.name">
            <td class="text-high-emphasis text-no-wrap">{{ layer.name }}</td>
            <td class="text-caption">{{ layer.what }}</td>
            <td class="text-caption text-no-wrap">{{ layer.where }}</td>
            <td class="text-caption">{{ layer.scope }}</td>
            <td class="text-caption">{{ layer.ttl }}</td>
            <td class="text-right text-caption text-no-wrap">{{ layer.size }}</td>
            <td class="text-right text-caption text-no-wrap">{{ layer.hit }}</td>
          </tr>
        </tbody>
      </v-table>
      <v-card-text class="text-caption text-medium-emphasis">
        列表浏览优先命中 L3/L1(直出),既不请求 Emby 也不会重新解析;
        首页「最新」、首页分区、视图与真实库列表走 API 响应缓存(若已开启);
        图片按 URL 里的 tag 永久缓存,连客户端都会长期复用。
      </v-card-text>
    </v-card>


    <v-card class="mb-4">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-calendar-clock" size="small" /> 下一次自动刷新
      </v-card-title>
      <v-card-text class="d-flex flex-column ga-3">
        <div v-for="job in jobs" :key="job.id" class="d-flex align-center flex-wrap ga-3">
          <v-icon :icon="job.icon" size="small" class="text-medium-emphasis" />
          <div class="flex-grow-1">
            <div class="text-body-2 text-high-emphasis">{{ job.title }}</div>
            <div class="text-caption text-medium-emphasis">{{ job.hint }}</div>
          </div>
          <v-chip v-if="job.nextRun" color="primary" variant="tonal" size="small">
            {{ job.nextRun }}
          </v-chip>
          <v-chip v-else variant="tonal" size="small">未启用</v-chip>
        </div>

        <v-divider />

        <div class="text-caption text-medium-emphasis">
          元数据软过期 {{ formatDuration(stats?.ttls?.meta_soft) }}(过期后照常返回并后台重建)、
          硬过期 {{ formatDuration(stats?.ttls?.meta_hard) }}(过期后回退实时查询);
          用户状态层 {{ formatDuration(stats?.ttls?.overlay_soft) }} 刷新一次,
          合并结果 {{ formatDuration(stats?.ttls?.merge) }} 过期。
        </div>
      </v-card-text>
    </v-card>

    <!-- 各库派生结果 -->
    <v-card class="mb-4">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-layers-triple-outline" size="small" /> 各库派生结果
        <v-spacer />
        <span class="text-caption text-medium-emphasis">共 {{ stats?.libraries?.length ?? 0 }} 个虚拟库</span>
      </v-card-title>
      <v-table density="comfortable" hover>
        <thead>
          <tr>
            <th>库</th>
            <th>资源类型</th>
            <th>筛选器</th>
            <th class="text-right">条目</th>
            <th>缓存年龄</th>
            <th>共享数据源</th>
            <th class="text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="lib in stats?.libraries ?? []" :key="lib.id">
            <td class="text-high-emphasis">{{ lib.name }}</td>
            <td><v-chip size="x-small" variant="tonal">{{ lib.resource_type }}</v-chip></td>
            <td class="text-caption">
              <template v-if="lib.filter_name">
                {{ lib.filter_name }}
                <v-chip
                  size="x-small"
                  :color="lib.filter_mode === 'local' ? 'success' : 'warning'"
                  variant="tonal"
                  class="ml-1"
                >
                  {{ lib.filter_mode === 'local' ? '本地全量' : 'Emby 原生' }}
                </v-chip>
              </template>
              <span v-else class="text-medium-emphasis">—</span>
            </td>
            <td class="text-right">{{ lib.items }}</td>
            <td class="text-caption">{{ formatAge(lib.age_seconds) }}</td>
            <td>
              <v-icon
                :icon="lib.local_capable ? 'mdi-check-circle' : 'mdi-alert-circle-outline'"
                :color="lib.local_capable ? 'success' : 'warning'"
                size="small"
              />
              <v-tooltip activator="parent" location="top">
                {{
                  lib.local_capable
                    ? '从共享全库数据源本地派生,不再单独向 Emby 拉取'
                    : '该库仍逐库向 Emby 拉取(通常是 Emby 原生模式筛选器)'
                }}
              </v-tooltip>
            </td>
            <td class="text-right">
              <v-btn
                size="small"
                variant="text"
                :loading="refreshingId === lib.id"
                @click="refreshLibrary(lib.id, lib.name)"
              >
                立即刷新
              </v-btn>
            </td>
          </tr>
          <tr v-if="(stats?.libraries?.length ?? 0) === 0">
            <td colspan="7" class="text-center text-medium-emphasis py-6">还没有虚拟库</td>
          </tr>
        </tbody>
      </v-table>
    </v-card>

    <!-- 用户状态层 -->
    <v-card class="mb-4">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-account-multiple-outline" size="small" /> 用户状态层
        <v-spacer />
        <span class="text-caption text-medium-emphasis">
          每账号一份,只存自己的已看/进度;{{ formatDuration(stats?.ttls?.overlay_soft) }} 过期
        </span>
      </v-card-title>
      <v-card-text v-if="(stats?.overlays?.length ?? 0) > 0" class="d-flex flex-column ga-2">
        <div v-for="ov in stats?.overlays ?? []" :key="`${ov.user_id}-${ov.source}`" class="d-flex align-center ga-3">
          <v-icon icon="mdi-account" size="small" class="text-medium-emphasis" />
          <div class="flex-grow-1 text-body-2">
            <span class="text-high-emphasis">{{ shortUser(ov.user_id) }}</span>
            <span class="text-medium-emphasis ml-2">数据源 {{ ov.source }}</span>
          </div>
          <span class="text-caption text-medium-emphasis">{{ ov.entries }} 条</span>
          <span class="text-caption">{{ formatAge(ov.age_seconds) }}</span>
          <v-progress-linear
            :model-value="overlayFreshness(ov.age_seconds)"
            :color="overlayFreshness(ov.age_seconds) < 40 ? 'warning' : 'success'"
            height="6"
            rounded
            class="overlay-bar"
          />
        </div>
      </v-card-text>
      <v-card-text v-else class="text-medium-emphasis">
        暂无用户状态层:首次有人浏览虚拟库时会自动构建。
      </v-card-text>
    </v-card>

    <!-- 图片缓存 -->
    <v-card class="mb-4">
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-image-multiple-outline" size="small" /> 图片缓存
        <v-spacer />
        <v-btn
          size="small"
          variant="text"
          color="error"
          prepend-icon="mdi-delete-outline"
          :loading="clearingImages"
          @click="onClearImages"
        >
          清空
        </v-btn>
      </v-card-title>
      <v-card-text class="d-flex flex-column ga-2">
        <div class="d-flex align-center flex-wrap ga-4">
          <div>
            <div class="text-caption text-medium-emphasis">已缓存文件</div>
            <div class="text-h6">{{ stats?.images?.files ?? 0 }}</div>
          </div>
          <div>
            <div class="text-caption text-medium-emphasis">占用</div>
            <div class="text-h6">{{ formatBytes(stats?.images?.bytes) }}</div>
          </div>
          <div>
            <div class="text-caption text-medium-emphasis">上限</div>
            <div class="text-h6">{{ imageCapLabel }}</div>
          </div>
          <div>
            <div class="text-caption text-medium-emphasis">命中 / 回源</div>
            <div class="text-h6">{{ counter('image_hit') }} / {{ counter('image_miss') }}</div>
          </div>
        </div>
        <v-progress-linear
          v-if="imageUsagePercent !== undefined"
          :model-value="imageUsagePercent"
          :color="imageUsagePercent > 85 ? 'error' : 'primary'"
          height="8"
          rounded
        />
        <div class="text-caption text-medium-emphasis">
          说明:图片 URL 自带内容指纹,我们回的是 <code>immutable</code> —— 浏览器会自己长期复用,
          所以刷新页面通常<strong>不会再请求图片</strong>,<code>image_hit</code> 长期为 0 属正常。
          真正有意义的指标是 <code>image_miss</code>(= 实际抓取并落盘的张数);
          想看到 hit,可用 curl 连打同一张图,或换个浏览器 / 清掉浏览器缓存。
        </div>
      </v-card-text>
    </v-card>

    <!-- 维护操作 -->
    <v-card class="mb-4 border-danger">
      <v-card-title class="d-flex align-center ga-2 text-error">
        <v-icon icon="mdi-alert-octagon-outline" size="small" /> 维护操作
      </v-card-title>
      <v-card-text class="d-flex flex-wrap ga-3">
        <v-btn
          color="error"
          variant="tonal"
          prepend-icon="mdi-database-remove-outline"
          :loading="clearingVlib"
          @click="onClearVlib"
        >
          清空虚拟库项目缓存
        </v-btn>
        <v-btn
          color="error"
          variant="tonal"
          prepend-icon="mdi-image-multiple-outline"
          :loading="clearingCovers"
          @click="onClearCovers"
        >
          清空所有本地封面
        </v-btn>
      </v-card-text>
      <v-card-text class="text-caption text-medium-emphasis pt-0">
        <div>
          <strong>清空项目缓存</strong>:删除 SQLite 里的全库数据源、各库派生结果、用户状态层、
          TMDB 索引与合并结果,同时清空代理内存缓存。图片缓存与封面不受影响;
          下次浏览或预热时会自动重建(会重新向 Emby 拉一次全库)。
        </div>
        <div class="mt-1">
          <strong>清空所有封面</strong>:删除 <code>config/images/</code> 下已生成的封面并重置封面状态。
          图片缓存不受影响;缺少封面的库会在下次预热或浏览时按当前样式重新生成。
        </div>
      </v-card-text>
    </v-card>

    <!-- 原始计数器 -->
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-counter" size="small" /> 命中计数器
        <v-spacer />
        <v-btn size="small" variant="text" :loading="resettingCounters" @click="onResetCounters">
          清零
        </v-btn>
      </v-card-title>
      <v-card-text class="d-flex flex-wrap ga-2">
        <v-chip v-for="(value, name) in stats?.counters ?? {}" :key="name" size="small" variant="tonal">
          {{ name }}: {{ value }}
        </v-chip>
        <span v-if="Object.keys(stats?.counters ?? {}).length === 0" class="text-medium-emphasis text-body-2">
          还没有命中记录(清零后或进程刚启动)
        </span>
      </v-card-text>
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { clearImageCache, getCacheStats, resetCacheCounters } from '@/api/config'
import { clearVlibCache, refreshRssLibrary } from '@/api/libraries'
import { clearCovers } from '@/api/covers'
import { useConfirm } from '@/composables/useConfirm'
import { useNotification } from '@/composables/useNotification'
import SectionHeader from '@/components/common/SectionHeader.vue'
import type { CacheStats } from '@/types/api'

const { confirm } = useConfirm()
const notify = useNotification()

const stats = ref<CacheStats | null>(null)
const loading = ref(false)
const autoRefresh = ref(true)
const lastUpdated = ref('')
const refreshingId = ref('')
const clearingImages = ref(false)
const resettingCounters = ref(false)
const clearingVlib = ref(false)
const clearingCovers = ref(false)

let timer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  try {
    stats.value = await getCacheStats()
    lastUpdated.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  } catch {
    stats.value = { available: false, reason: '读取失败(admin 无法连接代理核心)' }
  } finally {
    loading.value = false
  }
}

function counter(name: string): number {
  return stats.value?.counters?.[name] ?? 0
}

/** 命中率百分比(分母为 0 时返回 undefined,界面上不显示进度条) */
function hitRate(hitName: string, missName: string): number | undefined {
  const hit = counter(hitName)
  const miss = counter(missName)
  const total = hit + miss
  if (total === 0) return undefined
  return Math.round((hit / total) * 100)
}

const tiles = computed(() => {
  const base = stats.value?.base
  const servingRate = hitRate('serving_hit', 'serving_miss')
  const resultRate = hitRate('result_hit', 'result_miss')
  const imageMiss = counter('image_miss')
  const imageHit = counter('image_hit')
  return [
    {
      label: '全库数据源',
      value: `${base?.items ?? 0} 项`,
      hint: `所有虚拟库共用一份 · ${formatAge(base?.age_seconds)}`,
    },
    {
      label: '列表直出命中率',
      value: servingRate === undefined ? '尚无请求' : `${servingRate}%`,
      hint: `${counter('serving_hit')} 命中 / ${counter('serving_miss')} 未命中 · 304 省流 ${counter('serving_304')} 次`,
      progress: servingRate,
      progressColor: servingRate !== undefined && servingRate < 60 ? 'warning' : 'success',
    },
    {
      label: '结果层命中率',
      value: resultRate === undefined ? '尚无请求' : `${resultRate}%`,
      hint: '命中后翻页只做切片,不再重新组装与排序',
      progress: resultRate,
      progressColor: resultRate !== undefined && resultRate < 50 ? 'warning' : 'success',
    },
    {
      // 图片用"已缓存张数"作主指标:客户端拿到 immutable 后不再请求,命中率会长期是 0
      label: '图片已缓存',
      value: `${stats.value?.images?.files ?? 0} 张`,
      hint: `${formatBytes(stats.value?.images?.bytes)} / ${imageCapLabel.value} · 已抓取 ${imageMiss} 次 / 命中 ${imageHit} 次`,
      progress: imageUsagePercent.value,
      progressColor: (imageUsagePercent.value ?? 0) > 85 ? 'error' : 'primary',
    },
  ]
})

const imageCapLabel = computed(() => {
  const max = stats.value?.images?.max_bytes ?? 0
  return max > 0 ? formatBytes(max) : '不限制'
})

const imageUsagePercent = computed(() => {
  const max = stats.value?.images?.max_bytes ?? 0
  const used = stats.value?.images?.bytes ?? 0
  if (max <= 0) return undefined
  return Math.min(100, Math.round((used / max) * 100))
})

const jobs = computed(() => {
  const schedule = stats.value?.schedule
  const config = stats.value?.config
  const pick = (job: { next_run_at: string | null; next_run_in_seconds: number | null } | null | undefined) => {
    if (!job || !job.next_run_at) return null
    const at = new Date(job.next_run_at)
    const time = at.toLocaleString('zh-CN', { hour12: false })
    const remain = job.next_run_in_seconds
    return remain === null || remain === undefined ? time : `${time}(约 ${formatDuration(remain)} 后)`
  }
  return [
    {
      id: 'vlib_refresh',
      icon: 'mdi-database-clock-outline',
      title: '虚拟库缓存刷新',
      hint: config?.refresh_interval_hours
        ? `每 ${config.refresh_interval_hours} 小时先探测再刷新,内容没变化的库跳过`
        : '未开启定时刷新(间隔为 0),只能手动刷新',
      nextRun: pick(schedule?.vlib_refresh),
    },
    {
      id: 'tmdb_index',
      icon: 'mdi-movie-search-outline',
      title: 'TMDB 索引重建',
      hint: config?.enable_tmdb_index
        ? `每 ${config.tmdb_index_refresh_interval || 0} 小时重建一次(0 = 仅启动时)`
        : '已关闭,季/集合并会退回全库遍历',
      nextRun: pick(schedule?.tmdb_index),
    },
    {
      id: 'rss_refresh',
      icon: 'mdi-rss',
      title: 'RSS 虚拟库刷新',
      hint: '按 RSS 刷新间隔抓取源站更新',
      nextRun: pick(schedule?.rss_refresh),
    },
  ]
})

/** 全系统的缓存清单:回答"到底缓存了些什么" */
const layers = computed(() => {
  const s = stats.value
  const t = s?.ttls
  const c = s?.caches
  const cfg = s?.config
  return [
    {
      name: 'L3 直出结果层',
      what: '已组装 + 已排序的可见条目列表',
      where: '内存',
      scope: '每库 × 每用户 × 当前排序',
      ttl: '随元数据/状态层版本变化',
      size: `${c?.result_entries ?? 0} 条`,
      hit: `${counter('result_hit')} / ${counter('result_miss')}`,
    },
    {
      name: 'L2 用户状态层',
      what: '每个账号的已看 / 收藏 / 进度',
      where: '内存 + SQLite',
      scope: '每账号,所有虚拟库共用',
      ttl: formatDuration(t?.overlay_soft),
      size: `${s?.overlays?.length ?? 0} 份`,
      hit: '—',
    },
    {
      name: 'L1 派生元数据',
      what: '该库过滤 + TMDB 合并后的条目',
      where: '内存 + SQLite',
      scope: '每库',
      ttl: `${formatDuration(t?.meta_soft)} 软过期 / ${formatDuration(t?.meta_hard)} 硬过期`,
      size: `${s?.libraries?.length ?? 0} 库`,
      hit: `${counter('serving_hit')} / ${counter('serving_miss')}`,
    },
    {
      name: 'L0 全库数据源',
      what: '向 Emby 裸拉的原始条目(未筛选、未合并)',
      where: '内存 + SQLite',
      scope: '全局一份,所有库共用',
      ttl: `${formatDuration(t?.base_soft)}(内容有变化才重拉)`,
      size: `${s?.base?.items ?? 0} 项`,
      hit: '—',
    },
    {
      name: '季/集合并结果',
      what: '多版本剧集合并后的季与集',
      where: '内存',
      scope: '每账号 × 每剧集 × 每季',
      ttl: formatDuration(t?.merge),
      size: `${c?.merge_entries ?? 0} 条`,
      hit: '—',
    },
    {
      name: 'API 响应缓存',
      what: '所有 GET JSON:首页最新、首页分区、视图、真实库列表…',
      where: '内存',
      scope: '每账号 × URL × 查询参数',
      ttl: cfg?.api_cache_enabled ? `${cfg.api_cache_ttl} 秒` : '已关闭',
      size: `${s?.api_cache?.entries ?? 0} / 500 条`,
      hit: `${counter('api_hit')} / ${counter('api_miss')}`,
    },
    {
      name: '图片缓存',
      what: '海报 / 背景 / 缩略图',
      where: '磁盘 image_cache/',
      scope: '全局,按图片 tag 区分',
      ttl: '永久(URL 带 tag,immutable)',
      size: `${s?.images?.files ?? 0} 个 / ${formatBytes(s?.images?.bytes)}`,
      hit: `${counter('image_hit')} / ${counter('image_miss')}`,
    },
    {
      name: '封面素材',
      what: '每库前 100 项,供封面自动生成取材',
      where: '内存 + SQLite',
      scope: '每库',
      ttl: '跟随该库元数据',
      size: `${s?.cover_materials?.libraries ?? 0} 库`,
      hit: '—',
    },
    {
      name: 'TMDB 索引',
      what: '条目 → TMDB ID 查找表(季/集合并用)',
      where: 'SQLite',
      scope: '全局',
      ttl: cfg?.tmdb_index_refresh_interval ? `${cfg.tmdb_index_refresh_interval} 小时重建` : '仅启动时构建',
      size: `${s?.tmdb_index?.count ?? 0} 条`,
      hit: '—',
    },
  ]
})

function formatAge(seconds?: number | null): string {
  if (seconds === null || seconds === undefined) return '尚未构建'
  if (seconds < 60) return `${Math.round(seconds)} 秒前`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`
  return `${(seconds / 3600).toFixed(1)} 小时前`
}

function formatDuration(seconds?: number | null): string {
  if (!seconds || seconds <= 0) return '—'
  if (seconds < 60) return `${Math.round(seconds)} 秒`
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟`
  const hours = seconds / 3600
  return hours >= 10 ? `${Math.round(hours)} 小时` : `${hours.toFixed(1)} 小时`
}

function formatBytes(bytes?: number | null): string {
  if (!bytes) return '0 MB'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`
}

function shortUser(userId: string): string {
  return userId ? `${userId.slice(0, 8)}…` : '未知用户'
}

/** 叠加层新鲜度:年龄相对软过期时间,越小越旧 */
function overlayFreshness(ageSeconds: number): number {
  const ttl = stats.value?.ttls?.overlay_soft ?? 600
  if (ttl <= 0) return 100
  return Math.max(0, Math.min(100, Math.round((1 - ageSeconds / ttl) * 100)))
}

async function refreshLibrary(id: string, name: string) {
  refreshingId.value = id
  try {
    await refreshRssLibrary(id)
    notify.info(`已触发「${name}」刷新,后台处理中`)
    setTimeout(load, 2500)
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '触发刷新失败')
  } finally {
    refreshingId.value = ''
  }
}

async function onClearImages() {
  const yes = await confirm({
    title: '清空图片缓存',
    message: '将删除 config/image_cache/ 下的所有海报/背景图缓存。\n虚拟库封面不受影响,图片会在下次浏览时重新抓取。确定继续吗?',
    confirmText: '清空',
    cancelText: '取消',
    color: 'error',
    icon: 'mdi-delete-outline',
  })
  if (!yes) return
  clearingImages.value = true
  try {
    const result = await clearImageCache()
    notify.success(`图片缓存已清空(删除 ${result.removed ?? 0} 个文件)`)
    await load()
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '清空图片缓存失败')
  } finally {
    clearingImages.value = false
  }
}

async function onClearVlib() {
  const yes = await confirm({
    title: '清空虚拟库项目缓存',
    message:
      '将删除 SQLite 中的全库数据源、各库派生结果、用户状态层、TMDB 索引与合并结果,并清空代理内存缓存。\n' +
      '图片缓存和封面不受影响。下次浏览或预热时会重新向 Emby 拉取全库并重建。确定继续吗?',
    confirmText: '清空',
    cancelText: '取消',
    color: 'error',
    icon: 'mdi-database-remove-outline',
  })
  if (!yes) return
  clearingVlib.value = true
  try {
    const counts = await clearVlibCache()
    notify.success(
      `缓存已清空(项目缓存 ${counts.vlib_items} 库 / 解析数据 ${counts.serving_rows} 行)`,
    )
    await load()
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '清空项目缓存失败')
  } finally {
    clearingVlib.value = false
  }
}

async function onClearCovers() {
  const yes = await confirm({
    title: '清空所有本地封面',
    message:
      '将删除 config/images/ 下所有已生成的封面,并重置各虚拟库的封面状态。\n' +
      '图片缓存不受影响;缺少封面的库会在下次预热或浏览时按当前样式重新生成。确定继续吗?',
    confirmText: '清空',
    cancelText: '取消',
    color: 'error',
    icon: 'mdi-image-multiple-outline',
  })
  if (!yes) return
  clearingCovers.value = true
  try {
    await clearCovers()
    notify.success('所有本地封面已清空')
    await load()
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '清空封面失败')
  } finally {
    clearingCovers.value = false
  }
}

async function onResetCounters() {
  const yes = await confirm({
    title: '清零命中计数器',
    message: '只把命中/未命中的统计数字归零,不会影响任何缓存内容。\n适合在改完配置后重新观察命中率。确定继续吗?',
    confirmText: '清零',
    cancelText: '取消',
    color: 'warning',
    icon: 'mdi-counter',
  })
  if (!yes) return
  resettingCounters.value = true
  try {
    await resetCacheCounters()
    notify.success('计数器已清零')
    await load()
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '清零失败')
  } finally {
    resettingCounters.value = false
  }
}

function startTimer() {
  stopTimer()
  if (autoRefresh.value) timer = setInterval(load, 10_000)
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(autoRefresh, startTimer)

onMounted(async () => {
  await load()
  startTimer()
})

onBeforeUnmount(stopTimer)
</script>

<style scoped>
.overlay-bar {
  width: 90px;
  flex: 0 0 auto;
}

.border-danger {
  border: 1px solid rgba(var(--v-theme-error), 0.35);
}
</style>
