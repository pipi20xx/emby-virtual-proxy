<template>
  <div class="evp-page">
    <SectionHeader title="虚拟媒体库" subtitle="虚拟库会像真实媒体库一样出现在 Emby 客户端的主页上">
      <template #actions>
        <v-btn
          prepend-icon="mdi-refresh"
          :loading="embyStore.loadState === 'loading'"
          @click="refreshEmbyData"
        >
          刷新 Emby 数据
        </v-btn>
        <v-btn prepend-icon="mdi-view-dashboard-edit-outline" :to="{ name: 'layout' }">
          主页布局
        </v-btn>
        <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" :to="{ name: 'library-new' }">
          新建虚拟库
        </v-btn>
      </template>
    </SectionHeader>

    <!-- 过滤工具条 -->
    <v-card>
      <v-card-text class="d-flex flex-wrap align-center ga-3 py-3">
        <v-text-field
          v-model="search"
          class="flex-grow-1"
          min-width="220px"
          prepend-inner-icon="mdi-magnify"
          placeholder="搜索库名称 / 资源 ID / RSS 链接"
          clearable
          hide-details
        />
        <v-chip-group v-model="typeFilter" selected-class="text-primary">
          <v-chip value="" filter>全部</v-chip>
          <v-chip
            v-for="(label, type) in RESOURCE_TYPE_LABELS"
            :key="type"
            :value="type"
            filter
          >
            {{ label }}
          </v-chip>
        </v-chip-group>
      </v-card-text>
    </v-card>

    <!-- 加载中 -->
    <v-card v-if="!configStore.loaded && !configStore.loadError">
      <v-progress-linear indeterminate color="primary" />
    </v-card>

    <!-- 加载失败 -->
    <v-card v-else-if="configStore.loadError">
      <EmptyState icon="mdi-alert-circle-outline" title="配置加载失败" :hint="configStore.loadError">
        <v-btn color="primary" @click="initialize">重试</v-btn>
      </EmptyState>
    </v-card>

    <!-- 空列表 -->
    <v-card v-else-if="!filteredLibraries.length">
      <EmptyState
        icon="mdi-library-shelves"
        :title="hasAnyLibrary ? '没有符合条件的虚拟库' : '还没有任何虚拟库'"
        :hint="hasAnyLibrary ? '试试调整搜索关键词或类型过滤' : '基于合集、标签、类型、工作室或人员创建你的第一个虚拟库'"
      >
        <v-btn v-if="!hasAnyLibrary" color="primary" variant="flat" prepend-icon="mdi-plus" :to="{ name: 'library-new' }">
          新建虚拟库
        </v-btn>
      </EmptyState>
    </v-card>

    <!-- 库卡片网格 -->
    <v-row v-else>
      <v-col v-for="lib in filteredLibraries" :key="lib.id" cols="12" sm="6" md="4" lg="3">
        <v-card class="d-flex flex-column fill-height">
          <router-link :to="{ name: 'library-edit', params: { id: lib.id } }" class="text-decoration-none">
            <v-img
              v-if="lib.image_tag"
              :src="`/covers/${lib.id}.${lib.cover_format || 'jpg'}?t=${lib.image_tag}`"
              aspect-ratio="16/9"
              cover
            >
              <template #placeholder>
                <div class="evp-cover d-flex align-center justify-center bg-surface-variant">
                  <v-progress-circular indeterminate size="20" width="2" />
                </div>
              </template>
            </v-img>
            <div
              v-else
              class="evp-cover d-flex flex-column align-center justify-center bg-surface-variant text-medium-emphasis"
            >
              <v-icon icon="mdi-image-off-outline" size="32" />
              <span class="text-caption mt-2">暂无封面</span>
            </div>
          </router-link>

          <v-card-text class="pb-1 flex-grow-1">
            <div class="d-flex align-center ga-2">
              <span class="text-subtitle-1 font-weight-bold text-truncate">{{ lib.name }}</span>
              <v-spacer />
              <v-chip
                :color="lib.merge_by_tmdb_id ? 'success' : 'default'"
                size="small"
                :variant="lib.merge_by_tmdb_id ? 'tonal' : 'outlined'"
                title="是否启用 TMDB ID 合并"
              >
                TMDB合并
              </v-chip>
            </div>

            <div class="d-flex align-center ga-2 mt-2">
              <v-chip color="primary" variant="tonal" size="small">
                {{ RESOURCE_TYPE_LABELS[lib.resource_type] }}
              </v-chip>
              <span
                v-if="lib.advanced_filter_id"
                class="text-caption text-medium-emphasis"
                title="已绑定高级筛选器"
              >
                <v-icon icon="mdi-filter-outline" size="x-small" />
                筛选器
              </span>
            </div>

            <div class="text-caption text-medium-emphasis text-truncate-2 mt-2">
              {{ resourceDetail(lib) }}
            </div>
          </v-card-text>

          <v-card-actions>
            <v-spacer />
            <v-btn
              v-if="lib.resource_type === 'rsshub'"
              icon="mdi-rss"
              variant="text"
              color="success"
              :loading="refreshingId === lib.id"
              title="手动刷新 RSS 源"
              @click="refreshRss(lib)"
            />
            <v-btn
              icon="mdi-pencil-outline"
              variant="text"
              title="编辑"
              :to="{ name: 'library-edit', params: { id: lib.id } }"
            />
            <v-btn
              icon="mdi-delete-outline"
              variant="text"
              color="error"
              title="删除"
              @click="removeLibrary(lib)"
            />
          </v-card-actions>
        </v-card>
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import * as librariesApi from '@/api/libraries'
import EmptyState from '@/components/common/EmptyState.vue'
import SectionHeader from '@/components/common/SectionHeader.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useNotification } from '@/composables/useNotification'
import { useConfigStore } from '@/stores/useConfigStore'
import { useEmbyDataStore } from '@/stores/useEmbyDataStore'
import { RESOURCE_TYPE_LABELS, type ResourceType, type VirtualLibrary } from '@/types/config'

