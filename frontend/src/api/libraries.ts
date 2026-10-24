import client from './client'
import type { LibraryBrief } from '@/types/api'
import type { VirtualLibrary } from '@/types/config'

export async function getAllLibraries(): Promise<LibraryBrief[]> {
  const { data } = await client.get<LibraryBrief[]>('/all-libraries')
  return data
}

export async function createLibrary(library: VirtualLibrary): Promise<VirtualLibrary> {
  const { data } = await client.post<VirtualLibrary>('/libraries', library)
  return data
}

export async function updateLibrary(id: string, library: VirtualLibrary): Promise<VirtualLibrary> {
  const { data } = await client.put<VirtualLibrary>(`/libraries/${id}`, library)
  return data
}

export async function deleteLibrary(id: string): Promise<void> {
  await client.delete(`/libraries/${id}`)
}

/** 手动刷新 RSS 虚拟库(后端 202,后台异步处理) */
export async function refreshRssLibrary(id: string): Promise<void> {
  await client.post(`/libraries/${id}/refresh`)
}

/** 保存主页显示顺序(只传"已显示"库的 id 列表) */
export async function saveDisplayOrder(orderedIds: string[]): Promise<void> {
  await client.post('/display-order', orderedIds)
}
