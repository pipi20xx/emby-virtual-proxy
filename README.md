# Emby Virtual Proxy

本项目是一个功能强大的 **Emby 代理服务器**。它作为中间层部署在您的 Emby 客户端（如浏览器、手机APP）和真实的 Emby 服务器之间。通过智能地拦截和修改客户端与服务器之间的通信数据（API请求），本项目实现了许多 Emby 原生不支持的高级功能，极大地增强了您的媒体库管理和浏览体验。

**功能总览**：虚拟媒体库 · 高级筛选器 · TMDB ID 合并 · RSS 虚拟库（豆瓣/Bangumi）· 风格化封面生成（含动态封面）· 四层缓存与本地直出 · 图片本地化缓存 · 缺失剧集显示 · 实时运行日志 · 缓存状态面板 · Web 管理后台

---

## 🚀 快速开始 (Docker Compose)

1.  在您的服务器上创建一个目录，例如 `emby-proxy`。
2.  在该目录下，创建一个名为 `docker-compose.yml` 的文件。
3.  将以下内容复制并粘贴到 `docker-compose.yml` 文件中：

    ```yaml
    services:
      emby-proxy:
        image: pipi20xx/emby-virtual-proxy
        container_name: emby-proxy
        ports:
          # 管理后台端口，左边为主机端口，右边为容器端口
          - "8011:8001"
          # 代理核心端口，左边为主机端口，右边为容器端口
          - "8999:8999"
        volumes:
          # 挂载配置文件和生成的封面/日志/图片缓存目录，确保数据持久化
          - ./config:/app/config
        environment:
          # 环境变量：告诉管理服务如何访问同一容器内的代理服务
          - PROXY_CORE_URL=http://localhost:8999
          # 时区：影响日志时间戳与按天日志切分，可改为任意 IANA 时区名
          - TZ=Asia/Shanghai
        restart: unless-stopped
    ```

4.  在 `docker-compose.yml` 文件所在的目录中，运行以下命令启动服务：
    ```bash
    docker-compose up -d
    ```

5.  部署成功后：
    - **访问管理后台**: `http://<您的服务器IP>:8011`
    - **在Emby中配置代理**: 将Emby客户端（如Infuse, Emby Web）的服务器地址改为 `http://<您的服务器IP>:8999`
    - **设置管理密码（可选）**: 在管理后台的“系统设置”中填写 `admin_password` 并保存，之后打开管理后台需要先登录

> 说明：
> - 管理后台已不再提供「重启代理服务」按钮（该方式在 compose 环境下不稳定），需要清空内存缓存请用「缓存状态」页的维护操作，或直接 `docker compose restart`。
> - 如果想从源码构建镜像，可直接克隆本仓库并使用仓库自带的 `docker-compose.yml`（使用 `build: .` 方式构建）。
> - `config/` 目录现在还会保存 `image_cache/`（海报本地化缓存），体积随浏览增长，可在设置页限制上限或一键清空。

### 🔌 端口说明

| 端口 | 用途 |
|---|---|
| 8001（容器）→ 8011（主机） | 管理后台 Web 界面及其 API |
| 8999 | 代理核心，**Emby 客户端应连接此端口** |

---

## ✨ 功能详细说明

### 1. 虚拟媒体库 (Virtual Libraries)

虚拟媒体库并不在 Emby 服务器上真实存在，而是由代理按规则动态生成。它会像真实库一样出现在客户端首页，拥有自己的封面，并支持绑定筛选器和合并策略。

**支持的资源类型**：

| 资源类型 | 说明 |
|---|---|
| 合集 (collection) | 以某个 Emby 合集（如“漫威电影宇宙”）的全部条目作为内容源 |
| 标签 (tag) | 以指定标签下的所有条目作为内容源 |
| 类型 (genre) | 以指定影片类型下的所有条目作为内容源 |
| 工作室 (studio) | 聚合指定工作室出品的全部内容 |
| 演职人员 (person) | 聚合指定演员/导演参与的全部内容 |
| 全库 (all) | 聚合所有媒体库的内容，配合高级筛选器实现对整个媒体资源的灵活筛选 |
| RSS 订阅 (rsshub) | 将 RSSHub 生成的豆瓣 / Bangumi 订阅源映射为动态更新的媒体库（见第 4 节） |

