/** 不被 Emby 官方查询参数支持、只能由代理端后筛选的字段。
 *
 * 这些字段没有对应的 Emby 原生查询参数(或原生参数含义不同),
 * 一律由代理取回数据后逐条比对;新增字段需与后端
 * src/advanced_filters/translator.py 的「不映射」约定保持一致。
 */
import type { ValueControl } from './nativeFields'

/** 后筛选字段的分组名(高效对照表会排除该组) */
export const POST_FILTER_GROUP = '代理端后筛选(较慢)'

const BOOLEAN_ENUM = [
  { title: 'true', value: 'true' },
  { title: 'false', value: 'false' },
]

export interface PostFilterFieldMeta {
  group: string
  title: string
  /** 后筛选方式说明 */
  post: string
  /** 可用操作符(全部走后筛选;未列出的操作符对该字段无意义,不应提供) */
  postOps: string[]
  /** 值填写说明 */
  valueHint?: string
  /** 值控件(建议值) */
  valueControl?: ValueControl
}

export const POST_FILTER_FIELDS: Record<string, PostFilterFieldMeta> = {
  CriticRating: {
    group: POST_FILTER_GROUP,
    title: '影评人评分',
    post: 'Emby 无可用原生参数(实测 Min/MaxCriticRating 均被忽略),代理逐条比对',
    postOps: ['equals', 'not_equals', 'greater_than', 'less_than', 'is_empty', 'is_not_empty'],
    valueControl: { kind: 'number', min: 0, max: 100, step: 1 },
  },
  VideoRange: {
    group: POST_FILTER_GROUP,
    title: '视频范围',
    post: '按条目的 VideoRange 属性逐条比对',
    postOps: ['equals', 'not_equals', 'contains', 'not_contains', 'is_empty', 'is_not_empty'],
    valueHint: '常见如 SDR / HDR10 / HLG / DOVI',
    valueControl: { kind: 'suggest', options: ['SDR', 'HDR10', 'HLG', 'DOVI'] },
  },
  Name: {
    group: POST_FILTER_GROUP,
    title: '名称',
    post: '对条目名称做字符串比对,支持包含/不包含',
    postOps: ['equals', 'not_equals', 'contains', 'not_contains', 'is_empty', 'is_not_empty'],
  },
  IsMovie: {
    group: POST_FILTER_GROUP,
    title: '是否为电影',
    post: '按条目类型(Type)逐条比对(实测 Emby 忽略 IsMovie 参数)',
    postOps: ['equals', 'not_equals', 'is_empty', 'is_not_empty'],
    valueHint: '只能选 true / false',
    valueControl: { kind: 'enum', options: BOOLEAN_ENUM },
  },
  IsSeries: {
    group: POST_FILTER_GROUP,
    title: '是否为剧集',
    post: '按条目类型(Type)逐条比对(实测 Emby 忽略 IsSeries 参数)',
    postOps: ['equals', 'not_equals', 'is_empty', 'is_not_empty'],
    valueHint: '只能选 true / false',
    valueControl: { kind: 'enum', options: BOOLEAN_ENUM },
  },
  IsUnplayed: {
    group: POST_FILTER_GROUP,
    title: '未播放',
    post: '按条目的 UserData.Played 逐条比对(实测 Emby 忽略 IsUnplayed 参数)',
    postOps: ['equals', 'not_equals', 'is_empty', 'is_not_empty'],
    valueHint: '只能选 true / false',
    valueControl: { kind: 'enum', options: BOOLEAN_ENUM },
  },
}
