<template>
  <div class="evp-page">
    <SectionHeader
      title="主页布局"
      subtitle="拖拽调整各媒体库在客户端首页『我的媒体』中的显示与顺序"
    >
      <template #actions>
        <v-chip v-if="dirty" color="warning" size="small" variant="tonal" class="mr-1">有未保存修改</v-chip>
        <v-btn
          color="primary"
          variant="flat"
          prepend-icon="mdi-content-save-outline"
          :loading="saving"
          @click="save"
        >
          保存布局
        </v-btn>
      </template>
    </SectionHeader>

    <v-row v-if="ready">
      <v-col cols="12" md="6">
        <v-card class="fill-height">
          <v-card-title class="d-flex align-center ga-2">
            <v-icon icon="mdi-eye-outline" size="small" /> 已显示(按顺序)
          </v-card-title>
          <v-card-text>
            <draggable
              v-model="displayed"
              item-key="id"
              v-bind="dragOptions"
              class="d-flex flex-column ga-2"
            >
              <template #item="{ element }">
                <div class="lib-item pa-2 d-flex align-center ga-2 rounded-lg">
                  <v-icon class="drag-handle" icon="mdi-drag-vertical" size="small" />
                  <span class="text-caption text-medium-emphasis">{{ indexLabel(element) }}</span>
                  <v-chip :color="chipColor(element)" size="small" variant="tonal">
                    {{ chipText(element) }}
                  </v-chip>
                  <span class="text-body-2 text-truncate">{{ element.name }}</span>
                </div>
              </template>
            </draggable>
            <div v-if="!displayed.length" class="text-caption text-medium-emphasis text-center py-6">
              从右侧拖入库,或直接把库拖到此处调整顺序
            </div>
          </v-card-text>
        </v-card>
      </v-col>

      <v-col cols="12" md="6">
        <v-card class="fill-height">
          <v-card-title class="d-flex align-center ga-2">
            <v-icon icon="mdi-eye-off-outline" size="small" /> 未显示
          </v-card-title>
          <v-card-text>
            <draggable
              v-model="undisplayed"
              item-key="id"
              v-bind="dragOptions"
              class="d-flex flex-column ga-2"
            >
              <template #item="{ element }">
                <div class="lib-item pa-2 d-flex align-center ga-2 rounded-lg">
                  <v-icon class="drag-handle" icon="mdi-drag-vertical" size="small" />
                  <v-chip :color="chipColor(element)" size="small" variant="tonal">
                    {{ chipText(element) }}
                  </v-chip>
                  <span class="text-body-2 text-truncate">{{ element.name }}</span>
                </div>
              </template>
            </draggable>
            <div v-if="!undisplayed.length" class="text-caption text-medium-emphasis text-center py-6">
              所有库都已显示
            </div>
            <div class="text-caption text-medium-emphasis mt-3">
              未显示的库不会出现在首页『我的媒体』中(仍可直接访问)。
            </div>
          </v-card-text>
        </v-card>
      </v-col>
    </v-row>

    <v-card v-else>
      <v-progress-linear indeterminate color="primary" />
    </v-card>

    <v-alert type="info" variant="tonal" density="compact" icon="mdi-information-outline">
      保存后 Emby 4.10+ 客户端首页将按此布局展示;代理存在约 5 分钟 API 缓存,未立即生效请稍候或重启代理服务。
    </v-alert>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import draggable from 'vuedraggable'

import * as librariesApi from '@/api/libraries'
import { useNotification } from '@/composables/useNotification'
import { useDragSort } from '@/composables/useDragSort'
import SectionHeader from '@/components/common/SectionHeader.vue'
import { useConfigStore } from '@/stores/useConfigStore'
import { useEmbyDataStore } from '@/stores/useEmbyDataStore'
import type { LibraryBrief } from '@/types/api'

const configStore = useConfigStore()
const embyStore = useEmbyDataStore()
const notify = useNotification()
const dragOptions = useDragSort()

const ready = ref(false)
const saving = ref(false)
const displayed = ref<LibraryBrief[]>([])
const undisplayed = ref<LibraryBrief[]>([])
const initialSnapshot = ref('')

const dirty = computed(() => JSON.stringify(idsSnapshot()) !== initialSnapshot.value)

function idsSnapshot(): [string[], string[]] {
  return [displayed.value.map((item) => item.id), undisplayed.value.map((item) => item.id)]
}

function indexLabel(element: LibraryBrief): string {
  const index = displayed.value.findIndex((item) => item.id === element.id)
  return index >= 0 ? String(index + 1) : ''
}

function chipText(element: LibraryBrief): string {
  return element.type === 'virtual' ? '虚拟库' : '真实库'
}

function chipColor(element: LibraryBrief): string {
  return element.type === 'virtual' ? 'success' : 'primary'
}

async function initialize() {
  await Promise.all([configStore.load(), embyStore.load()])

  const briefsById = new Map(embyStore.allLibraries.map((lib) => [lib.id, lib]))
  const order = configStore.config?.display_order ?? []
  displayed.value = order
    .map((id) => briefsById.get(id))
    .filter((item): item is LibraryBrief => Boolean(item))

  const shownIds = new Set(displayed.value.map((item) => item.id))
  undisplayed.value = embyStore.allLibraries.filter((item) => !shownIds.has(item.id))

  initialSnapshot.value = JSON.stringify(idsSnapshot())
  ready.value = true
}

async function save() {
  saving.value = true
  try {
    await librariesApi.saveDisplayOrder(displayed.value.map((item) => item.id))
    if (configStore.config) {
      configStore.config.display_order = displayed.value.map((item) => item.id)
    }
    initialSnapshot.value = JSON.stringify(idsSnapshot())
    notify.success('主页布局已保存')
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '保存布局失败')
  } finally {
    saving.value = false
  }
}

onMounted(initialize)
</script>

<style scoped>
.lib-item {
  background: rgba(var(--v-theme-surface-variant), 0.4);
  cursor: grab;
}

.lib-item:active {
  cursor: grabbing;
}

.drag-handle {
  cursor: grab;
  color: rgb(var(--v-theme-on-surface-variant));
}
</style>