**每个虚拟库可独立配置**：

- **高级筛选器**：绑定一个筛选器规则组，精确控制该库收录的内容（见第 2 节）。
- **TMDB ID 合并**：独立开启按 TMDB ID 合并版本（见第 3 节）。
- **展示类型**：控制客户端按哪种媒体库界面渲染——电视剧库 / 电影库 / 混合（电影+剧集）。「自动」跟随资源类型：**全库默认按混合呈现，电影与剧集都可见**，其余资源类型按电视剧呈现（历史行为）；内容同时包含电影和剧集的标签、类型等库建议显式选「混合」，否则客户端只会按剧集浏览。
- **范围限定**：将内容源限定在某个真实媒体库之内。
- **封面**：单独配置封面样式、中英文标题、字体与素材图片目录（见第 5 节）。

**首页展示控制**：在管理后台通过**拖拽**调整虚拟库与真实库在 Emby 主页的显示顺序；真实库也可以选择**隐藏**，只留下虚拟库。

### 2. 高级筛选器 (Advanced Filters)

一个筛选器由名称和多条规则组成，每条规则为“字段 + 操作符 + 值”。筛选器可绑定到任意虚拟库，也可用于“全库”类型的虚拟库对整个 Emby 媒体资源做精确筛选。

**支持的字段**：

| 类别 | 字段 |
|---|---|
| 评分 | 社区评分 (CommunityRating)、影评人评分 (CriticRating) |
| 日期 | 首播日期 (PremiereDate)、添加日期 (DateCreated)、发行年份 (ProductionYear) |
| 检索 | 名称开头 (NameStartsWith)、官方评级 (OfficialRating)、是否有官方评级 |
| 分类 | 类型 (Genres)、标签 (Tags)、工作室 (Studios) |
| 媒体 | 视频规格 (VideoRange，如 HDR/SDR)、封装格式 (Container)、是否有字幕 |
| 布尔 | 是否电影、是否剧集、已播放、未播放、有无 TMDB ID、有无 IMDB ID |
| 剧集 | 剧集状态 (SeriesStatus，如连播中/已完结) |

**支持的操作符**：等于 / 不等于 / 包含 / 不包含 / 大于 / 小于 / 为空 / 不为空。

**动态日期**：首播日期和添加日期除绝对日期外，还支持“最近 N 天内”这类**相对时间**规则。筛选器在每次执行时动态计算日期范围，而非在设置时转换一次——保存“最近 30 天内添加”后，每天看到的都是真正的最近 30 天。

**双执行模式**：每个筛选器可独立选择执行模式（编辑页顶部切换）：

| 模式 | 规则由谁评估 | 特点 |
|---|---|---|
| **本地全量**（新建默认） | 代理拉取完整数据后，全部规则在本地对全量数据逐条评估 | 同一字段多条规则真正 AND 组合；「匹配任意 (OR)」生效；所有字段与操作符可用；计数与分页始终准确。已播放/未播放按**每个用户自己的**播放状态评估。配合虚拟库缓存预热，浏览时不再逐条请求 Emby |
| **Emby 原生**（存量默认） | 能翻译的规则变成 Emby 查询参数由服务器执行，翻译不了的由代理后筛选兜底（历史行为） | 开销最小；但同一字段多条规则仅最后一条生效、OR 不参与执行、部分字段/操作符不可用 |

存量筛选器默认保持「Emby 原生」模式，行为与升级前完全一致；切换模式保存即生效。**使用原生模式前请务必阅读下方“Emby 查询语义备忘”**，了解 Emby 查询的 AND/OR 语义与参数陷阱（本地全量模式由代理自行评估，不受这些限制）。