const configStore = useConfigStore()
const embyStore = useEmbyDataStore()
const { confirm } = useConfirm()
const notify = useNotification()

const search = ref('')
const typeFilter = ref<ResourceType | ''>('')
const refreshingId = ref<string | null>(null)

const hasAnyLibrary = computed(() => configStore.virtualLibraries.length > 0)

const filteredLibraries = computed(() =>
  configStore.virtualLibraries.filter((lib) => {
    if (typeFilter.value && lib.resource_type !== typeFilter.value) return false
    const query = (search.value ?? '').trim().toLowerCase()
    if (!query) return true
    return (
      lib.name.toLowerCase().includes(query) ||
      (lib.resource_id ?? '').toLowerCase().includes(query) ||
      (lib.rsshub_url ?? '').toLowerCase().includes(query)
    )
  }),
)

/** 资源详情展示文本 */
function resourceDetail(lib: VirtualLibrary): string {
  if (lib.resource_type === 'rsshub') return lib.rsshub_url ?? '未配置 RSS 链接'
  if (lib.resource_type === 'all') return '全部媒体库内容'
  if (!lib.resource_id) return '未选择资源'
  if (lib.resource_type === 'person') {
    const cached = embyStore.personName(lib.resource_id)
    return cached ? `${cached} (${lib.resource_id})` : `ID: ${lib.resource_id}`
  }
  const item = embyStore.findResourceName(lib.resource_type, lib.resource_id)
  return item ? `${item.name} (${lib.resource_id})` : `未知ID: ${lib.resource_id}`
}

/** 为 person 类型的库预解析人名 */
async function resolvePersonNames() {
  const personIds = configStore.virtualLibraries
    .filter((lib) => lib.resource_type === 'person' && lib.resource_id && !embyStore.personName(lib.resource_id))
    .map((lib) => lib.resource_id!)
  await Promise.allSettled(personIds.map((id) => embyStore.resolvePersonName(id)))
}

async function refreshEmbyData() {
  await embyStore.load()
  if (embyStore.loadState === 'ok') {
    notify.success('Emby 数据已刷新')
    await resolvePersonNames()
  } else {
    notify.error('刷新失败,请检查 Emby 连接设置')
  }
}

async function refreshRss(lib: VirtualLibrary) {
  refreshingId.value = lib.id
  try {
    await librariesApi.refreshRssLibrary(lib.id)
    notify.info(`「${lib.name}」的 RSS 刷新已在后台开始处理`)
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '刷新失败')
  } finally {
    refreshingId.value = null
  }
}

async function removeLibrary(lib: VirtualLibrary) {
  const yes = await confirm({
    title: '删除虚拟库',
    message: `确定删除虚拟库「${lib.name}」吗?\n该操作不可恢复,RSS 库的本地缓存数据也会一并清理。`,
    confirmText: '删除',
    cancelText: '再想想',
    color: 'error',
    icon: 'mdi-delete-alert-outline',
  })
  if (!yes) return
  try {
    await librariesApi.deleteLibrary(lib.id)
    await configStore.reload()
    notify.success(`「${lib.name}」已删除`)
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '删除失败')
  }
}

async function initialize() {
  await configStore.load()
  await resolvePersonNames()
}

onMounted(initialize)
</script>
