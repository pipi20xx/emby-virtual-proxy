/** vuedraggable 共享参数:同一 group 的列表之间可以互相拖动 */
export function useDragSort(group = 'evp-libraries') {
  return {
    group,
    animation: 150,
    handle: '.drag-handle',
    ghostClass: 'drag-ghost',
  }
}