### 3. TMDB ID 合并 (Merge by TMDB ID)

自动将在不同资料库中但拥有相同 `TheMovieDb.org ID` 的电影或剧集进行聚合。当您在“最近添加”或媒体库视图中浏览时，将只看到一个条目，有效解决版本重复（如 1080p 和 4K 版本）的问题。

- 支持两种粒度：**虚拟库级开关**（`merge_by_tmdb_id`）和**全局强制开关**（`force_merge_by_tmdb_id`，启用后覆盖所有虚拟库的独立设置）。
- 合并后的剧集可以跨库正确取回季与分集信息；代理内置 **TMDB 全库索引**（本地 SQLite），跨库查找无需遍历全部媒体库，性能开销极低。

### 4. RSS 虚拟库 (RSSHub)

将 RSSHub 生成的订阅源映射为一个动态更新的媒体库，资源类型选择“RSS 订阅”即可。

- **数据源**：`rss_type` 支持**豆瓣**（想看/在看/看过等频道）与 **Bangumi**（番剧放送列表）两类订阅链接。
- **混合内容展示**：虚拟库自动区分 RSS 源中的项目哪些已在您的 Emby 库中，哪些尚未入库；未入库的项目通过 TMDB API 获取元数据（海报、简介、年份），动态生成“占位符”供您浏览“待看”清单。
- **数据保留**：开启 `enable_retention` 后，即使条目已从 RSS 源中消失也会保留在库中，超过 `retention_days`（默认 7 天，0 为永久）才自动清理——解决 RSS 源更新过快导致未入库条目被“顶出”的问题。
- **兜底条目**：可为 RSS 库指定一个 TMDB ID 与媒体类型，该条目会被无条件追加到库中，确保源匹配失败或为空时库中至少有一个项目。
- **刷新方式**：管理后台手动刷新，或设置全局定时刷新 `rss_refresh_interval`（小时，0 为禁用）。
- **依赖**：需在系统设置中配置有效的 **TMDB API 密钥**；Bangumi 匹配为全本地智能匹配（多策略搜索 + 打分系统），结果与 API 响应均持久化缓存于 `bangumi_cache.db`。

### 5. 封面生成 (Cover Generation)

为虚拟库一键生成风格化海报封面，支持静态与动图两种形态。

**内置 7 种样式**：

| 样式 | 类型 |
|---|---|
| 样式一（多图拼接） | 静态（默认样式） |
| 样式二（单图） / 样式三（单图） | 静态 |
| 多图九宫格（动态） | 动图：三列斜置滚动 + 标题 |
| 横向轮播（动态） | 动图：4 张海报带倒影横向轮播 |
| 背景轮播（动态） / 背景斜播（动态） | 动图：6/9 张海报作为背景轮播 |

- **动图输出**：支持 GIF / WebP / APNG 三种格式（GIF 兼容性最好），画布固定 320×180（16:9），兼容 Emby 动态壁纸；每个虚拟库会记忆自己上次使用的样式与动图格式。
- **素材来源（四级回退）**：虚拟库内容随机取材 → 库级自定义图片目录 → 全局自定义图片目录 → 编辑页临时上传的素材（最多 9 张，超出所需时随机抽选，保证每次生成有所不同）。
- **自定义外观**：封面可单独设置中文主标题（留空使用库名）与英文标题；中英文字体支持全局设置（`custom_zh_font_path` / `custom_en_font_path`），也可为单个虚拟库覆盖，不填则使用内置默认字体。
- **自动生成与重绘**：保存虚拟库、缓存预热或定时刷新完成后，代理会按内容指纹（条目集合哈希）自动重新生成封面——内容未变化则跳过重绘，内容变化则沿用该库固化的样式配方重绘。自动生成的样式由 `default_cover_style` 控制。
- **存储与维护**：封面文件存放于 `config/images` 并通过代理 `/covers` 路径提供服务；「缓存状态」页面可一键清空所有本地封面（清空后下次预热/浏览会按当前配方重新生成）。

