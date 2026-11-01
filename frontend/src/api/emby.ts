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

/** 后台凭证状态(模式 + 缓存健康度,不发网络请求) */
export interface CredentialStatus {
  mode: 'api_key' | 'user_pass' | 'none'
  username: string
  token_cached: boolean
  updated_at: number | null
  last_ok_at: number | null
  last_error: string
  user_id: string
}

/** 连接测试结果 */
export interface CredentialTestResult {
  ok: boolean
  mode: CredentialStatus['mode']
  user: string
  message: string
}

export async function getCredentialStatus(): Promise<CredentialStatus> {
  const { data } = await client.get<CredentialStatus>('/emby-credential/status')
  return data
}

export async function testCredential(): Promise<CredentialTestResult> {
  const { data } = await client.post<CredentialTestResult>('/emby-credential/test')
  return data
}

/** 行级连接测试请求体(测试尚未保存的服务器条目) */
export interface CredentialTestBody {
  url: string
  api_key: string
  username: string
  password: string
  proxy: string
}

/** 行级连接测试;不传参则测试已保存的主服 */
export async function testServerCredential(payload?: Partial<CredentialTestBody>): Promise<CredentialTestResult> {
  const { data } = await client.post<CredentialTestResult>('/emby-credential/test', payload ?? {})
  return data
}
