<template>
  <v-text-field
    :model-value="modelValue"
    :label="label"
    :type="visible ? 'text' : 'password'"
    :placeholder="placeholder"
    :rules="rules"
    :disabled="disabled"
    :hint="hint"
    :persistent-hint="Boolean(hint)"
    :append-inner-icon="visible ? 'mdi-eye-off-outline' : 'mdi-eye-outline'"
    @update:model-value="emit('update:modelValue', String($event ?? ''))"
    @click:append-inner="visible = !visible"
  />
</template>

<script setup lang="ts">
import { ref } from 'vue'

defineProps<{
  modelValue: string | null
  label: string
  placeholder?: string
  hint?: string
  rules?: ((value: string) => true | string)[]
  disabled?: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: string): void
}>()

const visible = ref(false)
</script>
