<template>
  <div class="evp-page">
    <SectionHeader
      title="主页布局"
      subtitle="拖拽或点击箭头按钮,调整各媒体库在客户端首页『我的媒体』中的显示与顺序"
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
              <template #item="{ element, index }">
                <div class="lib-item pa-1 pl-2 d-flex align-center ga-1 rounded-lg">
                  <v-icon class="drag-handle" icon="mdi-drag-vertical" size="small" />
                  <span class="order-index text-caption text-medium-emphasis">{{ index + 1 }}</span>
                  <v-chip :color="chipColor(element)" size="small" variant="tonal">
                    {{ chipText(element) }}
                  </v-chip>
                  <span class="text-body-2 text-truncate flex-grow-1">{{ element.name }}</span>
                  <div class="lib-actions d-flex align-center">
                    <v-btn
                      class="lib-action"
                      icon="mdi-arrow-up"
                      size="x-small"
                      variant="text"
                      aria-label="上移"
                      :disabled="index === 0"
                      @click="moveDisplayed(index, index - 1)"
                    />
                    <v-btn
                      class="lib-action"
                      icon="mdi-arrow-down"
                      size="x-small"
                      variant="text"
                      aria-label="下移"
                      :disabled="index === displayed.length - 1"
                      @click="moveDisplayed(index, index + 1)"
                    />
                    <v-btn
                      class="lib-action"
                      icon="mdi-eye-off-outline"
                      size="x-small"
                      variant="text"
                      aria-label="隐藏"
                      @click="hideLibrary(element)"
                    />
                  </div>
                </div>
              </template>
            </draggable>
            <div v-if="!displayed.length" class="text-caption text-medium-emphasis text-center py-6">
              从右侧拖入库,或点击右侧库的『显示』按钮
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
                <div class="lib-item pa-1 pl-2 d-flex align-center ga-1 rounded-lg">
                  <v-icon class="drag-handle" icon="mdi-drag-vertical" size="small" />
                  <v-chip :color="chipColor(element)" size="small" variant="tonal">
                    {{ chipText(element) }}
                  </v-chip>
                  <span class="text-body-2 text-truncate flex-grow-1">{{ element.name }}</span>
                  <v-btn
                    class="lib-action"
                    icon="mdi-eye-outline"
                    size="x-small"
                    variant="text"
                    aria-label="显示"
                    @click="showLibrary(element)"
                  />
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

function moveDisplayed(from: number, to: number) {
  if (to < 0 || to >= displayed.value.length || from === to) return
  const [item] = displayed.value.splice(from, 1)
  displayed.value.splice(to, 0, item)
}

function showLibrary(element: LibraryBrief) {
  const index = undisplayed.value.findIndex((item) => item.id === element.id)
  if (index < 0) return
  undisplayed.value.splice(index, 1)
  displayed.value.push(element)
}

function hideLibrary(element: LibraryBrief) {
  const index = displayed.value.findIndex((item) => item.id === element.id)
  if (index < 0) return
  displayed.value.splice(index, 1)
  undisplayed.value.push(element)
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
  transition: background-color 0.15s ease;
}

.lib-item:hover {
  background: rgba(var(--v-theme-surface-variant), 0.65);
}

.drag-handle {
  cursor: grab;
  padding: 6px 4px;
  margin: -6px -4px;
  color: rgb(var(--v-theme-on-surface-variant));
}

.drag-handle:active {
  cursor: grabbing;
}

.order-index {
  width: 18px;
  text-align: center;
  flex: none;
}

/* 拖拽视觉反馈:被拖起的项半透明,原位置留虚线占位 */
.drag-ghost {
  opacity: 0.45;
  border: 1px dashed rgb(var(--v-theme-primary));
  background: rgba(var(--v-theme-primary), 0.06);
}

.drag-chosen {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.25);
}
</style>
