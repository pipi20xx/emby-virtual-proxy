<template>
  <div class="evp-page">
    <SectionHeader title="系统设置" subtitle="连接信息、代理行为与封面默认参数,保存后写入 config.json">
      <template #actions>
        <v-chip v-if="dirty" color="warning" size="small" variant="tonal" class="mr-1">有未保存修改</v-chip>
        <v-btn
          color="primary"
          variant="flat"
          prepend-icon="mdi-content-save-outline"
          :loading="configStore.saving"
          @click="saveAll"
        >
          保存所有设置
        </v-btn>
      </template>
    </SectionHeader>

    <template v-if="ready">
      <v-form ref="formRef" @submit.prevent>
        <!-- Emby 连接 -->
        <v-card>
          <v-card-title class="d-flex align-center ga-2">
            <v-icon icon="mdi-server" size="small" /> Emby 连接
          </v-card-title>
          <v-card-text class="d-flex flex-column ga-4">
            <v-text-field
              v-model="draft.emby_url"
              label="Emby 服务器地址"
              placeholder="http://192.168.50.12:8096"
              hint="真实 Emby/Jellyfin 服务器的完整访问地址"
              persistent-hint
              :rules="[rules.required('Emby 服务器地址'), rules.httpUrl]"
            />
            <SecretField
              v-model="draft.emby_api_key"
              label="Emby API 密钥"
              placeholder="在 Emby 控制台 -> 高级 -> API 密钥 中生成"
              :rules="[rules.required('Emby API 密钥')]"
            />
          </v-card-text>
        </v-card>

        <v-row>
          <v-col cols="12" md="6">
            <!-- TMDB 与缺失剧集 -->
            <v-card class="fill-height">
              <v-card-title class="d-flex align-center ga-2">
                <v-icon icon="mdi-movie-search" size="small" /> TMDB 与缺失剧集
              </v-card-title>
              <v-card-text class="d-flex flex-column ga-4">
                <SecretField
                  v-model="draft.tmdb_api_key"
                  label="TMDB API 密钥"
                  hint="用于查询缺失剧集信息与 RSS 占位条目"
                  persistent-hint
                />
                <v-text-field
                  v-model="draft.tmdb_proxy"
                  label="TMDB HTTP 代理"
                  placeholder="http://127.0.0.1:7890"
                  hint="服务器无法直连 TMDB 时配置,留空不使用"
                  persistent-hint
                />
                <v-switch
                  v-model="draft.show_missing_episodes"
                  color="primary"
                  label="显示缺失的剧集"
                  hint="浏览季页面时,通过 TMDB 注入本地尚未收藏的剧集"
                  persistent-hint
                />
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="6">
            <!-- 代理行为 -->
            <v-card class="fill-height">
              <v-card-title class="d-flex align-center ga-2">
                <v-icon icon="mdi-swap-horizontal" size="small" /> 代理行为
              </v-card-title>
              <v-card-text class="d-flex flex-column ga-4">
                <v-combobox
                  v-model="draft.hide"
                  :items="HIDE_TYPE_PRESETS"
                  label="全局隐藏类型"
                  multiple
                  closable-chips
                  hint="按 CollectionType 隐藏真实库(旧版布局模式生效);主页布局中可覆盖"
                  persistent-hint
                />
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <v-row>
          <v-col cols="12" md="4">
            <!-- 缓存与访问加速 -->
            <v-card class="fill-height">
              <v-card-title class="d-flex align-center ga-2">
                <v-icon icon="mdi-database-refresh" size="small" /> 缓存与访问加速
              </v-card-title>
              <v-card-text class="d-flex flex-column ga-4">
                <div class="text-subtitle-2 text-high-emphasis">虚拟库列表缓存(访问加速)</div>
                <v-switch
                  v-model="draft.enable_vlib_cache"
                  color="primary"
                  label="启用项目缓存"
                  hint="总开关:把每个虚拟库的项目列表解析结果缓存到内存与 SQLite(重启不丢)。关闭后不再保存任何项目数据,封面生成改为按需实时向 Emby 取材,下方所有选项停用。"
                  persistent-hint
                />
                <v-switch
                  v-model="draft.enable_vlib_local_serving"
                  color="primary"
                  label="虚拟库列表本地直出"
                  :disabled="!draft.enable_vlib_cache"
                  hint="浏览虚拟库时由代理直接返回缓存列表(排序、翻页、类型过滤都在本地完成),不再请求 Emby。分两层存储:共享元数据(每库一份、所有用户共用)+ 用户状态层(每账号只存自己的已看/进度),多用户互不串数据。"
                  persistent-hint
                />
                <v-text-field
                  :model-value="String(draft.vlib_cache_refresh_interval ?? 0)"
                  label="预热间隔(小时)"
                  type="number"
                  min="0"
                  step="1"
                  suffix="小时"
                  :disabled="!draft.enable_vlib_cache"
                  hint="每 N 小时后台自动刷新全部虚拟库缓存(重新解析共享元数据 + 刷新各用户状态层),并顺带补齐缺失封面;0 表示禁用。启用后代理重启时也会先全量预热一轮。"
                  persistent-hint
                  @update:model-value="setVlibCacheInterval"
                />
                <div class="text-subtitle-2 text-high-emphasis mt-2">Emby 响应缓存(内存)</div>
                <v-switch
                  v-model="draft.enable_cache"
                  color="primary"
                  label="启用响应缓存(内存)"
                  hint="总开关:相同的 API 请求在 TTL 内直接返回内存缓存(重启即清空,由预热机制自动回填)。关闭后所有请求实时转发 Emby,下方 TTL 停用。"
                  persistent-hint
                />
                <v-text-field
                  :model-value="String(draft.api_cache_ttl ?? 0)"
                  label="响应缓存 TTL(秒)"
                  type="number"
                  min="0"
                  step="30"
                  suffix="秒"
                  :disabled="!draft.enable_cache"
                  hint="内存缓存的有效期:相同请求在此时间内直接返回缓存响应(对首页、媒体库视图等所有 GET 接口生效);0 表示禁用。保存后即时生效,无需重启。"
                  persistent-hint
                  @update:model-value="setApiCacheTtl"
                />

                <div class="text-caption text-medium-emphasis">
                  <div class="font-weight-medium text-high-emphasis mb-1">工作原理</div>
                  <div>① 共享元数据:每个虚拟库只向 Emby 解析一份(所有用户共用),这是缓存的大头;</div>
                  <div>② 用户状态层:每个账号只叠加自己的已看/进度(几十 KB),互不串数据;</div>
                  <div>③ 没被预热到的用户,首次浏览照常实时查询并自动补建,第二次起秒开;</div>
                  <div>④ 数据过期后先返回旧数据、后台静默刷新,浏览永不阻塞。</div>
                  <div class="mt-1">
                    注意:海报图片、播放控制与已看状态上报始终直连 Emby;列表与详情类请求由以上缓存加速,图片本地化暂未包含。
                  </div>
                </div>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="4">
            <!-- TMDB 合并与索引加速 -->
            <v-card class="fill-height">
              <v-card-title class="d-flex align-center ga-2">
                <v-icon icon="mdi-movie-search" size="small" /> TMDB 合并与索引加速
              </v-card-title>
              <v-card-text class="d-flex flex-column ga-4">
                <v-switch
                  v-model="draft.force_merge_by_tmdb_id"
                  color="primary"
                  label="全局强制按 TMDB ID 合并"
                  hint="同一部作品(相同 TMDB ID)的多个版本条目,在虚拟库列表和详情页的季/集中去重合并为一个展示。开启后覆盖所有虚拟库的独立设置;未开启时各虚拟库可在编辑页单独开启。"
                  persistent-hint
                />
                <v-switch
                  v-model="draft.enable_tmdb_index"
                  color="primary"
                  label="启用 TMDB 索引"
                  hint="把全库「媒体条目 → TMDB ID」的对应关系建成一张本地查找表(管理员视角)。开启后,进入剧集详情页合并季/集时直接查索引定位关联剧集,不再全库遍历 Emby —— 这是详情页打开速度的最大瓶颈。关闭后退回旧的遍历方案,功能不变但每次进详情页都会全库扫描。服务于所有启用合并的库(全局强制或单库开启均可)。"
                  persistent-hint
                />
                <v-text-field
                  :model-value="String(draft.tmdb_index_refresh_interval ?? 0)"
                  label="重建间隔(小时)"
                  type="number"
                  min="0"
                  step="1"
                  suffix="小时"
                  :disabled="!draft.enable_tmdb_index"
                  hint="每 N 小时全量重建一次索引(只拉条目 ID 清单,负载很小);0 表示仅启动时构建一次,之后靠查询自愈回写维持。索引里已删除的媒体不会造成错误 —— 每次查询都会实时校验条目存活与可见性。"
                  persistent-hint
                  @update:model-value="setTmdbIndexInterval"
                />

                <div class="text-caption text-medium-emphasis">
                  <div class="font-weight-medium text-high-emphasis mb-1">工作原理</div>
                  <div>① 合并:列表与详情页中,相同 TMDB ID 的多版本条目按顺序去重,保留第一个作为代表;</div>
                  <div>② 索引构建:定时以管理员身份拉取全库带 TMDB ID 的条目清单,一次性重建「条目 → TMDB ID」查找表;</div>
                  <div>③ 查询:进详情页合并季/集时,按 TMDB ID 查索引定位关联剧集,再一次批量校验条目存活与当前账号可见性;</div>
                  <div>④ 自愈与安全:新入库剧集未进索引时,自动回退一次全库遍历并把结果回写;每个账号的可见性实时校验,不会串数据。</div>
                </div>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="4">
            <!-- RSS -->
            <v-card class="fill-height">
              <v-card-title class="d-flex align-center ga-2">
                <v-icon icon="mdi-rss" size="small" /> RSS 定时刷新
              </v-card-title>
              <v-card-text>
                <v-text-field
                  :model-value="String(draft.rss_refresh_interval ?? 0)"
                  label="刷新间隔(小时)"
                  type="number"
                  min="0"
                  step="1"
                  suffix="小时"
                  hint="RSS 虚拟库的自动刷新周期,0 表示禁用定时刷新"
                  persistent-hint
                  @update:model-value="setRefreshInterval"
                />
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="4">
            <!-- 封面默认 -->
            <v-card class="fill-height">
              <v-card-title class="d-flex align-center ga-2">
                <v-icon icon="mdi-image-auto-adjust" size="small" /> 封面默认参数
              </v-card-title>
              <v-card-text class="d-flex flex-column ga-4">
                <v-select
                  v-model="draft.default_cover_style"
                  :items="COVER_STYLES"
                  item-title="title"
                  item-value="value"
                  label="自动生成封面默认样式"
                />
                <v-text-field
                  v-model="draft.custom_zh_font_path"
                  label="自定义中文字体路径"
                  placeholder="容器内绝对路径,留空使用内置字体"
                />
                <v-text-field
                  v-model="draft.custom_en_font_path"
                  label="自定义英文字体路径"
                  placeholder="容器内绝对路径,留空使用内置字体"
                />
                <v-text-field
                  v-model="draft.custom_image_path"
                  label="全局自定义图片目录"
                  placeholder="容器内绝对路径;虚拟库未单独指定时作为封面素材来源"
                />
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <!-- 访问安全 -->
        <v-card>
          <v-card-title class="d-flex align-center ga-2">
            <v-icon icon="mdi-shield-lock-outline" size="small" /> 访问安全
          </v-card-title>
          <v-card-text>
            <SecretField
              v-model="draft.admin_password"
              label="管理面板密码"
              placeholder="留空 = 无需登录直接使用"
              hint="设置并保存后,打开管理面板需要输入密码;只影响面板,代理核心(8999)不受影响"
              persistent-hint
            />
          </v-card-text>
        </v-card>

        <!-- 危险区域 -->
        <v-card class="border-error">
          <v-card-title class="d-flex align-center ga-2 text-error">
            <v-icon icon="mdi-alert-octagon-outline" size="small" /> 危险区域
          </v-card-title>
          <v-card-text class="d-flex flex-wrap ga-3">
            <v-btn
              color="error"
              prepend-icon="mdi-image-multiple-outline"
              :loading="clearingCovers"
              @click="clearCovers"
            >
              清空所有本地封面
            </v-btn>
            <v-btn
              color="error"
              prepend-icon="mdi-database-remove-outline"
              :loading="clearingVlibCache"
              @click="clearVlibCache"
            >
              清空虚拟库项目缓存
            </v-btn>
            <v-btn
              color="warning"
              prepend-icon="mdi-restart"
              :loading="restarting"
              @click="restartProxy"
            >
              重启代理服务(清空缓存)
            </v-btn>
            <div class="text-body-2 text-medium-emphasis align-self-center">
              重启会短暂中断 8999 端口的代理服务,是修改配置后清空内存缓存的正确方式。
            </div>
          </v-card-text>
        </v-card>
      </v-form>
    </template>

    <v-card v-else-if="configStore.loadError">
      <EmptyState icon="mdi-alert-circle-outline" title="配置加载失败" :hint="configStore.loadError">
        <v-btn color="primary" @click="initialize">重试</v-btn>
      </EmptyState>
    </v-card>

    <v-card v-else>
      <v-progress-linear indeterminate color="primary" />
    </v-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import type { VForm } from 'vuetify/components'

