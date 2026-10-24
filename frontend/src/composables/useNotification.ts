import { useUiStore } from '@/stores/useUiStore'

/** 通知 composable:success/error/info/warning 四级 */
export function useNotification() {
  const ui = useUiStore()

  return {
    success: (text: string) => ui.notify(text, 'success'),
    error: (text: string) => ui.notify(text, 'error', 5000),
    info: (text: string) => ui.notify(text, 'info'),
    warning: (text: string) => ui.notify(text, 'warning', 4500),
  }
}
