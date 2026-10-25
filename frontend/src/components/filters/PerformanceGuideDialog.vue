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
          把筛选规则翻译成该请求的查询参数后,转发给真实 Emby 服务器:
        </p>
        <ul class="text-body-2 text-medium-emphasis mb-2">
          <li><b>能翻译的规则</b> → 变成 Emby 原生查询参数(见下方对照表),由 Emby 服务器执行,速度最快;</li>
          <li><b>不能翻译的规则</b> → 代理取回数据后,在本地对结果逐条比对(后筛选),规则越多越慢。</li>
        </ul>
        <div class="code-block">GET /emby/Users/{userId}/Items?Recursive=true&amp;IncludeItemTypes=Movie,Series,Video
    &amp;GenreIds=&lt;虚拟库绑定的类型ID&gt;&amp;MinCommunityRating=8&amp;Tags=肉番</div>
        <p class="text-caption text-medium-emphasis mb-2">
          ↑ 其中 GenreIds 是虚拟库自身的资源绑定;MinCommunityRating=8、Tags=肉番 才是规则「社区评分 > 8」「标签 = 肉番」翻译出的原生参数。
        </p>

        <!-- 二、查询语义 -->
        <div class="text-subtitle-1 font-weight-bold mt-4 mb-2">二、Emby 查询语义:不同维度之间 AND,同一维度内永远 OR</div>
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
            <template v-for="group in efficientGroups" :key="group.name">
              <tr>
                <td colspan="3" class="text-caption font-weight-bold text-medium-emphasis">{{ group.name }}</td>
              </tr>
              <tr v-for="row in group.rows" :key="row.field">
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
            </template>
          </tbody>
        </v-table>
        <p class="text-caption text-medium-emphasis mb-2">
          注:Min/Max 类参数按含端点(≥ / ≤)处理,如「社区评分 大于 8」实际为 ≥ 8、「发行年份 大于 2022」会命中 2022 年及以后;「最近N天」含第 N 天。
        </p>

        <!-- 四、参数陷阱 -->
        <div class="text-subtitle-1 font-weight-bold mt-4 mb-2 text-warning">
          <v-icon icon="mdi-alert-outline" size="small" class="mr-1" />
          四、参数陷阱
        </div>
        <v-list density="compact" class="mb-2">
          <v-list-item subtitle="「标签 / 类型」等规则的值支持多值:用管道符 | 分隔表示任一满足(如 后宫|肉番);逗号不会拆分,写 后宫,肉番 会把整串当成一个标签名;" />
          <v-list-item subtitle="同一字段写多条「等于」类规则会翻译到同一个参数,后写的覆盖先写的,只有最后一条生效 —— 例如两条「标签 等于」只生效最后一条,想要任一满足请用一条规则写 后宫|肉番;" />
          <v-list-item subtitle="「大于 + 小于」是两个不同参数(Min / Max),可以同时使用组合成区间,如「社区评分 大于 6」+「社区评分 小于 9」;" />
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
import { FIELD_META, GROUP_ORDER, POST_FILTER_GROUP } from './fieldMeta'

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

interface EfficientGroup {
  name: string
  rows: EfficientRuleRow[]
}

/** 高效对照表由 fieldMeta.ts 的唯一字段元数据生成(与规则编辑器的下拉和提示同源) */
const efficientGroups: EfficientGroup[] = GROUP_ORDER.filter((group) => group !== POST_FILTER_GROUP)
  .map((group) => ({
    name: group,
    rows: Object.entries(FIELD_META)
      .filter(([, meta]) => meta.group === group && meta.native)
      .map(([value, meta]) => ({
        field: `${meta.title} (${value})`,
        ops: meta.ops ?? [],
        param: meta.paramExample ?? meta.native ?? '',
      })),
  }))
  .filter((group) => group.rows.length)

const inefficientRules = [
  '字段为「名称 (Name)」(无论使用什么操作符)',
  '字段为「视频范围 (VideoRange)」(无论使用什么操作符)',
  '操作符为「不等于 / 包含 / 不包含」(任意字段)',
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