import * as coversApi from '@/api/covers'
import * as librariesApi from '@/api/libraries'
import * as systemApi from '@/api/emby'
import { useConfirm } from '@/composables/useConfirm'
import { useNotification } from '@/composables/useNotification'
import { useConfigStore } from '@/stores/useConfigStore'
import { deepClone } from '@/utils/clone'
import { COVER_STYLES, HIDE_TYPE_PRESETS, createDefaultConfig, type AppConfig } from '@/types/config'
import EmptyState from '@/components/common/EmptyState.vue'
import SecretField from '@/components/common/SecretField.vue'
import SectionHeader from '@/components/common/SectionHeader.vue'

const configStore = useConfigStore()
const { confirm } = useConfirm()
const notify = useNotification()

const formRef = ref<VForm | null>(null)
const draft = ref<AppConfig>(createDefaultConfig())
const ready = ref(false)
const initialSnapshot = ref('')
const clearingCovers = ref(false)
const clearingVlibCache = ref(false)
const restarting = ref(false)

const dirty = computed(() => Boolean(draft.value) && JSON.stringify(draft.value) !== initialSnapshot.value)

const rules = {
  required: (label: string) => (value: string) =>
    Boolean(value && String(value).trim()) || `${label}不能为空`,
  httpUrl: (value: string) =>
    /^https?:\/\//.test(String(value ?? '')) || '地址必须以 http:// 或 https:// 开头',
}

