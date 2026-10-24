import client from './client'
import type { CoverGenerateResult } from '@/types/api'

export interface CoverGeneratePayload {
  library_id: string
  title_zh: string
  title_en: string | null
  style_name: string
  temp_image_paths: string[] | null
  /** 动图样式的输出格式: gif | webp | png;静态样式传 null */
  animation_format: string | null
}

export async function generateCover(payload: CoverGeneratePayload): Promise<CoverGenerateResult> {
  const { data } = await client.post<CoverGenerateResult>('/generate-cover', payload)
  return data
}

export async function uploadTempImage(file: File): Promise<{ path: string }> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await client.post<{ path: string }>('/upload_temp_image', form)
  return data
}

export async function clearCovers(): Promise<void> {
  await client.post('/covers/clear')
}
