<template>
  <v-row>
    <!-- 封面预览 -->
    <v-col cols="12" md="7">
      <div class="evp-cover rounded-lg border-thin d-flex align-center justify-center overflow-hidden">
        <v-img
          v-if="previewUrl"
          :key="previewUrl"
          :src="previewUrl"
          aspect-ratio="16/9"
          cover
        >
          <template #placeholder>
            <div class="d-flex align-center justify-center fill-height">
              <v-progress-circular indeterminate size="24" width="2" />
            </div>
          </template>
        </v-img>
        <div v-else class="d-flex flex-column align-center text-medium-emphasis pa-6 text-center">
          <v-icon icon="mdi-image-plus-outline" size="40" class="mb-2" />
          <span class="text-body-2">暂无封面</span>
          <span class="text-caption mt-1">保存虚拟库后即可在此生成风格化海报</span>
        </div>
      </div>

      <v-alert class="mt-3" density="compact" type="info" variant="tonal">
        封面将从虚拟库内容中随机选取素材自动合成。
      </v-alert>
      <v-alert class="mt-2" density="compact" type="warning" variant="tonal">
        生成依赖缓存数据:请先在 Emby 客户端访问一次该虚拟库,再点生成。
      </v-alert>
    </v-col>

    <!-- 生成参数 -->
    <v-col cols="12" md="5">
      <div class="d-flex flex-column ga-4">
        <v-text-field
          v-model="titleZh"
          :label="`封面中文标题(留空使用「${library.name || '库名'}」)`"
          clearable
        />
        <v-text-field v-model="titleEn" label="封面英文标题(装饰文字)" clearable />

        <v-select
          v-model="selectedStyle"
          :items="COVER_STYLES"
          item-title="title"
          item-value="value"
          label="封面样式"
        />

        <v-select
          v-if="isAnimatedStyle"
          v-model="animationFormat"
          :items="ANIMATION_FORMATS"
          item-title="title"
          item-value="value"
          label="动图格式"
          hint="GIF 兼容性最好,WebP 体积最小,APNG 画质最好"
          persistent-hint
        />

        <v-text-field
          v-model="library.cover_custom_zh_font_path"
          label="库级中文字体路径"
          placeholder="留空使用全局/内置字体"
        />
        <v-text-field
          v-model="library.cover_custom_en_font_path"
          label="库级英文字体路径"
          placeholder="留空使用全局/内置字体"
        />
        <v-text-field
          v-model="library.cover_custom_image_path"
          label="库级自定义图片目录"
          placeholder="留空则从虚拟库内容抓取素材"
        />

        <!-- 临时素材上传 -->
        <div>
          <v-file-input
            :model-value="pendingFiles"
            multiple
            accept="image/*"
            label="上传临时素材图片(可选,最多9张)"
            prepend-icon=""
            prepend-inner-icon="mdi-image-multiple-outline"
            :loading="uploading"
            @update:model-value="handleFilesSelected"
          />

          <div v-if="uploads.length" class="d-flex flex-wrap ga-2 mt-2">
            <div v-for="(upload, index) in uploads" :key="upload.path" class="upload-thumb">
              <v-img :src="upload.preview" cover width="56" height="56" rounded="lg" />
              <v-btn
                icon="mdi-close-circle"
                size="x-small"
                variant="text"
                color="error"
                class="thumb-remove"
                @click="removeUpload(index)"
              />
            </div>
          </div>
        </div>

        <v-btn
          color="primary"
          variant="flat"
          size="large"
          :prepend-icon="library.image_tag ? 'mdi-refresh' : 'mdi-palette-outline'"
          :loading="generating"
          :disabled="!library.id"
          @click="generate"
        >
          {{ library.image_tag ? '重新生成封面' : '生成封面' }}
        </v-btn>
        <div v-if="!library.id" class="text-caption text-warning">
          新建虚拟库需要先保存,才能生成封面。
        </div>
      </div>
    </v-col>
  </v-row>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'

import * as coversApi from '@/api/covers'
import { useNotification } from '@/composables/useNotification'
import { useConfigStore } from '@/stores/useConfigStore'
import { ANIMATION_FORMATS, COVER_STYLES, type VirtualLibrary } from '@/types/config'

const MAX_MATERIALS = 9

const props = defineProps<{
  /** 编辑页草稿;字体/图片目录字段直接写回草稿,封面 tag 通过事件通知父级 */
  library: VirtualLibrary
}>()

const emit = defineEmits<{
  (e: 'generated', payload: { imageTag: string; coverFormat: string }): void
}>()

const configStore = useConfigStore()
const notify = useNotification()

const titleZh = ref('')
const titleEn = ref('')
const selectedStyle = ref(configStore.config?.default_cover_style || 'style_multi_1')
const animationFormat = ref('gif')
const generating = ref(false)
const uploading = ref(false)

const isAnimatedStyle = computed(
  () => COVER_STYLES.find((style) => style.value === selectedStyle.value)?.animated === true,
)

interface UploadedMaterial {
  path: string
  preview: string
  name: string
}

const uploads = ref<UploadedMaterial[]>([])
const pendingFiles = ref<File[] | null>(null)
/** 生成成功后优先使用后端返回的预览地址(带真实扩展名) */
const previewOverride = ref<string | null>(null)

const previewUrl = computed(() => {
  if (previewOverride.value) return previewOverride.value
  if (props.library.id && props.library.image_tag) {
    const ext = props.library.cover_format || 'jpg'
    return `/covers/${props.library.id}.${ext}?t=${props.library.image_tag}`
  }
  return null
})

function readPreview(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(new Error('读取图片失败'))
    reader.readAsDataURL(file)
  })
}

async function handleFilesSelected(files: File | File[] | null) {
  pendingFiles.value = null
  const selected = files ? (Array.isArray(files) ? files : [files]) : []
  if (!selected.length) return

  uploading.value = true
  try {
    for (const file of selected) {
      if (uploads.value.length >= MAX_MATERIALS) {
        notify.warning('最多上传 9 张素材图片,多余部分已忽略')
        break
      }
      const { path } = await coversApi.uploadTempImage(file)
      const preview = await readPreview(file)
      uploads.value.push({ path, preview, name: file.name })
    }
    notify.success('素材上传完成,生成封面时将优先使用这些图片')
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '素材上传失败')
  } finally {
    uploading.value = false
  }
}

function removeUpload(index: number) {
  uploads.value.splice(index, 1)
}

async function generate() {
  if (!props.library.id) return
  generating.value = true
  try {
    const result = await coversApi.generateCover({
      library_id: props.library.id,
      title_zh: titleZh.value.trim() || props.library.name,
      title_en: titleEn.value.trim() || null,
      style_name: selectedStyle.value,
      temp_image_paths: uploads.value.length ? uploads.value.map((item) => item.path) : null,
      animation_format: isAnimatedStyle.value ? animationFormat.value : null,
    })
    // 后端返回带真实扩展名的预览地址;image_tag/cover_format 变化驱动预览刷新(时间戳防缓存)
    previewOverride.value = result.image_url
    const coverFormat = result.image_url.split('?')[0].split('.').pop() || 'jpg'
    emit('generated', { imageTag: result.image_tag, coverFormat })
    notify.success('封面生成成功')
  } catch (error) {
    notify.error(error instanceof Error ? error.message : '封面生成失败,详见后端日志')
  } finally {
    generating.value = false
  }
}
</script>

<style scoped>
.upload-thumb {
  position: relative;
}

.thumb-remove {
  position: absolute;
  top: -8px;
  right: -8px;
}
</style>