async function initialize() {
  await configStore.load()
  if (configStore.config) {
    resetDraft(configStore.config)
    ready.value = true
  }
}

function resetDraft(source: AppConfig) {
  draft.value = deepClone(source)
  initialSnapshot.value = JSON.stringify(draft.value)
}

function setRefreshInterval(value: unknown) {
  if (!draft.value) return
  const parsed = Math.max(0, Math.floor(Number(value) || 0))
  draft.value.rss_refresh_interval = parsed
}

function setVlibCacheInterval(value: unknown) {
  if (!draft.value) return
  const parsed = Math.max(0, Math.floor(Number(value) || 0))
  draft.value.vlib_cache_refresh_interval = parsed
}

function setApiCacheTtl(value: unknown) {
  if (!draft.value) return
  const parsed = Math.max(0, Math.floor(Number(value) || 0))
  draft.value.api_cache_ttl = parsed
}

function setTmdbIndexInterval(value: unknown) {
  if (!draft.value) return
  const parsed = Math.max(0, Math.floor(Number(value) || 0))
  draft.value.tmdb_index_refresh_interval = parsed
}

async function saveAll() {
  const { valid } = await formRef.value!.validate()
  if (!valid) {
    notify.error('请先修正表单中标红的必填项')
    return
  }
  try {
    const saved = await configStore.save(draft.value)
    resetDraft(saved)
    notify.success('设置已保存')
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '保存失败')
  }
}