### 6. 性能与缓存 (Performance & Caching)

缓存按“越往下越共享”分成四层，另有几类全局缓存（API 响应 / 图片 / TMDB 索引）。所有层的实时状态都可在管理后台的**「缓存状态」页面**查看（条目数、时效、命中率、下次自动刷新时间、磁盘占用）。

| 层 | 存什么 | 位置 | 作用域 | 默认时效 | 开关 |
|---|---|---|---|---|---|
| **L0 全库数据源** | 向 Emby 裸拉的全部条目（未筛选、未合并） | 内存 + SQLite `vlib_base_items` | **全局一份，所有虚拟库共用** | 6 小时（内容有变化才重拉） | `enable_vlib_local_serving` + `enable_vlib_cache` |
| **L1 派生元数据** | 该库过滤 + TMDB 合并后的条目 | 内存 + SQLite `vlib_metadata` | 每虚拟库 | 6 小时软过期 / 72 小时硬过期 | 同上 |
| **L2 用户状态层** | 每账号的已看 / 收藏 / 进度 | 内存 + SQLite `vlib_user_overlay` | 每账号（所有虚拟库共用） | 10 分钟 / 24 小时硬过期 | 同上 |
| **L3 直出结果层** | 已组装 + 已排序的可见列表 | 内存 | 库 × 用户 × 当前排序 | 随 L1/L2 版本变化 | 同上 |
| **季/集合并结果** | 合并后的季与集 | 内存 | 账号 × 剧集 × 季 | 15 分钟 | `enable_merge_cache` |
| **API 响应缓存** | 任意 GET JSON 响应 | 内存 | 账号 × URL × 参数 | `api_cache_ttl`（默认 300 秒） | `enable_cache` |
| **图片缓存** | 海报 / 背景 / 缩略图 | 磁盘 `config/image_cache/` | 全局（按条目 + 尺寸 + tag） | 永久（URL 带内容指纹，`immutable`） | `enable_image_cache` + `image_cache_max_mb` |
| **封面素材** | 每库前 100 项，供封面自动取材 | 内存 + SQLite `vlib_items` | 每虚拟库 | 跟随 L1 | `enable_vlib_cache` |
| **TMDB 索引** | 条目 → TMDB ID 查找表 | SQLite `emby_tmdb_index` | 全局 | 每 `tmdb_index_refresh_interval` 小时重建 | `enable_tmdb_index` |

**作用范围**：L0~L3、季/集合并、封面素材只服务**虚拟库**请求；真实库的列表与详情请求原样透传，既不读也不写这些缓存。**图片缓存与 API 响应缓存是全局的** —— 图片缓存对真实库同样生效（同一张图在虚拟库和真实库中共用一份缓存）。

**关键机制**：

- **共享数据源**：挂在同一批内容上的多个虚拟库只向 Emby 拉一次（L0），各库在本地派生自己的结果。「合集 / 标签 / 类型 / 工作室 / 人员」型虚拟库的资源过滤也已下沉到本地完成（依赖 `CollectionIds` / `TagItems` / `GenreItems` 字段），因此同样共享 L0。
- **探测式定时刷新**：到点先发 **1 个 `Limit=1` 的请求**，比对 `TotalRecordCount` 与最新 `DateCreated`；内容没变化就整轮跳过，有新增/删除才真正重建。把“每小时全量重拉”降成“每小时每库 1 个极小请求”。
- **条件请求（ETag / 304）**：虚拟库列表响应带 ETag（由数据版本 + 请求形态决定），客户端前进/后退/重复进入时直接 304，连序列化都省掉。
- **写入即失效**：修改库规则 → 立即失效该库派生缓存并重建（不再等 6 小时）；标记已看/收藏 → 立即丢弃该账号的状态层与响应缓存。共享的 L0/L2 不会因为改一个库而被清空。
- **stale-while-revalidate**：软过期后照常返回旧数据，后台静默重建，浏览永不阻塞。
- **JSON 压缩**：只对 `application/json` 做 gzip / br（视频与图片流完全不碰），一页几百 KB 的列表通常压到 10~20%。
- **图片永久缓存**：Emby 的图片 URL 自带 `tag`（内容指纹），同一 URL 内容永不改变，因此可回 `Cache-Control: immutable`，连客户端都不再重复请求；磁盘超出配额时按最久未使用淘汰到 90%。

