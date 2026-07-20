import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PermissionRequest } from '../types'
import { fetchPendingRequests, respondToRequest } from '../api/permission'

// 权限请求管理 Store
export const usePermissionStore = defineStore('permission', () => {
  // 待处理的权限请求列表
  const pendingRequests = ref<PermissionRequest[]>([])
  // 是否显示权限对话框
  const showDialog = ref(false)
  // 是否正在轮询
  let polling = false

  // 轮询获取待处理的权限请求
  async function pollPending() {
    try {
      const data = await fetchPendingRequests()
      if (data.pending.length > 0) {
        pendingRequests.value = data.pending
        showDialog.value = true
      }
    } catch {
      // ignore
    }
  }

  // 开始定时轮询权限请求
  function startPolling() {
    if (polling) return
    polling = true
    const interval = setInterval(async () => {
      await pollPending()
      if (!showDialog.value && pendingRequests.value.length === 0) {
        clearInterval(interval)
        polling = false
      }
    }, 1000)
  }

  // 处理收到的权限请求（来自 SSE）
  function handleIncoming(request: PermissionRequest) {
    const existing = pendingRequests.value.find(r => r.id === request.id)
    if (!existing) {
      pendingRequests.value.push(request)
    }
    showDialog.value = true
  }

  // 响应权限请求（允许/拒绝）
  async function respond(requestId: string, decision: 'allowed' | 'denied', remember: boolean = false) {
    await respondToRequest(requestId, decision, remember)
    pendingRequests.value = pendingRequests.value.filter(r => r.id !== requestId)
    if (pendingRequests.value.length === 0) {
      showDialog.value = false
    }
  }

  // 关闭权限对话框
  function dismiss() {
    showDialog.value = false
  }

  return { pendingRequests, showDialog, pollPending, startPolling, handleIncoming, respond, dismiss }
})
