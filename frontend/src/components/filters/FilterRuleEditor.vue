<template>
  <div class="rule-row pa-3 rounded-lg border-thin mb-2">
    <v-row dense class="align-center">
      <v-col cols="12" sm="4">
        <v-select
          v-model="rule.field"
          :items="FIELD_OPTIONS"
          label="字段"
          hide-details
        />
      </v-col>

      <v-col cols="12" sm="3">
        <v-select
          v-model="rule.operator"
          :items="OPERATOR_OPTIONS"
          label="操作符"
          hide-details
        />
      </v-col>

      <v-col cols="12" sm="5">
        <!-- 为空/不为空:无需值 -->
        <div v-if="isEmptyOperator" class="text-caption text-medium-emphasis py-2">
          该操作符不需要填写值
        </div>

        <!-- 日期字段:绝对日期 + 最近N天 二选一 -->
        <template v-else-if="isDateField">
          <div class="d-flex align-center ga-2">
            <v-text-field
              :model-value="rule.value ?? ''"
              label="日期"
              type="date"
              hide-details
              :disabled="Boolean(rule.relative_days)"
              @update:model-value="rule.value = ($event || null) as string | null"
            />
            <v-text-field
              :model-value="rule.relative_days === null ? '' : String(rule.relative_days)"
              class="flex-shrink-1"
              style="max-width: 150px"
              label="最近N天"
              type="number"
              min="1"
              step="1"
              suffix="天内"
              hide-details
              @update:model-value="setRelativeDays"
            />
            <v-btn
              v-if="rule.relative_days !== null"
              icon="mdi-close-circle-outline"
              size="x-small"
              variant="text"
              title="改用固定日期"
              @click="clearRelativeDays"
            />
          </div>
        </template>

        <!-- 普通字段 -->
        <v-text-field
          v-else
          v-model="rule.value"
          label="值"
          hide-details
          placeholder="匹配值"
        />
      </v-col>

      <v-col cols="12" class="d-flex justify-end">
        <v-btn icon="mdi-delete-outline" size="x-small" variant="text" color="error" @click="emit('remove')" />
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { AdvancedFilterRule } from '@/types/config'

/** 编辑页草稿对象;本组件直接修改传入的 rule 字段(保存时才提交) */
const props = defineProps<{
  rule: AdvancedFilterRule
}>()

const emit = defineEmits<{
  (e: 'remove'): void
}>()

export interface FieldOption {
  value: string
  title: string
}

const FIELD_OPTIONS: FieldOption[] = [
  { value: 'CommunityRating', title: '社区评分' },
  { value: 'CriticRating', title: '影评人评分' },
  { value: 'OfficialRating', title: '官方分级' },
  { value: 'ProductionYear', title: '发行年份' },
  { value: 'PremiereDate', title: '首播日期' },
  { value: 'DateCreated', title: '添加日期' },
  { value: 'Genres', title: '类型' },
  { value: 'Tags', title: '标签' },
  { value: 'Studios', title: '工作室' },
  { value: 'VideoRange', title: '视频范围' },
  { value: 'Container', title: '文件容器' },
  { value: 'NameStartsWith', title: '名称以…开头' },
  { value: 'SeriesStatus', title: '剧集状态' },
  { value: 'IsMovie', title: '是否为电影' },
  { value: 'IsSeries', title: '是否为剧集' },
  { value: 'IsPlayed', title: '已播放' },
  { value: 'IsUnplayed', title: '未播放' },
  { value: 'HasSubtitles', title: '有字幕' },
  { value: 'HasOfficialRating', title: '有官方评级' },
  { value: 'ProviderIds.Tmdb', title: '拥有 TMDB ID' },
  { value: 'ProviderIds.Imdb', title: '拥有 IMDB ID' },
  { value: 'Name', title: '名称' },
]

const OPERATOR_OPTIONS: { value: string; title: string }[] = [
  { value: 'equals', title: '等于' },
  { value: 'not_equals', title: '不等于' },
  { value: 'contains', title: '包含' },
  { value: 'not_contains', title: '不包含' },
  { value: 'greater_than', title: '大于' },
  { value: 'less_than', title: '小于' },
  { value: 'is_empty', title: '为空' },
  { value: 'is_not_empty', title: '不为空' },
]

const DATE_FIELDS = ['PremiereDate', 'DateCreated']

const isEmptyOperator = computed(() => ['is_empty', 'is_not_empty'].includes(props.rule.operator))
const isDateField = computed(() => DATE_FIELDS.includes(props.rule.field))

/** 设置"最近N天":清空固定日期,并把操作符强制为大于(与后端翻译逻辑一致) */
function setRelativeDays(value: unknown) {
  const parsed = Math.floor(Number(value))
  props.rule.relative_days = Number.isFinite(parsed) && parsed > 0 ? parsed : null
  if (props.rule.relative_days !== null) {
    props.rule.value = null
    props.rule.operator = 'greater_than'
  }
}

function clearRelativeDays() {
  props.rule.relative_days = null
}
</script>

<style scoped>
.rule-row {
  background: rgba(var(--v-theme-surface-variant), 0.35);
}
</style>