**排障速查**（在服务器上执行）：

```bash
# 各层条目数 / 时效 / 命中率 / 下次自动刷新时间
curl -s http://127.0.0.1:8999/api/internal/cache-stats | python3 -m json.tool

# 图片缓存自检:给定 path 与原始 query,直接回答"会不会命中同一个文件"
curl -s -G http://127.0.0.1:8999/api/internal/image-cache-selftest \
  --data-urlencode 'path=emby/Items/123456/Images/Primary' \
  --data-urlencode 'query=maxHeight=563&maxWidth=375&tag=<tag>&quality=90'

# 单个图片请求是否走了本地缓存(看响应头)
curl -sI 'http://127.0.0.1:8999/emby/Items/123456/Images/Primary?maxHeight=563&maxWidth=375&tag=<tag>&quality=90' \
  | grep -i x-evp-image-cache
#   miss      = 回源抓取并落盘
#   hit       = 读本地磁盘
#   hit-304   = 客户端带 ETag 来,直接 304
#   无此响应头 = 未接管(无 tag / 带 Range / 功能关闭)
```

日志中的关键事件：`图片缓存命中: <id>/Primary`、`从共享数据源派生 '<库名>' 完成`、`探测到库 '<名>' 内容有变化(...)`、`启动预热:库 '<名>' 缓存仍新鲜(...),跳过刷新`。

### 7. 显示缺失剧集 (Missing Episodes)

在系统设置中开启 `show_missing_episodes`（需配置 TMDB API 密钥）后，浏览电视剧的季页面时，代理会自动通过 TMDB 查询该季完整分集列表，与本地库对比后将**未收藏的剧集以占位图形式注入**显示，方便直观地补全收藏。

占位图可通过替换 `src/assets/images_placeholder/placeholder.jpg` 自定义（推荐 16:9 比例）。

### 8. 运行日志 (Runtime Logs)

- **实时查看**：管理后台“运行日志”页面通过 WebSocket 实时滚动展示代理 / 管理双进程日志，内置最近 1000 条环形历史，打开页面即可回溯。
- **落盘留存**：日志按天写入 `config/logs/{进程名}-YYYY-MM-DD.log`，默认保留 7 天；`docker logs` 仍可看到全部输出。
- **级别与时区**：`log_level` 支持 debug / info / warn / error；`TZ` 环境变量影响日志时间戳与按天切分。

### 9. Web 管理后台 (Web Admin UI)

内置基于 Vue 3 + TypeScript + Vuetify 4 的管理界面（管理端口 8001），覆盖全部配置能力：

- **页面**：登录、虚拟库管理、虚拟库编辑（资源/筛选器/封面工作室）、高级筛选器管理、主页布局、**缓存状态**、运行日志、系统设置。
- **缓存状态页**：实时快照 + 10 秒自动刷新，包含全库数据源与各库派生条目、用户状态层、合并结果、图片缓存占用、命中计数器、**定时任务的下次运行时间**，以及维护操作（清空项目缓存 / 清空图片缓存 / 清空所有本地封面 / 清零计数器）。
- **密码登录（可选）**：设置 `admin_password` 后打开管理后台需先登录，会话 Token 7 天有效，重启服务后需重新登录。密码只保护管理端口，**不影响代理端口（8999）的客户端访问**。
- **维护操作**：手动生成/刷新封面、清空本地封面、清空项目缓存、清空图片缓存、清零命中计数器，均集中在「缓存状态」页（原「系统设置 → 危险区域」已移除，为改配置后清缓存而重启代理的做法也不再需要）。

---

## ⚙️ 配置项参考 (config.json)

