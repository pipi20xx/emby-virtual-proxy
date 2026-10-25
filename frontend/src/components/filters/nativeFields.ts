/** Emby 官方支持的筛选字段(可翻译为原生查询参数,由 Emby 服务端执行)。
 *
 * 必须与后端 src/advanced_filters/translator.py 的 FIELD_MAP 保持一致;
 * 新增/删除字段时两处需同步修改。
 */

/** 枚举值:严格限制,下拉只能选 */
export interface FieldValueEnum {
  kind: 'enum'
  options: { title: string; value: string }[]
}

/** 建议值:可自由输入,下拉提供常用值 */
export interface FieldValueSuggest {
  kind: 'suggest'
  options: string[]
}

/** 数值:限制输入范围与步进 */
export interface FieldValueNumber {
  kind: 'number'
  min?: number
  max?: number
  step?: number
}

export type ValueControl = FieldValueEnum | FieldValueSuggest | FieldValueNumber

/** 操作符标识 -> 中文名(与后端 _check_condition 支持的操作符一致) */
export const OPERATOR_TITLES: Record<string, string> = {
  equals: '等于',
  not_equals: '不等于',
  contains: '包含',
  not_contains: '不包含',
  greater_than: '大于',
  less_than: '小于',
  is_empty: '为空',
  is_not_empty: '不为空',
}

export interface NativeFieldMeta {
  group: string
  title: string
  /** 高效操作符 */
  ops: string[]
  /** 可用但会降级为代理端后筛选的操作符(未列出的操作符对该字段无意义,不应提供) */
  postOps?: string[]
  /** 生成参数(简洁描述,编辑器提示用) */
  native: string
  /** 参数示例(性能指南表格用) */
  paramExample?: string
  /** 值填写说明 */
  valueHint?: string
  /** 值控件:枚举/建议/数字范围,用于限制值输入 */
  valueControl?: ValueControl
}

const BOOLEAN_VALUES = [
  { title: 'true', value: 'true' },
  { title: 'false', value: 'false' },
]

