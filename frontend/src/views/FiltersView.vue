<template>
  <div class="evp-page">
    <SectionHeader title="高级筛选器" subtitle="可复用的内容过滤规则;创建后在虚拟库中绑定使用">
      <template #actions>
        <v-btn icon="mdi-help-circle-outline" variant="text" title="性能指南" @click="guideVisible = true" />
        <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreate">新增筛选器</v-btn>
      </template>
    </SectionHeader>

    <!-- 列表 -->
    <template v-if="filters.length">
      <v-card v-for="filter in filters" :key="filter.id" class="mb-3">
        <v-card-text class="d-flex align-center flex-wrap ga-3 py-3">
          <v-icon icon="mdi-filter-variant" size="small" class="text-primary" />
          <span class="text-subtitle-1 font-weight-bold">{{ filter.name }}</span>
          <v-chip size="small" variant="tonal" :color="filter.match_all ? 'primary' : 'warning'">
            匹配{{ filter.match_all ? '所有' : '任意' }}条件
          </v-chip>
          <v-chip size="small" variant="tonal" :color="isLocalMode(filter) ? 'success' : 'default'">
            {{ isLocalMode(filter) ? '本地全量' : 'Emby 原生' }}
          </v-chip>
          <span class="text-caption text-medium-emphasis">共 {{ filter.rules.length }} 条规则</span>
          <v-spacer />
          <v-btn icon="mdi-pencil-outline" variant="text" title="编辑" @click="openEdit(filter)" />
          <v-btn
            icon="mdi-delete-outline"
            variant="text"
            color="error"
            title="删除"
            @click="removeFilter(filter)"
          />
        </v-card-text>
      </v-card>
    </template>

    <v-card v-else>
      <EmptyState
        icon="mdi-filter-variant"
        title="还没有筛选器"
        hint="筛选器由多条规则组成,可在虚拟库编辑页绑定,实现 Emby 原生做不到的精确筛选"
      >
        <v-btn color="primary" variant="flat" prepend-icon="mdi-plus" @click="openCreate">新增筛选器</v-btn>
      </EmptyState>
    </v-card>

    <!-- 编辑弹窗 -->
    <v-dialog v-model="editorVisible" max-width="920" persistent scrollable>
      <v-card>
        <v-card-title class="d-flex align-center ga-2">
          <v-icon icon="mdi-pencil-box-outline" size="small" />
          {{ editingId ? '编辑筛选器' : '新增筛选器' }}
        </v-card-title>

        <v-card-text v-if="editing" class="d-flex flex-column ga-4">
          <v-text-field
            v-model="editing.name"
            label="筛选器名称"
            placeholder="例如:豆瓣高分电影"
            :rules="[rules.required('筛选器名称')]"
          />

          <!-- 执行模式:决定规则由谁评估 -->
          <div>
            <div class="text-caption text-medium-emphasis mb-1">执行模式</div>
            <v-btn-toggle v-model="editing.execution_mode" mandatory color="primary" density="comfortable">
              <v-btn value="native" prepend-icon="mdi-server">
                Emby 原生
              </v-btn>
              <v-btn value="local" prepend-icon="mdi-filter-check-outline">
                本地全量
              </v-btn>
            </v-btn-toggle>
            <v-alert
              class="mt-2"
              :type="isLocalMode(editing) ? 'success' : 'info'"
              variant="tonal"
              density="compact"
              :icon="isLocalMode(editing) ? 'mdi-filter-check-outline' : 'mdi-server'"
            >
              <span v-if="isLocalMode(editing)">
                代理拉取完整数据后本地评估全部规则:同一字段多条规则真正「且」组合、「匹配任意 (OR)」生效、
                计数与分页始终准确;配合虚拟库缓存预热使用,浏览时不再逐条请求 Emby。
              </span>
              <span v-else>
                规则优先翻译为 Emby 查询参数由服务器执行,开销最小(历史行为)。
                注意:同一字段多条规则仅最后一条生效;「匹配任意 (OR)」不参与执行,详见性能指南。
              </span>
            </v-alert>
          </div>

          <v-radio-group v-model="editing.match_all" inline hide-details density="compact">
            <v-radio label="匹配所有条件 (AND)" :value="true" />
            <v-radio label="匹配任意条件 (OR)" :value="false" />
          </v-radio-group>

          <v-alert
            v-if="!isLocalMode(editing)"
            type="warning"
            variant="tonal"
            density="compact"
            icon="mdi-snail"
          >
            原生模式下「匹配任意 (OR)」不参与执行(一律按 AND 组合);部分操作符会降级为代理端后筛选,可能影响性能,详见性能指南。
          </v-alert>
          <v-alert
            v-else-if="!editing.match_all"
            type="info"
            variant="tonal"
            density="compact"
            icon="mdi-set-center"
          >
            本地全量模式下 OR 已生效:任一规则命中的条目都会保留。
          </v-alert>

          <FilterRuleEditor
            v-for="(rule, index) in editing.rules"
            :key="index"
            :rule="rule"
            :execution-mode="editing.execution_mode ?? 'native'"
            @remove="editing.rules.splice(index, 1)"
          />

          <v-btn prepend-icon="mdi-plus" @click="addRule">添加规则</v-btn>
        </v-card-text>

        <v-card-actions>
          <v-spacer />
          <v-btn variant="text" @click="editorVisible = false">取消</v-btn>
          <v-btn color="primary" variant="flat" :loading="persisting" @click="saveEditing">保存筛选器</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>

    <PerformanceGuideDialog v-model="guideVisible" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import * as filtersApi from '@/api/filters'
