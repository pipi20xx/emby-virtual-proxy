import client from './client'
import type { Classifications, EmbyItemBrief } from '@/types/api'

export async function restartProxy(): Promise<void> {
  await client.post('/proxy/restart')
}

export async function getClassifications(): Promise<Classifications> {
  const { data } = await client.get<Classifications>('/emby/classifications')
  return data
}

/** 人员远程搜索,每页 100 条 */
export async function searchPersons(query: string, page: number): Promise<EmbyItemBrief[]> {
  const { data } = await client.get<EmbyItemBrief[]>('/emby/persons/search', {
    params: { query: query || undefined, page },
  })
  return data
}

/** 解析单个 Emby 条目(用于人名回显) */
export async function resolveItem(itemId: string): Promise<EmbyItemBrief> {
  const { data } = await client.get<{ name: string; id: string }>(`/emby/resolve-item/${itemId}`)
  return data
}
