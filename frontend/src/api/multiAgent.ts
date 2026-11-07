import type { TurnDiff } from '../types'
import { apiRequest } from './errors'

const BASE = '/api/chat'

/** [撤回改动] 恢复某条 assistant 消息对应轮次的文件改动（内部 before_tree + 外部文件写回） */
export async function restoreSnapshot(conversationId: string, messageId: string): Promise<{
  restored: boolean
  already?: boolean
  internal?: number
  external?: number
  restored_files?: string[]
  missing?: string[]
}> {
  return apiRequest(BASE + '/multi-agent/restore-snapshot', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, message_id: messageId }),
  }, true) as Promise<{
    restored: boolean
    already?: boolean
    internal?: number
    external?: number
    restored_files?: string[]
    missing?: string[]
  }>
}

/** [查看改动] 渲染某条 assistant 消息对应轮次的 diff 文本（聊天卡片展开用） */
export async function fetchTurnDiff(
  conversationId: string,
  messageId: string,
  file?: string,
  step?: number,
): Promise<TurnDiff> {
  return apiRequest(BASE + '/multi-agent/diff', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      conversation_id: conversationId,
      message_id: messageId,
      file: file || '',
      step: step == null ? null : step,
    }),
  }, true) as Promise<TurnDiff>
}
