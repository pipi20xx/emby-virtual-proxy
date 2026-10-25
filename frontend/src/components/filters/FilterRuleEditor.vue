<template>
  <div class="rule-row pa-3 rounded-lg border-thin mb-2">
    <v-row dense class="align-center">
      <v-col cols="12" sm="4">
        <v-select
          v-model="rule.field"
          :items="fieldSelectItems"
          label="字段"
          hide-details
        />
      </v-col>

      <v-col cols="12" sm="3">
        <v-select
          v-model="rule.operator"
          :items="operatorOptions"
          item-title="title"
          item-value="value"
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

        <!-- 普通字段:按字段元数据的值控件渲染(枚举严格限制 / 数字限制范围 / 建议值 / 自由文本) -->
        <v-select
          v-else-if="enumOptions"
          v-model="rule.value"
          :items="enumOptions"
          item-title="title"
          item-value="value"
          label="值"
          hide-details
        />
        <v-text-field
          v-else-if="numberControl"
          :model-value="rule.value ?? ''"
          type="number"
          :min="numberControl.min"
          :max="numberControl.max"
          :step="numberControl.step"
          label="值"
          hide-details
          @update:model-value="rule.value = ($event || null) as string | null"
        />
        <v-combobox
          v-else-if="suggestOptions"
          v-model="rule.value"
          :items="suggestOptions"
          label="值"
          hide-details
        />
        <v-text-field
          v-else
          v-model="rule.value"
          label="值"
          hide-details
          placeholder="匹配值"
        />
      </v-col>

      <v-col cols="12" class="d-flex align-center ga-2">
        <span class="text-caption text-medium-emphasis flex-grow-1">{{ fieldHint }}</span>
        <v-btn icon="mdi-delete-outline" size="x-small" variant="text" color="error" @click="emit('remove')" />
      </v-col>
    </v-row>
  </div>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'

import type { AdvancedFilterRule } from '@/types/config'

import { FIELD_META, OPERATOR_TITLES, buildFieldHint, buildFieldSelectItems } from './fieldMeta'

/** 编辑页草稿对象;本组件直接修改传入的 rule 字段(保存时才提交) */
const props = defineProps<{
  rule: AdvancedFilterRule
  /** 筛选器执行模式:local 下操作符不再区分原生/后筛选 */
  executionMode?: 'native' | 'local'
}>()

const emit = defineEmits<{
  (e: 'remove'): void
}>()

const fieldSelectItems = buildFieldSelectItems()

const isLocalMode = computed(() => (props.executionMode ?? 'native') === 'local')

const fieldHint = computed(() => buildFieldHint(props.rule.field, props.executionMode ?? 'native'))

const fieldMeta = computed(() => FIELD_META[props.rule.field])

const enumOptions = computed(() => {
  const control = fieldMeta.value?.valueControl
  return control?.kind === 'enum' ? control.options : null
})

const suggestOptions = computed(() => {
  const control = fieldMeta.value?.valueControl
  return control?.kind === 'suggest' ? control.options : null
})

const numberControl = computed(() => {
  const control = fieldMeta.value?.valueControl
  return control?.kind === 'number' ? control : null
})

/** 操作符下拉:按当前字段过滤;原生模式下原生/后筛选分组展示,本地全量模式下全部平铺 */
const operatorOptions = computed<Array<Record<string, unknown>>>(() => {
  const meta = fieldMeta.value
  const native = meta?.ops ?? []
  const post = meta?.postOps ?? []

  if (!native.length && !post.length) {
    // 未选择字段:给全量操作符,不分组
    return Object.entries(OPERATOR_TITLES).map(([value, title]) => ({ value, title }))
  }

  if (isLocalMode.value) {
    // 本地全量模式:所有操作符同样由代理评估,无快慢之分
    const all = [...native, ...post]
    return all.map((value) => ({ value, title: OPERATOR_TITLES[value] ?? value }))
  }

  const items: Array<Record<string, unknown>> = []
  if (native.length) {
    items.push({ type: 'subheader', title: '原生执行(最快)' })
    native.forEach((value) => items.push({ value, title: OPERATOR_TITLES[value] ?? value }))
  }
  if (post.length) {
    if (native.length) items.push({ type: 'divider' })
    items.push({ type: 'subheader', title: '代理端后筛选(慢)' })
    post.forEach((value) => items.push({ value, title: OPERATOR_TITLES[value] ?? value }))
  }
  return items
})

/** 切换字段后:
 * 1. 操作符不在新字段允许列表时,重置为该字段的第一个原生操作符
 * 2. 值不在新字段的枚举里则清空,避免残留非法值 */
watch(
  () => props.rule.field,
  () => {
    const meta = fieldMeta.value
    const allowed = new Set([...(meta?.ops ?? []), ...(meta?.postOps ?? [])])
    if (allowed.size && !allowed.has(props.rule.operator)) {
      props.rule.operator = meta?.ops?.[0] ?? [...allowed][0]
    }
    const options = enumOptions.value
    if (options && !options.some((option) => option.value === props.rule.value)) {
      props.rule.value = null
    }
  },
)

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
