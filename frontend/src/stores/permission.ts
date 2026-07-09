import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { PermissionRequest } from '../types'
import { fetchPendingRequests, respondToRequest } from '../api/permission'

export const usePermissionStore = defineStore('permission', () => {
  const pendingRequests = ref<PermissionRequest[]>([])
  const showDialog = ref(false)
  let polling = false

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

  function handleIncoming(request: PermissionRequest) {
    const existing = pendingRequests.value.find(r => r.id === request.id)
    if (!existing) {
      pendingRequests.value.push(request)
    }
    showDialog.value = true
  }

  async function respond(requestId: string, decision: 'allowed' | 'denied', remember: boolean = false) {
    await respondToRequest(requestId, decision, remember)
    pendingRequests.value = pendingRequests.value.filter(r => r.id !== requestId)
    if (pendingRequests.value.length === 0) {
      showDialog.value = false
    }
  }

  function dismiss() {
    showDialog.value = false
  }

  return { pendingRequests, showDialog, pollPending, startPolling, handleIncoming, respond, dismiss }
})
