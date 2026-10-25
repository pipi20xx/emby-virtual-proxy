/** 高级筛选字段元数据聚合(唯一调用入口)。
 *
 * 字段按 Emby 支持情况分两个文件存放:
 * - nativeFields.ts:Emby 官方支持的筛选字段(含支持的值限制:枚举/建议/范围)
 * - postFilterFields.ts:Emby 不官方支持、只能代理端后筛选的字段
 *
 * 分类与字段必须与后端 src/advanced_filters/translator.py 的 FIELD_MAP 保持一致。
 */
import { NATIVE_FIELDS, OPERATOR_TITLES } from './nativeFields'
import { POST_FILTER_FIELDS, POST_FILTER_GROUP } from './postFilterFields'
import type { ValueControl } from './nativeFields'

export { POST_FILTER_GROUP, OPERATOR_TITLES }
export type { ValueControl }

export interface FieldMeta {
  group: string
  title: string
  /** 存在即表示可翻译为 Emby 原生查询参数 */
  native?: string
  /** 参数示例(性能指南表格用) */
  paramExample?: string
  /** 高效操作符 */
  ops?: string[]
  /** 可用但降级后筛选的操作符(未列出的操作符对该字段无意义) */
  postOps?: string[]
  /** 值填写说明 */
  valueHint?: string
  /** 存在即表示只能走代理端后筛选 */
  post?: string
  /** 值控件:枚举/建议/数字范围,用于限制值输入 */
  valueControl?: ValueControl
}

export const FIELD_META: Record<string, FieldMeta> = {
  ...NATIVE_FIELDS,
  ...POST_FILTER_FIELDS,
}

/** 下拉分组顺序 */
export const GROUP_ORDER = [
  '评分',
  '时间',
  '分类与标签',
  '媒体与文件',
  '布尔条件',
  '存在性检查',
  POST_FILTER_GROUP,
]

export interface FieldSelectItem {
  type?: 'subheader' | 'divider'
  title?: string
  value?: string
}

/** 字段下拉项:按分类分组,小标题用 Vuetify 列表原生 subheader */
export function buildFieldSelectItems(): FieldSelectItem[] {
  const items: FieldSelectItem[] = []
  GROUP_ORDER.forEach((group, groupIndex) => {
    const entries = Object.entries(FIELD_META).filter(([, meta]) => meta.group === group)
    if (!entries.length) return
    if (groupIndex > 0) items.push({ type: 'divider' })
    items.push({ type: 'subheader', title: group })
    for (const [value, meta] of entries) {
      items.push({ value, title: `${meta.title} (${value})` })
    }
  })
  return items
}

/** 规则编辑时的字段提示:执行方式 + 高效操作符 + 值说明 */
export function buildFieldHint(field: string): string {
  const meta = FIELD_META[field]
  if (!meta) return '选择字段后,这里会显示该字段的执行方式与填写说明'
  const titleOf = (op: string) => OPERATOR_TITLES[op] ?? op
  const parts: string[] = []
  if (meta.native) parts.push(`原生执行(Emby 服务端处理,最快):生成参数 ${meta.native}`)
  else if (meta.post) parts.push(`代理端后筛选(逐条比对,较慢):${meta.post}`)
  if (meta.ops?.length) parts.push(`高效操作符:${meta.ops.map(titleOf).join(' / ')}`)
  if (meta.native && meta.postOps?.length) {
    parts.push(`「${meta.postOps.map(titleOf).join(' / ')}」降级为后筛选(慢)`)
  }
  if (meta.valueHint) parts.push(meta.valueHint)
  return parts.join(';')
}
