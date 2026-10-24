import { useUiStore } from '@/stores/useUiStore'
import type { ConfirmOptions } from '@/stores/useUiStore'

/** 确认框 composable:await confirm({...}) 得到用户选择 */
export function useConfirm() {
  const ui = useUiStore()

  async function confirm(options: ConfirmOptions): Promise<boolean> {
    return ui.confirm(options)
  }

  return { confirm }
}
