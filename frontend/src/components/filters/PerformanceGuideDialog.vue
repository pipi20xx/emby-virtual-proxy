<template>
  <v-dialog :model-value="modelValue" max-width="880" scrollable @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-speedometer" size="small" />
        高级筛选器性能指南
      </v-card-title>

      <v-divider />

      <v-card-text class="pt-4">
        <!-- 一、执行流程与 API 端点 -->
        <div class="text-subtitle-1 font-weight-bold mb-2">一、筛选规则是如何执行的</div>
        <p class="text-body-2 text-medium-emphasis mb-2">
          浏览绑定了筛选器的虚拟库时,代理会拦截客户端发出的库内容请求
          <code>GET /emby/Users/{userId}/Items?ParentId=&lt;虚拟库ID&gt;</code>,
          把规则改写成对这个端点的查询参数后,转发给真实 Emby 服务器:
        </p>
        <ul class="text-body-2 text-medium-emphasis mb-2">
          <li><b>能翻译的规则</b> → 变成 Emby 原生查询参数(见下方对照表),由 Emby 服务器执行,速度最快;</li>
          <li><b>不能翻译的规则</b> → 代理取回数据后,在本地对结果逐条比对(后筛选),规则越多越慢。</li>
        </ul>
        <div class="code-block">GET /emby/Users/{userId}/Items?Recursive=true&amp;IncludeItemTypes=Movie,Series,Video
    &amp;GenreIds=&lt;库资源ID&gt;&amp;MinCommunityRating=8&amp;Tags=肉番
    ↑ 规则「类型=某类型」「社区评分&gt;8」「标签=肉番」翻译后的原生参数</div>

        <!-- 二、查询语义 -->
        <div class="text-subtitle-1 font-weight-bold mt-4 mb-2">二、Emby 查询语义:跨维度 AND,同维度永远 OR</div>
        <p class="text-body-2 text-medium-emphasis mb-1">
          以下结论在 Emby 4.10.2 实测验证,写规则前务必理解:
        </p>
        <ul class="text-body-2 text-medium-emphasis mb-2">
          <li><b>不同维度的参数之间是 AND(且)</b>:类型 + 标签 + 评分 + 年份……组合取交集,放心叠加;</li>
          <li><b>同一维度内永远是 OR(任一满足)</b>:
            <code>TagIds=A,B</code>、<code>Tags=A|B</code>(官方管道符多值)、
            甚至 <code>TagIds=A</code> + <code>Tags=B</code> 两个参数组合,全部取并集。
            实测样本:标签"后宫"623 条、"肉番"165 条、交集 126 条,以上写法全部返回 662(并集)。</li>
          <li><b>想要"任一满足"</b>:在一条规则的值里用管道符写 <code>后宫|肉番</code>,原生即可完成;</li>
          <li><b>想要"同时满足"</b>:查询层面无解(Emby 不支持),同一字段写多条规则会翻译到同一个参数,
            <b>后写的覆盖先写的,只有最后一条生效</b>。</li>
        </ul>

        <!-- 三、高效对照表 -->
        <div class="text-subtitle-1 font-weight-bold mt-4 mb-2 text-success">
          <v-icon icon="mdi-rocket-launch-outline" size="small" class="mr-1" />
          三、高效筛选规则对照表(翻译为原生参数)
        </div>
        <p class="text-body-2 text-medium-emphasis mb-2">
          符合下表字段与操作符组合的规则,将由 Emby 服务器原生执行。右列为实际生成的查询参数。
        </p>

        <v-table density="compact" class="mb-2">
          <thead>
            <tr>
              <th class="text-left">字段</th>
              <th class="text-left">高效操作符</th>
              <th class="text-left">生成的查询参数(示例)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in efficientRules" :key="row.field">
              <td>{{ row.field }}</td>
              <td>
                <v-chip
                  v-for="op in row.ops"
                  :key="op"
                  size="x-small"
                  variant="tonal"
                  class="mr-1"
                >
                  {{ op }}
                </v-chip>
              </td>
              <td class="text-medium-emphasis">{{ row.param }}</td>
            </tr>
          </tbody>
        </v-table>

        <!-- 四、参数陷阱 -->
        <div class="text-subtitle-1 font-weight-bold mt-4 mb-2 text-warning">
          <v-icon icon="mdi-alert-outline" size="small" class="mr-1" />
          四、参数陷阱
        </div>
        <v-list density="compact" class="mb-2">
          <v-list-item subtitle="VideoTypes 参数的官方含义是介质类型(videofile / dvd / bluray / iso),与 HDR/SDR 画质无关 —— 「视频范围」规则会被翻译成该参数,但筛不出预期的画质结果;" />
          <v-list-item subtitle="Tags 参数多值分隔符是管道符 |(如 后宫|肉番);逗号不拆分,Tags=A,B 会把 A,B 整串当成一个标签名;" />
          <v-list-item subtitle="同一字段写多条规则时,生成的参数名相同,后写的覆盖先写的 —— 实际只有最后一条生效;" />
          <v-list-item subtitle="「匹配任意 (OR)」选项当前未参与执行:所有规则一律按「且」组合,选择 OR 不会改变结果,请勿依赖。" />
        </v-list>

        <!-- 五、低效规则说明 -->
        <div class="text-subtitle-1 font-weight-bold mt-4 mb-2 text-warning">
          <v-icon icon="mdi-snail" size="small" class="mr-1" />
          五、低效筛选规则说明(降级为代理端后筛选)
        </div>
        <p class="text-body-2 text-medium-emphasis mb-2">
          出现以下任一情况时,该规则无法翻译为原生参数,将在代理端对取回的数据逐条过滤:
        </p>
        <v-list density="compact" class="mb-2">
          <v-list-item v-for="(item, index) in inefficientRules" :key="index" :subtitle="item" />
        </v-list>

        <v-alert type="info" variant="tonal" density="compact">
          最佳实践:优先使用「匹配所有 (AND)」;字段与操作符尽量符合高效对照表;
          需要跨维度组合时(类型+标签+评分……)放心叠加,它们天然是 AND。
        </v-alert>
      </v-card-text>

      <v-divider />

      <v-card-actions>
        <v-spacer />
        <v-btn color="primary" variant="flat" @click="emit('update:modelValue', false)">我明白了</v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
}>()