所有配置推荐在管理后台修改，持久化于 `config/config.json`。以下为全部配置项速查：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `emby_url` | `http://127.0.0.1:8096` | 上游 Emby 服务器地址 |
| `emby_api_key` | （空） | 上游 Emby 的 API 密钥 |
| `emby_server_id` | （空） | 上游服务器 ID，用于 TMDB 占位符的备用 ServerId |
| `emby_proxy` | （空） | 访问 Emby 上游的 HTTP 代理，格式 `http://[user:pass@]host:port`，留空直连，保存后即时生效 |
| `log_level` | `info` | 日志级别：debug / info / warn / error |
| `admin_password` | （空） | 管理后台登录密码，留空不启用登录 |
| `display_order` | `[]` | 主页媒体库（含虚拟库）显示顺序 |
| `hide` | `[]` | 需在首页隐藏的真实库 ID 列表 |
| `library` | `[]` | 虚拟库列表 |
| `advanced_filters` | `[]` | 高级筛选器列表 |
| `enable_cache` | `true` | API 响应缓存总开关（**全局**：含真实库的 GET JSON） |
| `api_cache_ttl` | `300` | API 响应缓存 TTL（秒），0 为禁用 |
| `enable_vlib_cache` | `true` | 虚拟库项目缓存总开关（内存 + SQLite + 预热） |
| `vlib_cache_refresh_interval` | `0` | 虚拟库缓存刷新间隔（小时），0 为禁用；到点先做一次变更探测，内容未变化则整轮跳过 |
| `enable_vlib_local_serving` | `true` | 虚拟库列表本地直出（四层缓存的总开关） |
| `enable_merge_cache` | `true` | 季/集合并结果短时缓存（15 分钟，按账号隔离） |
| `enable_tmdb_index` | `true` | TMDB 全库索引开关（加速季/集合并） |
| `tmdb_index_refresh_interval` | `24` | TMDB 索引重建间隔（小时），0 为仅启动时构建 |
| `enable_image_cache` | `true` | 图片本地化缓存开关（海报/背景，**全局**生效） |
| `image_cache_max_mb` | `2048` | 图片缓存磁盘上限（MB），0 为不限制 |
| `image_cache_require_credential` | `false` | 图片缓存是否要求请求带凭证；默认关闭——多数 Emby 的图片接口不鉴权，而浏览器 `<img>` 不携带可识别凭证，开启会导致图片不再命中缓存 |
| `show_missing_episodes` | `false` | 显示缺失剧集开关 |
| `tmdb_api_key` | （空） | TMDB API 密钥（RSS 占位符、缺失剧集依赖） |
| `tmdb_proxy` | （空） | TMDB 的 HTTP 代理 |
| `rss_refresh_interval` | `0` | RSS 定时刷新间隔（小时），0 为禁用 |
| `force_merge_by_tmdb_id` | `false` | 全局强制按 TMDB ID 合并（覆盖虚拟库级设置） |
| `default_cover_style` | `style_multi_1` | 自动生成封面的默认样式 |
| `custom_zh_font_path` / `custom_en_font_path` | （空） | 全局封面中/英文字体路径（容器内绝对路径） |
| `custom_image_path` | （空） | 全局封面素材图片目录（库级未指定时的后备） |

> 虚拟库对象（`library` 数组元素）的关键字段：`resource_type`（见资源类型表）、`resource_id`、`collection_type`（展示类型：`tvshows` / `movies` / `mixed`，`null` 为自动推导——全库→混合，其余→电视剧）、`advanced_filter_id`、`merge_by_tmdb_id`、`source_library`、`rsshub_url`、`rss_type`、`enable_retention` / `retention_days`、`fallback_tmdb_id` / `fallback_tmdb_type`、`cover_style` / `cover_title_zh` / `cover_title_en` / `cover_animation_format` 等封面字段。

---

## 📁 项目结构

