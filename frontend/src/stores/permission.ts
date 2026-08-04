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

export const usePermissionStore = defineStore('permission', () => {
  const pendingRequests = ref<PermissionRequest[]>([])
  const workspaces = ref<string[]>([])
  let polling = false

  async function pollPending() {
    try {
      const data = await fetchPendingRequests()
      pendingRequests.value = data.pending
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
    }
  }

  async function respond(requestId: string, decision: 'allowed' | 'denied', remember = false) {
    await respondToRequest(requestId, decision, remember)
    pendingRequests.value = pendingRequests.value.filter(r => r.id !== requestId)
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

  return { pendingRequests, workspaces, pollPending, startPolling, handleIncoming, respond, loadWorkspaces, addWorkspace, removeWorkspace }
})
