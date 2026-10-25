import client from './client'

export interface LogHistoryResponse {
  /** 时间升序的原始日志行(与 WS 推送同格式) */
  lines: string[]
  /** 下一页的结束游标;null 表示已翻到最早的可用日志 */
  next_start_index: number | null
  has_more: boolean
}

/** 首屏:返回最新 tail 行 */
export async function getLogTail(tail: number, limit = 100): Promise<LogHistoryResponse> {
  const { data } = await client.get<LogHistoryResponse>('/system/logs/history', { params: { tail, limit } })
  return data
}

/** 向上翻页:返回"结束于 startIndex(不含)"的最多 limit 行 */
export async function getLogHistory(startIndex: number, limit = 100): Promise<LogHistoryResponse> {
  const { data } = await client.get<LogHistoryResponse>('/system/logs/history', { params: { start_index: startIndex, limit } })
  return data
}
