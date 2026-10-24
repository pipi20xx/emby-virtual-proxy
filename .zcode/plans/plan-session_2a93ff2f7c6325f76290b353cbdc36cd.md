# 管理面板前端完全重写方案

## 一、目标与边界

把 `frontend/` 整体重写为与 **Anime-Manager 同构**的工程,视觉采用 **Vuetify 原生卡片 + 设计令牌**(不用玻璃/three.js),亮暗双主题,主色对齐 AM 的紫色系(#8D51F9),让两个项目像一个套件。

**硬边界**:后端 16 个 API 端点契约零改动(admin_server.py 与 services 层不动);旧面板在重新构建部署前继续可用(容器跑的是旧 dist);全部旧代码先保留,最后一步再清理,git 可整体回退。不加 i18n / PWA(保持轻量,后续要再说)。

## 二、技术栈(对齐 Anime-Manager 实际版本)

| 类别 | 选型 |
|---|---|
| 框架 | Vue 3.5 + TypeScript 5.9(`<script setup>`) |
| UI | Vuetify 4.1 + @mdi/font 图标(默认 mdi set) |
| 路由 | vue-router 5(替代现在的 activeTab ref) |
| 状态 | Pinia 4,按域拆分 |
| 构建 | Vite 8 + @vitejs/plugin-vue + vite-plugin-vuetify + sass-embedded |
| 工具 | @vueuse/core、lodash-es、vuedraggable、axios |
| 质量 | vue-tsc 类型检查进构建、ESLint + Prettier |
| 自动导入 | unplugin-auto-import(与 AM 一致) |

## 三、新目录结构

```
frontend/src/
├── main.ts / App.vue / env.d.ts
├── plugins/vuetify.ts        # 亮暗双主题(对齐AM色板) + icons + defaults
├── router/index.ts           # 5个路由页
├── api/                      # client.ts(axios+拦截器,统一错误)
│   ├── config.ts  libraries.ts  filters.ts  covers.ts  system.ts
├── types/config.ts           # AppConfig/VirtualLibrary/AdvancedFilter/AdvancedFilterRule
│                             # (与后端 Pydantic 模型一一对应,含 'library' 别名说明)
├── stores/
│   ├── useConfigStore.ts     # config 读写、保存
│   ├── useEmbyDataStore.ts   # classifications / all-libraries / personNameCache
│   └── useUiStore.ts         # 通知/确认框/全局loading
├── composables/
│   ├── useInfiniteScroll.ts  # 替代现在的 querySelector+setTimeout hack
│   ├── useDragSort.ts  useConfirm.ts  useNotification.ts  useTheme.ts
├── styles/                   # tokens.css(圆角/间距/色板变量 --evp-*)、base.css、
│                             # cards.css、pages.css、transitions.css、index.css
├── layouts/DefaultLayout.vue # 侧边导航(移动端抽屉) + 顶栏(主题切换/Emby连接状态)
├── components/common/        # SectionHeader、ConfirmDialog、EmptyState、SecretField、PageHeader
├── components/libraries/     # LibraryForm、ResourceSelect、CoverStudio、RssSection、CoverPreview
├── components/filters/       # FilterRuleEditor(单条规则行)、PerformanceGuideDialog
└── views/
    ├── LibrariesView.vue     # /libraries      虚拟库卡片网格
    ├── LibraryEditView.vue   # /libraries/new、/libraries/:id/edit  编辑+封面工作台
    ├── FiltersView.vue       # /filters        高级筛选器管理
    ├── LayoutView.vue        # /layout         主页布局(拖拽,独立页替代弹窗)
    └── SettingsView.vue      # /settings       系统设置
```

## 四、页面与旧功能映射(全量覆盖,不丢功能)

| 旧 | 新 | 要点 |
|---|---|---|
| SystemSettings(14 项设置+危险区) | SettingsView 分 5 组卡片:Emby 连接 / TMDB 与缺失剧集 / RSS 刷新 / 代理行为(缓存、强制合并、隐藏类型) / 封面默认(样式+字体+图片目录);密钥用 SecretField;**加表单校验**(旧版零校验) | POST /config |
| VirtualLibraries 表格 | LibrariesView 卡片网格:封面图、类型 chip、合并标记、RSS 刷新按钮、搜索+类型过滤;删除用全局 ConfirmDialog | /all-libraries、/libraries CRUD、/libraries/{id}/refresh |
| LibraryEditDialog(422行大弹窗) | LibraryEditView 独立页面 + 拆 4 个子组件;**草稿模式**(本地编辑、保存才提交、离开未保存提示);ResourceSelect 用 useInfiniteScroll(不再碰 Element Plus 内部 DOM),编辑回显保证已选值始终可见;RssSection 保留保留开关/天数/兜底TMDB 全部逻辑 | POST/PUT /libraries |
| 封面生成区 | CoverStudio:左侧 16:9 大预览 + 右侧参数(3样式/中英文标题/库级字体/图片目录/上传素材≤9张);上传改走 api 模块(不再硬编码 action);生成成功立即刷新预览 | /generate-cover、/upload_temp_image、/covers/{id}.jpg |
| AdvancedFilterManager(340行) | FiltersView + FilterRuleEditor:23 字段 × 8 操作符、动态日期(绝对/最近N天互斥、自动 greater_than)、AND/OR;**性能指南弹窗改为组件渲染,去掉 v-html**;保存前校验 | /advanced-filters |
| DisplayOrderManager 弹窗 | LayoutView 独立页:双栏拖拽(真实/虚拟库 tag 区分),保存语义与现在一致(只提交已显示列表) | /display-order |
| 重启服务/清空封面按钮 | LayoutView 或顶栏(重启代理)+ SettingsView 危险区(清空封面) | /proxy/restart、/covers/clear |
| api/index.js 单文件 | api/ 分域 + TS 类型 + 拦截器统一错误(useNotification 替代散落的 ElMessage) | 全部 16 端点 |

## 五、工程配置要点

- `vite.config.ts`:别名 `@→src`;**dev proxy:`/api`、`/covers` → http://192.168.50.252:8011**(修复旧版"本地 dev 无法联调"的痛点);`build.outDir: 'dist'`(Docker 链路不变);压缩配置
- `main.ts`:createApp → Pinia → Vuetify → Router → mount;全局样式只引 `@/styles/index.css`
- vuetify.ts 对齐 AM:`defaults` 统一 outlined/compact 表单、tonal 按钮;light/dark 主题色直接采用 AM 的色板
- **Dockerfile 改一处**:stage1 `node:18-alpine` → `node:22-alpine`(Vite 8 要求 Node ≥20.19),构建命令 `vue-tsc && vite build`,npm 淘宝镜像保留
- `index.html` 标题改为与项目一致的中文名

## 六、实施步骤(每步可独立验证)

1. **脚手架**:新依赖、tsconfig、vite 配置、plugins/vuetify、router 5 个空页、DefaultLayout、tokens.css → `npm run dev` 跑通空壳+代理联调
2. **基础设施**:types(对照 Pydantic 模型)、api 分域、3 个 store、5 个 composables、common 组件
3. **SettingsView**:最简单的页,打通 config 读写/校验/保存全链路
4. **LibrariesView 列表页**:卡片网格 + CRUD + 确认框 + RSS 刷新
5. **LibraryEditView**:ResourceSelect 无限滚动、RssSection、草稿与校验
6. **CoverStudio**:预览/上传/生成/刷新(依赖阶段4的库存在)
7. **FiltersView**:规则编辑器 + 性能指南
8. **LayoutView** + 整体打磨(空态/加载态/过渡/移动端抽屉)
9. **收尾**:`vue-tsc && npm run build` 通过 → **由你自己构建镜像部署**(和这次一样 compose build)→ 按回归清单验收 → 删除旧组件文件与冗余依赖(element-plus、sortablejs、uuid 等)

## 七、风险与回退

- Vuetify 4 生态较新 → 版本组合完全照抄 AM(它已在生产验证),遇到坑以 AM 用法为准
- Node 18→22 只影响 Dockerfile stage1 一行,后端镜像不受影响
- 旧面板在你执行构建前不受任何影响;全部旧代码 git 留档,任何时刻可回退
- 回归验收清单:16 个 API 全部触发一遍(建库/编辑/删除/封面生成/上传素材/筛选器/布局/设置/重启/清空),暗色模式、移动端宽度、强刷缓存各验一次