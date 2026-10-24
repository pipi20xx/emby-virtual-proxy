<template>
  <div class="d-flex flex-column align-center justify-center text-center pa-8 text-medium-emphasis">
    <v-icon :icon="icon" size="48" class="mb-3" />
    <div class="text-subtitle-1">{{ title }}</div>
    <div v-if="hint" class="text-body-2 mt-1" style="max-width: 420px">{{ hint }}</div>
    <div v-if="$slots.default" class="mt-4">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
withDefaults(
  defineProps<{
    icon?: string
    title: string
    hint?: string
  }>(),
  { icon: 'mdi-inbox-outline' },
)
</script>