async function clearCovers() {
  const yes = await confirm({
    title: '清空所有封面',
    message: '将删除所有已生成的封面图片,并重置各虚拟库的封面状态。\n该操作不可恢复,确定继续吗?',
    confirmText: '清空',
    cancelText: '再想想',
    color: 'error',
    icon: 'mdi-alert-octagon-outline',
  })
  if (!yes) return
  clearingCovers.value = true
  try {
    await coversApi.clearCovers()
    const fresh = await configStore.reload()
    if (fresh) resetDraft(fresh)
    notify.success('所有封面已清空')
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '清空封面失败')
  } finally {
    clearingCovers.value = false
  }
}

async function clearVlibCache() {
  const yes = await confirm({
    title: '清空虚拟库项目缓存',
    message: '将删除 SQLite 中持久化的项目列表,并清空代理核心内存缓存。\n封面不受影响,缓存会在下次浏览或预热时重建。确定继续吗?',
    confirmText: '清空',
    cancelText: '再想想',
    color: 'error',
    icon: 'mdi-database-remove-outline',
  })
  if (!yes) return
  clearingVlibCache.value = true
  try {
    const counts = await librariesApi.clearVlibCache()
    notify.success(`虚拟库缓存已清空(项目缓存 ${counts.vlib_items} 库 / 解析列表 ${counts.serving_rows} 行)`)
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '清空缓存失败')
  } finally {
    clearingVlibCache.value = false
  }
}

async function restartProxy() {
  const yes = await confirm({
    title: '重启代理服务',
    message: '代理将重启并清空全部内存缓存,期间客户端会短暂无法访问。\n确定继续吗?',
    confirmText: '重启',
    cancelText: '取消',
    color: 'warning',
    icon: 'mdi-restart',
  })
  if (!yes) return
  restarting.value = true
  try {
    await systemApi.restartProxy()
    notify.info('重启命令已发送,服务将在几秒内恢复')
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '重启失败')
  } finally {
    restarting.value = false
  }
}

onMounted(initialize)
</script>

<style scoped>
.border-error {
  border: 1px solid rgba(var(--v-theme-error), 0.35);
}
</style>
