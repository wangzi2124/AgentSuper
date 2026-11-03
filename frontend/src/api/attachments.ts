import { fetchWithTimeout } from './fetch'

export interface ExtractedAttachmentText {
  filename: string
  text: string
  truncated: boolean
  chars: number
}

export interface AttachmentPayload {
  filename: string
  mime_type: string
  data: string
}

// 聊天附件正文提取（「查看正文」弹层）；不依赖会话临时目录。
export async function extractAttachmentText(
  file: AttachmentPayload,
): Promise<ExtractedAttachmentText> {
  const res = await fetchWithTimeout(
    '/api/attachments/extract',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(file),
    },
    60_000,
  )
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body?.detail || detail
    } catch {
      // ignore non-JSON error body
    }
    throw new Error(`无法提取附件正文：${detail}`)
  }
  return res.json()
}
