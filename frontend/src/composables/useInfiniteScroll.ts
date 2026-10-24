import { onUnmounted, ref, toValue, watch } from 'vue'
import type { MaybeRefOrGetter } from 'vue'

interface InfiniteScrollOptions {
  hasMore: MaybeRefOrGetter<boolean>
  loading: MaybeRefOrGetter<boolean>
  /** 预加载距离,越早触发加载 */
  rootMargin?: string
}

/**
 * 基于 IntersectionObserver 的无限滚动:把 sentinelRef 绑到列表末尾的占位元素上,
 * 元素进入视口(或菜单滚动到底)且 hasMore 为真时触发 loadMore。
 * 无需像旧实现那样 querySelector Element/Emby 组件的内部 DOM。
 */
export function useInfiniteScroll(loadMore: () => void, options: InfiniteScrollOptions) {
  const sentinelRef = ref<HTMLElement | null>(null)
  let observer: IntersectionObserver | null = null

  function canLoad(): boolean {
    return toValue(options.hasMore) && !toValue(options.loading)
  }

  watch(
    sentinelRef,
    (el) => {
      observer?.disconnect()
      observer = null
      if (!el) return
      observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting) && canLoad()) {
            loadMore()
          }
        },
        { rootMargin: options.rootMargin ?? '160px' },
      )
      observer.observe(el)
    },
    { immediate: true },
  )

  onUnmounted(() => {
    observer?.disconnect()
    observer = null
  })

  return { sentinelRef }
}
