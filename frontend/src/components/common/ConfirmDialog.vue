<template>
  <v-dialog v-model="ui.confirmState.visible" max-width="440" persistent>
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon :color="ui.confirmState.color" :icon="ui.confirmState.icon" />
        <span>{{ ui.confirmState.title }}</span>
      </v-card-title>

      <v-card-text class="text-body-2" style="white-space: pre-line">
        {{ ui.confirmState.message }}
      </v-card-text>

      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="ui.resolveConfirm(false)">
          {{ ui.confirmState.cancelText }}
        </v-btn>
        <v-btn :color="ui.confirmState.color" variant="flat" @click="ui.resolveConfirm(true)">
          {{ ui.confirmState.confirmText }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { useUiStore } from '@/stores/useUiStore'

const ui = useUiStore()
</script>
