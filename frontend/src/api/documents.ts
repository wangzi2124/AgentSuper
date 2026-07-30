import type { DocumentListResponse, DocumentResponse, DeleteResponse, UploadResponse, TaskProgress } from '../types'
import { fetchWithTimeout } from './fetch'
import { getUserId } from './auth'

// 文档 API 基础路径
const BASE = '/api/documents'

// 获取所有已上传文档列表
export async function listDocuments(): Promise<DocumentListResponse> {
  const res = await fetchWithTimeout(BASE + '/')
  if (!res.ok) throw new Error(`Failed to list documents: ${res.statusText}`)
  return res.json()
}

// 上传文档，支持上传进度回调和任务轮询
export function uploadDocument(
  file: File,
  onProgress?: (pct: number, stage: string) => void,
  signal?: AbortSignal,
): Promise<DocumentResponse> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'))

    const form = new FormData()
    form.append('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', BASE + '/upload')
    xhr.setRequestHeader('X-User-Id', getUserId())

    const onAbort = () => {
      xhr.abort()
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', onAbort, { once: true })

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 4), `Uploading to server (${Math.round(e.loaded / e.total * 100)}%)`)
      }
    }

    xhr.onload = async () => {
      signal?.removeEventListener('abort', onAbort)
      if (xhr.status >= 200 && xhr.status < 300) {
        const { task_id } = JSON.parse(xhr.responseText) as UploadResponse
        onProgress?.(5, 'Queued for processing')

        try {
          const doc = await pollTask(task_id, onProgress, signal)
          resolve(doc)
        } catch (e) {
          reject(e)
        }
      } else {
        try {
          reject(new Error(`Upload failed: ${JSON.parse(xhr.responseText).detail ?? xhr.responseText}`))
        } catch {
          reject(new Error(`Upload failed: ${xhr.responseText}`))
        }
      }
    }

    xhr.onerror = () => reject(new Error('Upload failed: network error'))
    xhr.send(form)
  })
}

// 轮询文档处理任务状态，直到完成或失败
async function pollTask(
  taskId: string,
  onProgress?: (pct: number, stage: string) => void,
  signal?: AbortSignal,
): Promise<DocumentResponse> {
  const poll = async (): Promise<DocumentResponse> => {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')

    const res = await fetchWithTimeout(`${BASE}/tasks/${taskId}`, {}, 0)
    if (!res.ok) throw new Error(`Failed to get task progress: ${res.statusText}`)

    const data = (await res.json()) as TaskProgress

    if (onProgress) {
      onProgress(data.progress, data.stage)
    }

    if (data.status === 'completed') {
      return data.result!
    }

    if (data.status === 'failed') {
      throw new Error(data.error || 'Processing failed')
    }

    await new Promise((r) => setTimeout(r, 400))
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError')
    return poll()
  }

  return poll()
}

// 根据 ID 删除文档
export async function deleteDocument(id: string): Promise<DeleteResponse> {
  const res = await fetchWithTimeout(BASE + '/' + id, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Delete failed: ${res.statusText}`)
  return res.json()
}