```
src/
├── main.py                 # 双进程入口 (admin / proxy)
├── app_paths.py            # 文件系统路径唯一定义处
├── models.py               # Pydantic 数据模型
├── config_manager.py       # config.json 读写 (TTL缓存 + 只读快照 + 原子写入)
├── db_manager.py           # SQLite 缓存库管理 (每线程独立连接)
├── proxy_cache.py          # 代理端内存缓存 (四层缓存 / 响应缓存 / 合并缓存 / 命中计数器)
├── json_compress.py        # 只压缩 JSON 响应的 ASGI 中间件 (视频/图片流不碰)
├── emby_net.py             # 访问 Emby 上游的 HTTP 代理统一解析
├── logger.py               # 日志采集 (后台线程写盘 + WebSocket实时 + 按天文件)
│
├── proxy_server.py         # 代理核心 (客户端流量入口,勿随意改动)
├── proxy_handlers/         # 代理端各端点处理器
│   │                       #   视图/Items/季集/最新/首页分区/合并/RSS/封面等
│   ├── handler_items.py    #   四层缓存与本地直出、列表派生、变更探测的数据侧
│   ├── handler_image_cache.py  # 图片本地化缓存 (tag 即内容指纹,immutable)
│   └── handler_autogen.py  #   封面按内容指纹自动重绘
├── advanced_filters/       # 高级筛选器 (规则翻译为 Emby 原生参数)
│   └── translator.py       #   纯函数,无网络请求
│
├── admin_server.py         # 管理后台 API (纯薄路由层,只调用 services/)
├── services/               # 管理后台业务服务层 (认证/虚拟库/封面/RSS/日志/缓存预热/TMDB索引/容器重启)
├── rss_processor/          # RSS 数据刷新处理器 (豆瓣/Bangumi, 基类+子类)
├── cover_generator/        # 封面样式插件 (3 静态 + 4 动态,每个 style_*.py 一个样式,可插拔)
└── assets/                 # 内置字体与占位图
```

运行时数据都在 `config/`（容器内 `/app/config`，建议整目录挂载持久化）：
`config.json` · `vlib_items.db`（全库数据源/派生元数据/用户状态层/封面素材/合并缓存/TMDB 索引）· `images/`（生成的封面）· `image_cache/`（海报本地化缓存）· `logs/`（按天日志）· RSS 相关缓存库。

分层原则:**代理核心** (`proxy_server.py` + `proxy_handlers/`) 面向客户端,
改动需极度谨慎;**管理后台** (`admin_server.py` + `services/`) 面向配置,
业务逻辑沉淀在服务层,路由层保持薄。两边通过 `/api/internal/*`
内部接口和 SQLite 缓存库交互,不直接共享内存。

前端源码位于 `frontend/`(Vue 3 + TypeScript + Vuetify 4,构建产物由
Dockerfile 复制到 `/app/static` 由管理后台直接托管)。

## ⚠️ Emby 查询语义备忘(设计高级筛选器前必读)

以下结论在 Emby 4.10.2 上逐条实测验证,并与服务器自带 API 文档交叉核对。
**仅影响「Emby 原生」模式的筛选器**;「本地全量」模式由代理对完整数据自行评估,不受以下任何限制。
原生模式的规则最终会翻译成这些查询参数,**理解语义才能写出符合预期的规则**。

### 规则一:不同过滤"维度"之间是 AND(且)

不同维度的参数组合,Emby 查询引擎取**交集**:

- `GenreIds=141247 + Tags=肉番` → 类型里、且带该标签的条目 ✓
- `TagIds=498648 + MinCommunityRating=8` → 标签内、且评分达标的条目 ✓
- 评分 + 年份 + 官方分级 …… 不同字段之间天然是 AND,放心组合

### 规则二:同一"维度"内永远是 OR(任一满足),无论怎么写

同一维度给多个值,Emby 一律取**并集**,没有任何查询写法能表达"同时满足":

