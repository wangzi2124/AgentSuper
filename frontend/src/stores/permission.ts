import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PermissionRequest } from '../types'
import { fetchPendingRequests, respondToRequest } from '../api/permission'

export const usePermissionStore = defineStore('permission', () => {
  const pendingRequests = ref<PermissionRequest[]>([])
  let polling = false

  async function pollPending() {
    try {
      const data = await fetchPendingRequests()
      if (data.pending.length > 0) {
        pendingRequests.value = data.pending
      }
    } catch { /* ignore */ }
  }

  function startPolling() {
    if (polling) return
    polling = true
    const interval = setInterval(async () => {
      await pollPending()
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

  return { pendingRequests, pollPending, startPolling, handleIncoming, respond }
})
