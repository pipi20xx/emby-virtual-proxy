import { defineStore } from 'pinia'
import { reactive } from 'vue'

export interface ConfirmOptions {
  title?: string
  message: string
  confirmText?: string
  cancelText?: string
  color?: string
  icon?: string
}

type NotifyColor = 'success' | 'error' | 'info' | 'warning'

/** 全局 UI 状态:确认框 + 通知条 */
export const useUiStore = defineStore('ui', () => {
  const confirmState = reactive({
    visible: false,
    title: '确认操作',
    message: '',
    confirmText: '确定',
    cancelText: '取消',
    color: 'primary',
    icon: 'mdi-help-circle-outline',
  })

  let resolver: ((value: boolean) => void) | null = null

  function confirm(options: ConfirmOptions): Promise<boolean> {
    confirmState.title = options.title ?? '确认操作'
    confirmState.message = options.message
    confirmState.confirmText = options.confirmText ?? '确定'
    confirmState.cancelText = options.cancelText ?? '取消'
    confirmState.color = options.color ?? 'primary'
    confirmState.icon = options.icon ?? 'mdi-help-circle-outline'
    confirmState.visible = true
    return new Promise((resolve) => {
      resolver = resolve
    })
  }

  function resolveConfirm(value: boolean) {
    confirmState.visible = false
    resolver?.(value)
    resolver = null
  }

  const snackbar = reactive({
    visible: false,
    text: '',
    color: 'success' as NotifyColor,
    timeout: 3500,
  })

  function notify(text: string, color: NotifyColor = 'success', timeout = 3500) {
    snackbar.text = text
    snackbar.color = color
    snackbar.timeout = timeout
    snackbar.visible = true
  }

  return { confirmState, confirm, resolveConfirm, snackbar, notify }
})
