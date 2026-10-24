import client from './client'
import type { AppConfig } from '@/types/config'

export async function getConfig(): Promise<AppConfig> {
  const { data } = await client.get<AppConfig>('/config')
  return data
}

export async function saveConfig(config: AppConfig): Promise<AppConfig> {
  const { data } = await client.post<AppConfig>('/config', config)
  return data
}
