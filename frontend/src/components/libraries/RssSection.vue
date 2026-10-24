<template>
  <div class="d-flex flex-column ga-4">
    <v-text-field
      v-model="draft.rsshub_url"
      label="RSSHUB 链接"
      placeholder="https://rsshub.app/douban/people/xxx/wish"
      :rules="[rules.required('RSSHUB 链接')]"
      hint="RSSHub 生成的豆瓣/Bangumi 订阅链接"
      persistent-hint
    />

    <v-select
      v-model="draft.rss_type"
      :items="[
        { title: '豆瓣', value: 'douban' },
        { title: 'Bangumi', value: 'bangumi' },
      ]"
      item-title="title"
      item-value="value"
      label="RSS 类型"
      :rules="[rules.required('RSS 类型')]"
    />

    <v-switch
      v-model="draft.enable_retention"
      color="primary"
      label="开启数据保留"
      hint="开启后,已从 RSS 源消失的条目仍会在本地保留一段时间;关闭则每次刷新完全同步源内容"
      persistent-hint
    />

    <v-text-field
      v-if="draft.enable_retention"
      :model-value="String(draft.retention_days)"
      label="保留天数"
      type="number"
      min="0"
      step="1"
      suffix="天"
      hint="条目在本地库中的最长存活时间,0 表示永久保留"
      persistent-hint
      @update:model-value="setRetentionDays"
    />

    <v-divider />

    <v-text-field
      v-model="draft.fallback_tmdb_id"
      label="追加 TMDB ID(可选)"
      placeholder="例如:1078602"
      hint="无条件追加到 RSS 解析结果中的指定影视项目,保证源为空时库里仍有内容"
      persistent-hint
    />

    <v-select
      v-if="draft.fallback_tmdb_id"
      v-model="draft.fallback_tmdb_type"
      :items="[
        { title: '电影', value: 'Movie' },
        { title: '剧集', value: 'TV' },
      ]"
      item-title="title"
      item-value="value"
      label="追加项目的媒体类型"
      :rules="[rules.required('追加项目的媒体类型')]"
    />
  </div>
</template>

<script setup lang="ts">
import type { VirtualLibrary } from '@/types/config'

const props = defineProps<{
  /** 编辑页的本地草稿对象;本组件直接修改其 RSS 相关字段(草稿保存时才提交) */
  draft: VirtualLibrary
}>()

const rules = {
  required: (label: string) => (value: unknown) =>
    Boolean(value && String(value).trim()) || `${label}不能为空`,
}

function setRetentionDays(value: unknown) {
  props.draft.retention_days = Math.max(0, Math.floor(Number(value) || 0))
}
</script>