export const NATIVE_FIELDS: Record<string, NativeFieldMeta> = {
  CommunityRating: {
    group: '评分',
    title: '社区评分',
    ops: ['等于', '大于', '小于'],
    postOps: ['不等于', '为空', '不为空'],
    native: 'Min/MaxCommunityRating(等于时两者同值)',
    paramExample: 'MinCommunityRating=7.5 / MaxCommunityRating=7.5(等于时两者同值)',
    valueControl: { kind: 'number', min: 0, max: 10, step: 0.1 },
  },
  CriticRating: {
    group: '评分',
    title: '影评人评分',
    ops: ['等于', '大于', '小于'],
    postOps: ['不等于', '为空', '不为空'],
    native: 'Min/MaxCriticRating(等于时两者同值)',
    paramExample: 'MinCriticRating=80 / MaxCriticRating=80(等于时两者同值)',
    valueControl: { kind: 'number', min: 0, max: 100, step: 1 },
  },
  ProductionYear: {
    group: '时间',
    title: '发行年份',
    ops: ['等于', '大于', '小于'],
    postOps: ['不等于', '为空', '不为空'],
    native: '年份转为日期范围(含端点:大于 2022 会命中 2022 全年)',
    paramExample: 'MinPremiereDate=2023-01-01T00:00:00.000Z 等(年份转为日期范围)',
    valueControl: { kind: 'number', min: 1900, max: 2100, step: 1 },
  },
  PremiereDate: {
    group: '时间',
    title: '首播日期',
    ops: ['等于', '大于', '小于'],
    postOps: ['为空', '不为空'],
    native: 'Min/MaxPremiereDate,支持最近N天(含第 N 天)',
    paramExample: 'MinPremiereDate / MaxPremiereDate,支持最近N天',
    valueHint: '选日期或填最近N天',
  },
  DateCreated: {
    group: '时间',
    title: '添加日期',
    ops: ['等于', '大于', '小于'],
    postOps: ['为空', '不为空'],
    native: 'Min/MaxDateCreated,支持最近N天(含第 N 天)',
    paramExample: 'MinDateCreated / MaxDateCreated,支持最近N天',
    valueHint: '选日期或填最近N天',
  },
  OfficialRating: {
    group: '分类与标签',
    title: '官方分级',
    ops: ['等于'],
    postOps: ['不等于', '包含', '不包含', '为空', '不为空'],
    native: 'OfficialRatings',
    paramExample: 'OfficialRatings=R',
    valueHint: '随地区不同,常见如 R / PG-13 / TV-MA',
    valueControl: { kind: 'suggest', options: ['G', 'PG', 'PG-13', 'R', 'NC-17', 'TV-Y', 'TV-Y7', 'TV-G', 'TV-PG', 'TV-14', 'TV-MA'] },
  },
  Genres: {
    group: '分类与标签',
    title: '类型',
    ops: ['等于'],
    postOps: ['不等于', '包含', '不包含', '为空', '不为空'],
    native: 'Genres',
    paramExample: 'Genres=科幻',
    valueHint: '取决于媒体库内容,如 科幻;多值任一满足用 | 分隔,如 科幻|动画',
  },
  Tags: {
    group: '分类与标签',
    title: '标签',
    ops: ['等于'],
    postOps: ['不等于', '包含', '不包含', '为空', '不为空'],
    native: 'Tags',
    paramExample: 'Tags=肉番(多值任一满足:Tags=后宫|肉番)',
    valueHint: '取决于媒体库内容,如 肉番;多值任一满足用 | 分隔,如 后宫|肉番',
  },
  Studios: {
    group: '分类与标签',
    title: '工作室',
    ops: ['等于'],
    postOps: ['不等于', '包含', '不包含', '为空', '不为空'],
    native: 'Studios',
    paramExample: 'Studios=Netflix',
    valueHint: '取决于媒体库内容,如 Netflix',
  },
  Container: {
    group: '媒体与文件',
    title: '文件容器',
    ops: ['等于'],
    postOps: ['不等于', '包含', '不包含', '为空', '不为空'],
    native: 'Containers',
    paramExample: 'Containers=mkv',
    valueHint: '常见如 mkv / mp4',
    valueControl: { kind: 'suggest', options: ['mkv', 'mp4', 'avi', 'ts', 'm2ts', 'wmv', 'mov', 'flv', 'iso'] },
  },
  NameStartsWith: {
    group: '媒体与文件',
    title: '名称以…开头',
    ops: ['等于'],
    postOps: ['为空', '不为空'],
    native: 'NameStartsWith',
    paramExample: 'NameStartsWith=A',
    valueHint: '填名称首字符,如 A',
  },
  SeriesStatus: {
    group: '媒体与文件',
    title: '剧集状态',
    ops: ['等于'],
    postOps: ['不等于', '包含', '不包含', '为空', '不为空'],
    native: 'SeriesStatus',
    paramExample: 'SeriesStatus=Continuing / Ended',
    valueHint: '只能选连载中或已完结',
    valueControl: {
      kind: 'enum',
      options: [
        { title: '连载中 (Continuing)', value: 'Continuing' },
        { title: '已完结 (Ended)', value: 'Ended' },
      ],
    },
  },
  IsMovie: { group: '布尔条件', title: '是否为电影', ops: ['等于'], postOps: ['不等于', '为空', '不为空'], native: 'IsMovie', paramExample: 'IsMovie=true', valueHint: '只能选 true / false', valueControl: { kind: 'enum', options: BOOLEAN_VALUES } },
  IsSeries: { group: '布尔条件', title: '是否为剧集', ops: ['等于'], postOps: ['不等于', '为空', '不为空'], native: 'IsSeries', paramExample: 'IsSeries=true', valueHint: '只能选 true / false', valueControl: { kind: 'enum', options: BOOLEAN_VALUES } },
  IsPlayed: { group: '布尔条件', title: '已播放', ops: ['等于'], postOps: ['不等于', '为空', '不为空'], native: 'IsPlayed', paramExample: 'IsPlayed=true', valueHint: '只能选 true / false', valueControl: { kind: 'enum', options: BOOLEAN_VALUES } },
  IsUnplayed: { group: '布尔条件', title: '未播放', ops: ['等于'], postOps: ['不等于', '为空', '不为空'], native: 'IsUnplayed', paramExample: 'IsUnplayed=true', valueHint: '只能选 true / false', valueControl: { kind: 'enum', options: BOOLEAN_VALUES } },
  HasSubtitles: { group: '布尔条件', title: '有字幕', ops: ['等于'], postOps: ['不等于', '为空', '不为空'], native: 'HasSubtitles', paramExample: 'HasSubtitles=true', valueHint: '只能选 true / false', valueControl: { kind: 'enum', options: BOOLEAN_VALUES } },
  HasOfficialRating: { group: '布尔条件', title: '有官方评级', ops: ['等于'], postOps: ['不等于', '为空', '不为空'], native: 'HasOfficialRating', paramExample: 'HasOfficialRating=true', valueHint: '只能选 true / false', valueControl: { kind: 'enum', options: BOOLEAN_VALUES } },
  'ProviderIds.Tmdb': {
    group: '存在性检查',
    title: '拥有 TMDB ID',
    ops: ['不为空', '为空', '等于'],
    postOps: ['不等于'],
    native: 'HasTmdbId=true / false',
    paramExample: 'HasTmdbId=true / HasTmdbId=false,无需填值',
    valueHint: '通常用「为空/不为空」,无需填值',
    valueControl: { kind: 'enum', options: BOOLEAN_VALUES },
  },
  'ProviderIds.Imdb': {
    group: '存在性检查',
    title: '拥有 IMDB ID',
    ops: ['不为空', '为空', '等于'],
    postOps: ['不等于'],
    native: 'HasImdbId=true / false',
    paramExample: 'HasImdbId=true / HasImdbId=false,无需填值',
    valueHint: '通常用「为空/不为空」,无需填值',
    valueControl: { kind: 'enum', options: BOOLEAN_VALUES },
  },
}