import FilterRuleEditor from '@/components/filters/FilterRuleEditor.vue'
import PerformanceGuideDialog from '@/components/filters/PerformanceGuideDialog.vue'
import EmptyState from '@/components/common/EmptyState.vue'
import SectionHeader from '@/components/common/SectionHeader.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useNotification } from '@/composables/useNotification'
import { useConfigStore } from '@/stores/useConfigStore'
import { deepClone } from '@/utils/clone'
import { generateId } from '@/utils/id'
import type { AdvancedFilter } from '@/types/config'

const configStore = useConfigStore()
const { confirm } = useConfirm()
const notify = useNotification()

const guideVisible = ref(false)
const editorVisible = ref(false)
const persisting = ref(false)
const editing = ref<AdvancedFilter | null>(null)
const editingId = computed(() => editing.value?.id ?? null)

const filters = computed(() => configStore.advancedFilters)

const rules = {
  required: (label: string) => (value: unknown) =>
    Boolean(value && String(value).trim()) || `${label}不能为空`,
}

function isLocalMode(filter: AdvancedFilter): boolean {
  return (filter.execution_mode ?? 'native') === 'local'
}

function createEmptyFilter(): AdvancedFilter {
  return {
    id: generateId(),
    name: '',
    match_all: true,
    // 新建默认本地全量:语义完整(同字段 AND / OR 均可),配合缓存预热性能无忧
    execution_mode: 'local',
    rules: [{ field: '', operator: 'equals', value: '', relative_days: null }],
  }
}

function openCreate() {
  editing.value = createEmptyFilter()
  editorVisible.value = true
}

function openEdit(filter: AdvancedFilter) {
  const draft = deepClone(filter)
  // 存量筛选器没有该字段,规范化为 native(后端默认一致),保证切换控件可用
  if (!draft.execution_mode) draft.execution_mode = 'native'
  editing.value = draft
  editorVisible.value = true
}

function addRule() {
  editing.value?.rules.push({ field: '', operator: 'equals', value: '', relative_days: null })
}

async function persistFilters(next: AdvancedFilter[]) {
  persisting.value = true
  try {
    await filtersApi.saveFilters(next)
    if (configStore.config) {
      configStore.config.advanced_filters = next
    }
  } finally {
    persisting.value = false
  }
}

async function saveEditing() {
  if (!editing.value) return
  if (!editing.value.name.trim()) {
    notify.error('请填写筛选器名称')
    return
  }
  if (!editing.value.rules.length) {
    notify.error('至少需要一条规则')
    return
  }

  const next = [...filters.value]
  const index = next.findIndex((item) => item.id === editing.value!.id)
  if (index >= 0) {
    next[index] = editing.value
  } else {
    next.push(editing.value)
  }

  try {
    await persistFilters(next)
    editorVisible.value = false
    notify.success('筛选器已保存')
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '保存失败')
  }
}

async function removeFilter(filter: AdvancedFilter) {
  const yes = await confirm({
    title: '删除筛选器',
    message: `确定删除筛选器「${filter.name}」吗?\n已绑定它的虚拟库将不再应用任何过滤。`,
    confirmText: '删除',
    cancelText: '取消',
    color: 'error',
    icon: 'mdi-delete-alert-outline',
  })
  if (!yes) return
  try {
    await persistFilters(filters.value.filter((item) => item.id !== filter.id))
    notify.success(`「${filter.name}」已删除`)
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '删除失败')
  }
}
</script>
