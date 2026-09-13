import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PermissionRequest } from '../types'
import {
  fetchPendingRequests,
  respondToRequest,
  fetchWorkspaces,
  addWorkspace as apiAddWorkspace,
  removeWorkspace as apiRemoveWorkspace,
} from '../api/permission'

// 审批超时兜底（与后端 permission_approval_timeout 默认 180s 对齐，仅在后端未下发 expires_at 时用）。
// 正常流程以后端下发的 expires_at 为准，前端照此倒计时。
const APPROVAL_TIMEOUT_SEC = 180
// 提前量：后端达到截止时间后请求变为 stale（再次 respond → 404）。
// 留出 2s 余量让前端的自动拒绝在截止前命中 pending，避免 404 与超时竞态。
const SAFETY_MARGIN_SEC = 2

export const usePermissionStore = defineStore('permission', () => {
  const pendingRequests = ref<PermissionRequest[]>([])
  const workspaces = ref<string[]>([])
  let polling = false
  let expiryTimer: ReturnType<typeof setInterval> | null = null

  // 前端执行自动拒绝的时刻（含提前量），返回毫秒时间戳
  function expiryFor(r: PermissionRequest): number {
    const deadline = r.expires_at
      ? (new Date(r.expires_at).getTime() || Date.now())
      : (new Date(r.created_at).getTime() || Date.now()) + APPROVAL_TIMEOUT_SEC * 1000
    return deadline - SAFETY_MARGIN_SEC * 1000
  }

  // 剩余毫秒（0 表示已到自动拒绝点）
  function remainingFor(r: PermissionRequest): number {
    return Math.max(0, expiryFor(r) - Date.now())
  }

  function syncExpiryTimer() {
    if (pendingRequests.value.length === 0) {
      if (expiryTimer) { clearInterval(expiryTimer); expiryTimer = null }
      return
    }
    if (expiryTimer) return
    expiryTimer = setInterval(() => {
      const expired = pendingRequests.value.filter(r => remainingFor(r) <= 0)
      if (expired.length === 0) return
      for (const r of expired) {
        pendingRequests.value = pendingRequests.value.filter(x => x.id !== r.id)
        // 自动取消（拒绝）：尽力把决定回传给后端；
        // 若后端已自行过期（respond → 404），本地移除即可，不再等人工干涉。
        respondToRequest(r.id, 'denied', false).catch(() => { /* ignore */ })
      }
      syncExpiryTimer()
    }, 500)
  }

  async function pollPending() {
    try {
      const data = await fetchPendingRequests()
      pendingRequests.value = data.pending
      syncExpiryTimer()
    } catch { /* ignore */ }
  }

  function startPolling() {
    if (polling) return
    polling = true
    const interval = setInterval(async () => {
      await pollPending()
      // 服务器已无待处理请求时停止轮询
      if (pendingRequests.value.length === 0) {
        clearInterval(interval)
        polling = false
      }
    }, 1000)
  }

  function handleIncoming(request: PermissionRequest) {
    const existing = pendingRequests.value.find(r => r.id === request.id)
    if (!existing) {
      pendingRequests.value.push(request)
      syncExpiryTimer()
    }
  }

  async function respond(requestId: string, decision: 'allowed' | 'denied', remember = false) {
    // 乐观移除，避免 stale 请求（后端已过期）导致 404 抛错后列表卡死
    pendingRequests.value = pendingRequests.value.filter(r => r.id !== requestId)
    syncExpiryTimer()
    try {
      await respondToRequest(requestId, decision, remember)
    } catch { /* 已过期/已处理时后端返回 404，忽略并保持本地已移除 */ }
  }

  async function loadWorkspaces() {
    try {
      const data = await fetchWorkspaces()
      workspaces.value = data.workspaces
    } catch { /* ignore */ }
  }

  async function addWorkspace(path: string) {
    const data = await apiAddWorkspace(path)
    workspaces.value = data.workspaces
  }

  async function removeWorkspace(path: string) {
    const data = await apiRemoveWorkspace(path)
    workspaces.value = data.workspaces
  }

  return { pendingRequests, workspaces, expiryFor, remainingFor, pollPending, startPolling, handleIncoming, respond, loadWorkspaces, addWorkspace, removeWorkspace }
})