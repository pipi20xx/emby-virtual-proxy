import client from './client'
import type { AdvancedFilter } from '@/types/config'

export async function getFilters(): Promise<AdvancedFilter[]> {
  const { data } = await client.get<AdvancedFilter[]>('/advanced-filters')
  return data
}

/** 整体保存筛选器列表(后端 204) */
export async function saveFilters(filters: AdvancedFilter[]): Promise<void> {
  await client.post('/advanced-filters', filters)
}
