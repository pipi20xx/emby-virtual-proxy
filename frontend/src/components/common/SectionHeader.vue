<template>
  <div class="d-flex flex-wrap align-center ga-2 mb-4">
    <div>
      <div class="text-h6 font-weight-bold">{{ title }}</div>
      <div v-if="subtitle" class="text-body-2 text-medium-emphasis">{{ subtitle }}</div>
    </div>
    <v-spacer />
    <slot name="actions" />
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string
  subtitle?: string
}>()
</script>