| 写法 | 结果 | 说明 |
|---|---|---|
| `TagIds=A,B`(多 ID) | A 或 B | 并集 |
| `Tags=A\|B`(官方管道符多值) | A 或 B | 并集;分隔符是 `\|` 不是逗号 |
| `TagIds=A` + `Tags=B`(两个参数组合) | A 或 B | 这两个参数本质是同一维度,照样并集 |

> 实测样本:标签"后宫"623 条、"肉番"165 条、交集 126 条;
> 以上三种写法全部返回 662(并集),没有任何写法能直接得到 126。

### 对高级筛选器的实际指导

1. **想要“任一满足”**(后宫 或 肉番):原生模式在一条规则的值里用管道符写 `后宫|肉番`;本地全量模式直接选「匹配任意 (OR)」。
2. **想要“同时满足”**(后宫 且 肉番):原生模式查询层面无解——同一字段写多条规则会翻译到同一个查询参数,**后写的覆盖先写的,只有最后一条生效**;本地全量模式写两条规则按真正的 AND 组合,天然解决。
3. 不同字段的规则之间(标签+评分+日期……)天然是 AND,可放心叠加。

### 已知的参数陷阱

- `VideoTypes` 参数官方定义是**介质类型**(videofile / dvd / bluray / iso),与 HDR/SDR 画质无关,不要用它筛动态范围
- `Tags` 多值分隔符是管道符 `|`;`Tags=A,B` 会把 "A,B" 整串当成一个标签名

### 权威参考

Emby 服务器自带与你运行版本逐字一致的 API 文档(493 个端点),任何"支不支持某参数"的问题以它为准:
`http://<你的EMBY地址>:8096/emby/swagger`

## 🛠️ 技术架构

- **后端 (Backend)**:
    - **框架**: Python `FastAPI`，通过 `supervisor` 在单容器内运行双进程（代理核心 + 管理后台）。
    - **异步处理**: `aiohttp` 用于与 Emby 服务器进行高性能的异步HTTP通信；大对象的 SQLite 读写与 JSON 序列化走线程池，避免阻塞事件循环。
    - **缓存**: `cachetools` 内存缓存（四层虚拟库缓存 + 响应缓存 + 合并缓存 + 命中计数器）+ 多个 SQLite 持久层（虚拟库数据源/元数据/状态层、TMDB、Bangumi、RSS 等）+ 磁盘图片缓存。
    - **传输优化**: 只对 JSON 响应生效的 gzip/br 压缩中间件；虚拟库列表响应支持 ETag / 304。
    - **定时任务**: `APScheduler` 负责 RSS 刷新、虚拟库缓存刷新（先探测后重建）、TMDB 索引刷新等周期任务。
    - **封面生成**: `Pillow` + `NumPy` 负责图片拼贴与动图帧合成。
    - **日志**: 写盘与控制台输出在后台线程执行（`QueueHandler` + `QueueListener`），实时流经 WebSocket 推送。
- **前端 (Frontend)**:
    - **框架/构建**: `Vue 3` + `TypeScript` + `Vite`
    - **状态管理**: `Pinia`
    - **UI 组件库**: `Vuetify 4`

---

## 📱 关于客户端与播放器兼容性

作者个人只测试了小幻、yamby、EMBY小秘、Forward、senplay，目前均兼容。如果你遇到不兼容的客户端，请提出 issue 并留下客户端名称。

---

## 🙏 致谢

本项目的设计和功能受到了以下优秀项目的启发，特此感谢：

- **[EkkoG/emby-virtual-lib](https://github.com/EkkoG/emby-virtual-lib)**
- **[justzerock/MoviePilot-Plugins](https://github.com/justzerock/MoviePilot-Plugins/tree/8ef476f9e5ae4d3d549300bad74083c084a46f1d)**
- **[HappyQuQu/jellyfin-library-poster](https://github.com/HappyQuQu/jellyfin-library-poster)**

---

总而言之，**Emby Virtual Proxy** 是一个为 Emby 高级玩家和收藏家设计的强大工具，它通过“代理”这一巧妙的方式，无侵入性地为您的 Emby 带来了前所未有的灵活性和可定制性。
