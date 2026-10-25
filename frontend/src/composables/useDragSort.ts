/** vuedraggable 共享参数:同一 group 的列表之间可以互相拖动 */
export function useDragSort(group = 'evp-libraries') {
  return {
    group,
    animation: 150,
    handle: '.drag-handle',
    ghostClass: 'drag-ghost',
    chosenClass: 'drag-chosen',
    /** 触屏时按住 180ms 才开始拖拽,避免滚动列表时误触发 */
    delay: 180,
    delayOnTouchOnly: true,
  }
}
