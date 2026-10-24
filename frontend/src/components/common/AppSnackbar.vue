<template>
  <v-snackbar
    v-model="ui.snackbar.visible"
    :color="ui.snackbar.color"
    :timeout="ui.snackbar.timeout"
    location="top"
    variant="tonal"
  >
    <div class="d-flex align-center ga-2">
      <v-icon size="small" :icon="iconByColor" />
      <span>{{ ui.snackbar.text }}</span>
    </div>

    <template #actions>
      <v-btn icon="mdi-close" variant="text" @click="ui.snackbar.visible = false" />
    </template>
  </v-snackbar>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { useUiStore } from '@/stores/useUiStore'

const ui = useUiStore()

const iconByColor = computed(() => {
  switch (ui.snackbar.color) {
    case 'error':
      return 'mdi-alert-circle-outline'
    case 'warning':
      return 'mdi-alert-outline'
    case 'info':
      return 'mdi-information-outline'
    default:
      return 'mdi-check-circle-outline'
  }
})
</script>
