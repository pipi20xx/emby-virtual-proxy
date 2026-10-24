/**
 * 深拷贝纯 JSON 数据(配置/虚拟库/筛选器等)。
 * 注意:Vue 的 reactive 代理对象无法通过 structuredClone 克隆(会抛 DataCloneError),
 * 因此这里统一走 JSON 序列化,对纯 JSON 结构绝对安全。
 */
export function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}
