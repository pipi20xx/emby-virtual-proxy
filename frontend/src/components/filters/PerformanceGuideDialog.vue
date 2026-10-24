<template>
  <v-dialog :model-value="modelValue" max-width="760" @update:model-value="emit('update:modelValue', $event)">
    <v-card>
      <v-card-title class="d-flex align-center ga-2">
        <v-icon icon="mdi-speedometer" size="small" />
        高级筛选器性能指南
      </v-card-title>

      <v-card-text>
        <!-- 高效规则对照表 -->
        <div class="text-subtitle-1 font-weight-bold text-success mb-2">
          <v-icon icon="mdi-rocket-launch-outline" size="small" class="mr-1" />
          高效筛选规则对照表
        </div>
        <p class="text-body-2 text-medium-emphasis mb-2">
          当规则完全符合下表的字段和操作符组合时,筛选将由 Emby 服务器原生执行,速度最快。
        </p>

        <v-table density="compact" class="mb-4">
          <thead>
            <tr>
              <th class="text-left">字段</th>
              <th class="text-left">高效操作符</th>
              <th class="text-left">示例</th>
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
                  :color="row.opColor ?? 'success'"
                  class="mr-1"
                >
                  {{ op }}
                </v-chip>
              </td>
              <td class="text-medium-emphasis">{{ row.example }}</td>
            </tr>
          </tbody>
        </v-table>

        <!-- 低效规则说明 -->
        <div class="text-subtitle-1 font-weight-bold text-warning mb-2">
          <v-icon icon="mdi-snail" size="small" class="mr-1" />
          低效筛选规则说明
        </div>
        <p class="text-body-2 text-medium-emphasis mb-2">
          出现以下任一情况时,该规则将降级到代理服务器本地处理(先取回数据再过滤),可能导致性能问题:
        </p>
        <v-list density="compact" class="mb-2">
          <v-list-item v-for="(item, index) in inefficientRules" :key="index" :subtitle="item" />
        </v-list>

        <v-alert type="info" variant="tonal" density="compact">
          最佳实践:优先使用「匹配所有条件 (AND)」,并确保每条规则符合高效对照表。
        </v-alert>
      </v-card-text>

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
  example: string
  opColor?: string
}

const efficientRules: EfficientRuleRow[] = [
  { field: '社区评分 (CommunityRating)', ops: ['大于', '小于', '等于'], example: '7.5' },
  { field: '影评人评分 (CriticRating)', ops: ['大于', '小于', '等于'], example: '80' },
  { field: '发行年份 (ProductionYear)', ops: ['大于', '小于', '等于'], example: '2023' },
  { field: '首播日期 (PremiereDate)', ops: ['大于', '小于', '等于'], example: '2024-01-01,支持最近N天' },
  { field: '添加日期 (DateCreated)', ops: ['大于', '小于', '等于'], example: '2024-01-01,支持最近N天' },
  { field: '官方分级 (OfficialRating)', ops: ['等于'], example: 'R' },
  { field: '类型 (Genres)', ops: ['等于'], example: '科幻(效果为包含该值)' },
  { field: '标签 (Tags)', ops: ['等于'], example: '高分(效果为包含该值)' },
  { field: '工作室 (Studios)', ops: ['等于'], example: 'Netflix(效果为包含该值)' },
  { field: '视频范围 (VideoRange)', ops: ['等于'], example: 'HDR' },
  { field: '文件容器 (Container)', ops: ['等于'], example: 'mkv' },
  { field: '名称以…开头 (NameStartsWith)', ops: ['等于'], example: 'A' },
  { field: '剧集状态 (SeriesStatus)', ops: ['等于'], example: 'Continuing / Ended' },
  { field: '是否为电影 (IsMovie)', ops: ['等于'], example: 'true / false' },
  { field: '是否为剧集 (IsSeries)', ops: ['等于'], example: 'true / false' },
  { field: '已播放 (IsPlayed)', ops: ['等于'], example: 'true / false' },
  { field: '未播放 (IsUnplayed)', ops: ['等于'], example: 'true / false' },
  { field: '有字幕 (HasSubtitles)', ops: ['等于'], example: 'true / false' },
  { field: '有官方评级 (HasOfficialRating)', ops: ['等于'], example: 'true / false' },
  { field: '拥有 TMDB ID (ProviderIds.Tmdb)', ops: ['不为空', '为空'], example: '无需填写任何值' },
  { field: '拥有 IMDB ID (ProviderIds.Imdb)', ops: ['不为空', '为空'], example: '无需填写任何值' },
]

const inefficientRules = [
  '匹配逻辑为「匹配任意条件 (OR)」时,全部规则都会降级到代理端处理',
  '字段为「名称 (Name)」(无论使用什么操作符)',
  '操作符为「不等于 / 包含 / 不包含」',
  '操作符为「为空 / 不为空」,但字段不是「拥有 TMDB/IMDB ID」(例如:社区评分为空)',
]
</script>
