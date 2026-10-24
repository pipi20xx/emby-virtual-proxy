import { createRouter, createWebHashHistory } from 'vue-router'

// 使用 hash 模式:管理后台由 FastAPI StaticFiles(html=true) 托管,
// 无需后端增加 SPA 回退路由即可支持任意页面的刷新与直达。
const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: { name: 'libraries' } },
    {
      path: '/libraries',
      name: 'libraries',
      component: () => import('@/views/LibrariesView.vue'),
      meta: { title: '虚拟媒体库' },
    },
    {
      path: '/libraries/new',
      name: 'library-new',
      component: () => import('@/views/LibraryEditView.vue'),
      meta: { title: '新建虚拟库' },
    },
    {
      path: '/libraries/:id/edit',
      name: 'library-edit',
      component: () => import('@/views/LibraryEditView.vue'),
      meta: { title: '编辑虚拟库' },
    },
    {
      path: '/filters',
      name: 'filters',
      component: () => import('@/views/FiltersView.vue'),
      meta: { title: '高级筛选器' },
    },
    {
      path: '/layout',
      name: 'layout',
      component: () => import('@/views/LayoutView.vue'),
      meta: { title: '主页布局' },
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/views/SettingsView.vue'),
      meta: { title: '系统设置' },
    },
    { path: '/:pathMatch(.*)*', redirect: { name: 'libraries' } },
  ],
})

router.afterEach((to) => {
  const title = to.meta.title as string | undefined
  document.title = title ? `${title} · Emby Virtual Proxy` : 'Emby Virtual Proxy'
})

export default router
