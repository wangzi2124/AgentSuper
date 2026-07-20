import type { GeneratedFileList } from '../types'
import { fetchWithTimeout } from './fetch'

// 生成文件 API 基础路径
const BASE = '/api/generated'

// 获取生成文件列表，支持关键字搜索
export async function listGenerated(q?: string): Promise<GeneratedFileList> {
  const params = q ? `?q=${encodeURIComponent(q)}` : ''
  const res = await fetchWithTimeout(BASE + '/' + params)
  if (!res.ok) throw new Error(`Failed to list generated files: ${res.statusText}`)
  return res.json()
}

// 获取生成文件的文本内容
export async function getGeneratedContent(filename: string): Promise<string> {
  const res = await fetchWithTimeout(BASE + '/download/' + encodeURIComponent(filename))
  if (!res.ok) throw new Error(`Failed to fetch file: ${res.statusText}`)
  return res.text()
}

// 删除指定生成文件
export async function deleteGenerated(filename: string): Promise<void> {
  const res = await fetchWithTimeout(BASE + '/' + encodeURIComponent(filename), { method: 'DELETE' })
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Delete failed: ${err || res.statusText}`)
  }
}
