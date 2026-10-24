<template>
  <div class="evp-page">
    <SectionHeader
      :title="isEditing ? `编辑虚拟库:${draft.name || '…'}` : '新建虚拟库'"
      :subtitle="isEditing ? '修改会立即写入 config.json,并很快同步到客户端' : '填写基本信息并保存后,即可生成封面'"
    >
      <template #actions>
        <v-chip v-if="dirty" color="warning" size="small" variant="tonal" class="mr-1">有未保存修改</v-chip>
        <v-btn variant="text" prepend-icon="mdi-arrow-left" :to="{ name: 'libraries' }">返回列表</v-btn>
        <v-btn
          color="primary"
          variant="flat"
          prepend-icon="mdi-content-save-outline"
          :loading="saving"
          @click="save"
        >
          {{ isEditing ? '保存修改' : '创建虚拟库' }}
        </v-btn>
      </template>
    </SectionHeader>

    <v-form ref="formRef" @submit.prevent>
      <!-- 基本信息 -->
      <v-card>
        <v-card-title class="d-flex align-center ga-2">
          <v-icon icon="mdi-pencil-box-outline" size="small" /> 基本信息
        </v-card-title>
        <v-card-text class="d-flex flex-column ga-4">
          <v-text-field
            v-model="draft.name"
            label="虚拟库名称"
            placeholder="例如:豆瓣高分电影"
            :rules="[rules.required('虚拟库名称')]"
          />

          <v-select
            :model-value="draft.resource_type"
            :items="resourceTypeOptions"
            item-title="title"
            item-value="value"
            label="资源类型"
            :rules="[rules.required('资源类型')]"
            @update:model-value="onResourceTypeChange"
          />

          <ResourceSelect
            v-if="needsResourceSelect"
            v-model="draft.resource_id"
            :resource-type="draft.resource_type"
            label="选择资源"
            :rules="[rules.required('资源')]"
          />

          <v-select
            v-model="draft.advanced_filter_id"
            :items="filterOptions"
            item-title="title"
            item-value="value"
            label="高级筛选器(可选)"
            hint="绑定后,库内容将按筛选规则过滤"
            persistent-hint
            clearable
          />

          <v-switch
            v-model="draft.merge_by_tmdb_id"
            color="primary"
            label="按 TMDB ID 合并"
            hint="同一 TMDB ID 的不同版本(如 4K/1080p)只显示一个条目,可能略增加载时间"
            persistent-hint
          />
        </v-card-text>
      </v-card>

      <!-- RSS 专属设置 -->
      <v-card v-if="draft.resource_type === 'rsshub'">
        <v-card-title class="d-flex align-center ga-2">
          <v-icon icon="mdi-rss" size="small" /> RSS 设置
        </v-card-title>
        <v-card-text>
          <RssSection :draft="draft" />
        </v-card-text>
      </v-card>

      <!-- 封面工作台 -->
      <v-card>
        <v-card-title class="d-flex align-center ga-2">
          <v-icon icon="mdi-palette-outline" size="small" /> 封面生成
        </v-card-title>
        <v-card-text>
          <CoverStudio :library="draft" @generated="onCoverGenerated" />
        </v-card-text>
      </v-card>
    </v-form>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import type { VForm } from 'vuetify/components'

import * as librariesApi from '@/api/libraries'
import CoverStudio from '@/components/libraries/CoverStudio.vue'
import ResourceSelect from '@/components/libraries/ResourceSelect.vue'
import RssSection from '@/components/libraries/RssSection.vue'
import SectionHeader from '@/components/common/SectionHeader.vue'
import { useConfirm } from '@/composables/useConfirm'
import { useNotification } from '@/composables/useNotification'
import { useConfigStore } from '@/stores/useConfigStore'
import { deepClone } from '@/utils/clone'
import { RESOURCE_TYPE_LABELS, createEmptyLibrary, type ResourceType, type VirtualLibrary } from '@/types/config'

const route = useRoute()
const router = useRouter()
const configStore = useConfigStore()
const { confirm } = useConfirm()
const notify = useNotification()

const formRef = ref<VForm | null>(null)
const draft = ref<VirtualLibrary>(createEmptyLibrary())
const initialSnapshot = ref('')
const saving = ref(false)

const isEditing = computed(() => Boolean(route.params.id))
const dirty = computed(() => JSON.stringify(draft.value) !== initialSnapshot.value)

const needsResourceSelect = computed(
  () => draft.value.resource_type !== 'all' && draft.value.resource_type !== 'rsshub',
)

const resourceTypeOptions = (Object.keys(RESOURCE_TYPE_LABELS) as ResourceType[]).map((value) => ({
  value,
  title: RESOURCE_TYPE_LABELS[value],
}))

const filterOptions = computed(() => [
  { title: '无(不使用筛选器)', value: null },
  ...configStore.advancedFilters.map((filter) => ({ title: filter.name, value: filter.id })),
])

const rules = {
  required: (label: string) => (value: unknown) =>
    Boolean(value !== null && value !== undefined && String(value).trim()) || `${label}不能为空`,
}

function resetDraft(source: VirtualLibrary) {
  draft.value = deepClone(source)
  initialSnapshot.value = JSON.stringify(draft.value)
}

function onResourceTypeChange(type: ResourceType) {
  draft.value.resource_type = type
  // 切换类型后原资源 ID 必然失效,清空并让 ResourceSelect 重置
  draft.value.resource_id = null
}

function onCoverGenerated({ imageTag, coverFormat }: { imageTag: string; coverFormat: string }) {
  draft.value.image_tag = imageTag
  draft.value.cover_format = coverFormat
  initialSnapshot.value = JSON.stringify(draft.value) // 封面由后端立即持久化,不算未保存修改
}

async function save() {
  const { valid } = await formRef.value!.validate()
  if (!valid) {
    notify.error('请先修正表单中标红的必填项')
    return
  }

  saving.value = true
  try {
    const payload = draft.value
    const saved = isEditing.value
      ? await librariesApi.updateLibrary(payload.id, payload)
      : await librariesApi.createLibrary(payload)

    await configStore.reload()
    resetDraft(saved)
    notify.success(isEditing.value ? '修改已保存' : `虚拟库「${saved.name}」已创建`)

    // 新建后切换到编辑模式,便于继续生成封面
    if (!isEditing.value && saved.id) {
      await router.replace({ name: 'library-edit', params: { id: saved.id } })
    }
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '保存失败')
  } finally {
    saving.value = false
  }
}

async function loadDraft() {
  await configStore.load()
  const id = route.params.id as string | undefined
  if (id) {
    const existing = configStore.getLibraryById(id)
    if (!existing) {
      notify.error('未找到该虚拟库,可能已被删除')
      await router.replace({ name: 'libraries' })
      return
    }
    resetDraft(existing)
  } else {
    resetDraft(createEmptyLibrary())
  }
}

// 未保存修改的离开保护
async function guardUnsaved(): Promise<boolean> {
  if (!dirty.value) return true
  return confirm({
    title: '放弃修改?',
    message: '当前页面有未保存的修改,离开后将丢失。',
    confirmText: '放弃并离开',
    cancelText: '留在页面',
    color: 'warning',
    icon: 'mdi-exit-to-app',
  })
}

onBeforeRouteLeave(guardUnsaved)
onBeforeRouteUpdate(guardUnsaved)

onMounted(loadDraft)
</script>
