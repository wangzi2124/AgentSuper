import { fetchWithTimeout } from './fetch'

// 中断正在运行的会话：真正停止后台 Agent 任务（配合前端本地 abort 双保险）
export async function interruptSession(sessionId: string): Promise<void> {
  const res = await fetchWithTimeout(
    `/api/sessions/${encodeURIComponent(sessionId)}/interrupt`,
    { method: 'POST' },
    5000,
  )
  if (!res.ok) {
    const err = await res.text()
    throw new Error(`Interrupt session error: ${err || res.statusText}`)
  }
}
