<template>
  <v-autocomplete
    :model-value="modelValue"
    :items="items"
    :loading="loading"
    :label="label"
    :rules="rules"
    :disabled="disabled"
    item-title="title"
    item-value="value"
    no-filter
    clearable
    hide-no-data
    placeholder="输入关键词搜索,下拉到底自动加载更多"
    @update:model-value="emit('update:modelValue', ($event ?? null) as string | null)"
    v-model:search="search"
  >
    <template #no-data>
      <div class="text-caption text-medium-emphasis text-center py-2">输入关键词开始搜索</div>
    </template>

    <template #append-item>
      <div ref="sentinelRef" class="text-caption text-medium-emphasis text-center py-2">
        <v-progress-circular v-if="loading" indeterminate size="14" width="2" class="mr-1" />
        {{ footerText }}
      </div>
    </template>
  </v-autocomplete>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { watchDebounced } from '@vueuse/core'

import * as embyApi from '@/api/emby'
import { useInfiniteScroll } from '@/composables/useInfiniteScroll'
import { useEmbyDataStore } from '@/stores/useEmbyDataStore'
import { RESOURCE_PLURAL_KEY, type ResourceType } from '@/types/config'

const PAGE_SIZE = 100

const props = defineProps<{
  modelValue: string | null
  resourceType: ResourceType
  label?: string
  rules?: ((value: string) => true | string)[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string | null): void
}>()

const embyStore = useEmbyDataStore()

interface Option {
  title: string
  value: string
}

const items = ref<Option[]>([])
const search = ref('')
const page = ref(1)
const loading = ref(false)
const hasMore = ref(false)

const footerText = computed(() => {
  if (loading.value) return '加载中…'
  if (hasMore.value) return '继续滚动加载更多…'
  return items.value.length ? '没有更多了' : ''
})

/** 把已选中的资源补进列表,保证编辑回显永远可见 */
async function ensureSelectedVisible() {
  const id = props.modelValue
  if (!id || items.value.some((item) => item.value === id)) return

  if (props.resourceType === 'person') {
    const name = await embyStore.resolvePersonName(id)
    items.value.unshift({ title: name || `ID: ${id}`, value: id })
    return
  }

  const item = embyStore.findResourceName(props.resourceType, id)
  items.value.unshift({ title: item?.name ?? `未知ID: ${id}`, value: id })
}

function appendOptions(newOptions: Option[]) {
  const existing = new Set(items.value.map((item) => item.value))
  items.value.push(...newOptions.filter((option) => !existing.has(option.value)))
}

async function loadPage() {
  if (loading.value) return
  loading.value = true
  try {
    if (props.resourceType === 'person') {
      const results = await embyApi.searchPersons(search.value, page.value)
      for (const person of results) {
        embyStore.personNameCache[person.id] = person.name
      }
      appendOptions(results.map((person) => ({ title: person.name || person.id, value: person.id })))
      hasMore.value = results.length === PAGE_SIZE
    } else {
      const key = RESOURCE_PLURAL_KEY[props.resourceType]
      const source = key ? (embyStore.classifications[key] ?? []) : []
      const query = search.value.trim().toLowerCase()
      const filtered = query
        ? source.filter((item) => item.name.toLowerCase().includes(query))
        : source
      const slice = filtered.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE)
      appendOptions(slice.map((item) => ({ title: item.name || item.id, value: item.id })))
      hasMore.value = page.value * PAGE_SIZE < filtered.length
    }
    page.value += 1
  } catch {
    hasMore.value = false
  } finally {
    loading.value = false
  }
}

function resetAndLoad() {
  items.value = []
  page.value = 1
  hasMore.value = false
  void ensureSelectedVisible().then(() => loadPage())
}

// 下拉底部的哨兵元素进入视口时加载下一页
const { sentinelRef } = useInfiniteScroll(() => loadPage(), { hasMore, loading })

// 切换资源类型时重置
watch(
  () => props.resourceType,
  () => {
    search.value = ''
    resetAndLoad()
  },
)

// 搜索防抖
watchDebounced(
  search,
  () => {
    resetAndLoad()
  },
  { debounce: 300 },
)

onMounted(resetAndLoad)
</script>