interface EfficientRuleRow {
  field: string
  ops: string[]
  param: string
}

const efficientRules: EfficientRuleRow[] = [
  { field: '社区评分 (CommunityRating)', ops: ['大于', '小于'], param: 'MinCommunityRating=7.5 / MaxCommunityRating=7.5' },
  { field: '影评人评分 (CriticRating)', ops: ['大于', '小于'], param: 'MinCriticRating=80 / MaxCriticRating=80' },
  { field: '发行年份 (ProductionYear)', ops: ['大于', '小于', '等于'], param: 'MinPremiereDate=2023-01-01T00:00:00.000Z 等(年份转为日期范围)' },
  { field: '首播日期 (PremiereDate)', ops: ['大于', '小于', '等于'], param: 'MinPremiereDate / MaxPremiereDate,支持最近N天' },
  { field: '添加日期 (DateCreated)', ops: ['大于', '小于', '等于'], param: 'MinDateCreated / MaxDateCreated,支持最近N天' },
  { field: '官方分级 (OfficialRating)', ops: ['等于'], param: 'OfficialRatings=R' },
  { field: '类型 (Genres)', ops: ['等于'], param: 'Genres=科幻' },
  { field: '标签 (Tags)', ops: ['等于'], param: 'Tags=肉番(多值任一满足:Tags=后宫|肉番)' },
  { field: '工作室 (Studios)', ops: ['等于'], param: 'Studios=Netflix' },
  { field: '视频范围 (VideoRange)', ops: ['等于'], param: 'VideoTypes=<值> ⚠ 该参数实为介质类型,见陷阱' },
  { field: '文件容器 (Container)', ops: ['等于'], param: 'Containers=mkv' },
  { field: '名称以…开头 (NameStartsWith)', ops: ['等于'], param: 'NameStartsWith=A' },
  { field: '剧集状态 (SeriesStatus)', ops: ['等于'], param: 'SeriesStatus=Continuing / Ended' },
  { field: '是否为电影/剧集、已/未播放、有字幕、有官方评级', ops: ['等于'], param: 'IsMovie=true / IsSeries=true / IsPlayed=true / IsUnplayed=true / HasSubtitles=true / HasOfficialRating=true' },
  { field: '拥有 TMDB ID (ProviderIds.Tmdb)', ops: ['不为空', '为空'], param: 'HasTmdbId=true / HasTmdbId=false,无需填值' },
  { field: '拥有 IMDB ID (ProviderIds.Imdb)', ops: ['不为空', '为空'], param: 'HasImdbId=true / HasImdbId=false,无需填值' },
]

const inefficientRules = [
  '字段为「名称 (Name)」(无论使用什么操作符)',
  '操作符为「不等于 / 包含 / 不包含」',
  '操作符为「为空 / 不为空」,但字段不是「拥有 TMDB/IMDB ID」(例如:社区评分为空)',
]
</script>

<style scoped>
.code-block {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
  background: rgba(var(--v-theme-surface-variant), 0.5);
  padding: 10px 14px;
  border-radius: 8px;
  white-space: pre-wrap;
  word-break: break-all;
  margin-bottom: 8px;
}

code {
  padding: 1px 5px;
  border-radius: 4px;
  background: rgba(var(--v-theme-surface-variant), 0.6);
}
</style>
