import type { DocumentListResponse, DocumentResponse, DeleteResponse, UploadResponse, TaskProgress } from '../types'
import { fetchWithTimeout } from './fetch'

const BASE = '/api/documents'

export async function listDocuments(): Promise<DocumentListResponse> {
  const res = await fetchWithTimeout(BASE + '/')
  if (!res.ok) throw new Error(`Failed to list documents: ${res.statusText}`)
  return res.json()
}

export function uploadDocument(
  file: File,
  onProgress?: (pct: number, stage: string) => void,
): Promise<DocumentResponse> {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append('file', file)

    const xhr = new XMLHttpRequest()
    xhr.open('POST', BASE + '/upload')

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) {
        onProgress(Math.round((e.loaded / e.total) * 4), 'Uploading to server')
      }
    }

    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        const { task_id } = JSON.parse(xhr.responseText) as UploadResponse
        onProgress?.(5, 'Queued for processing')

        try {
          const doc = await pollTask(task_id, onProgress)
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

async function pollTask(
  taskId: string,
  onProgress?: (pct: number, stage: string) => void,
): Promise<DocumentResponse> {
  const poll = async (): Promise<DocumentResponse> => {
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
    return poll()
  }

  return poll()
}

export async function deleteDocument(id: string): Promise<DeleteResponse> {
  const res = await fetchWithTimeout(BASE + '/' + id, { method: 'DELETE' })
  if (!res.ok) throw new Error(`Delete failed: ${res.statusText}`)
  return res.json()
}
