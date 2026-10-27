import client from './client'
import type { AppConfig } from '@/types/config'
import type { CacheStats } from '@/types/api'

export async function getConfig(): Promise<AppConfig> {
  const { data } = await client.get<AppConfig>('/config')
  return data
}

export async function saveConfig(config: AppConfig): Promise<AppConfig> {
  const { data } = await client.post<AppConfig>('/config', config)
  return data
}

/** 代理核心的缓存状态与命中率快照(核心不可达时 available=false) */
export async function getCacheStats(): Promise<CacheStats> {
  const { data } = await client.get<CacheStats>('/cache-stats')
  return data
}
