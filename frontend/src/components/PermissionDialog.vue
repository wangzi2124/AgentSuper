<script setup lang="ts">
import { computed, ref, watch, onBeforeUnmount } from 'vue'
import { usePermissionStore } from '../stores/permission'

const perm = usePermissionStore()
const currentRequest = computed(() => perm.pendingRequests[0] ?? null)

const remainingMs = ref(0)
const totalMs = ref(60000)
let ticker: ReturnType<typeof setInterval> | null = null

function stopTicker() {
  if (ticker) { clearInterval(ticker); ticker = null }
}

watch(currentRequest, (req) => {
  stopTicker()
  if (!req) { remainingMs.value = 0; return }
  const deadline = perm.expiryFor(req)
  totalMs.value = Math.max(1000, deadline - (new Date(req.created_at).getTime() || Date.now()))
  remainingMs.value = Math.max(0, deadline - Date.now())
  ticker = setInterval(() => {
    remainingMs.value = Math.max(0, deadline - Date.now())
  }, 500)
})

const remainingSec = computed(() => Math.ceil(remainingMs.value / 1000))
const barPct = computed(() => totalMs.value ? Math.min(100, (remainingMs.value / totalMs.value) * 100) : 0)

function allow(remember: boolean = false) {
  if (!currentRequest.value) return
  perm.respond(currentRequest.value.id, 'allowed', remember)
}

function deny() {
  if (!currentRequest.value) return
  perm.respond(currentRequest.value.id, 'denied')
}

onBeforeUnmount(stopTicker)
</script>

<template>
  <div v-if="currentRequest" class="perm-card">
    <div class="perm-head">
      <span class="perm-avatar">🤖</span>
      <div class="perm-head-text">
        <span class="perm-agent">AI 助手</span>
        <span class="perm-action-label">需要权限确认</span>
      </div>
    </div>
    <div class="perm-detail">
      <div class="perm-row">
        <span class="perm-label">操作</span>
        <span class="perm-op">{{
          currentRequest.operation === 'command' ? '执行命令' :
          currentRequest.operation === 'write' ? '写入文件' :
          currentRequest.operation === 'read' ? '读取文件' :
          currentRequest.operation
        }}</span>
      </div>
      <div v-if="currentRequest.operation === 'command'" class="perm-row">
        <span class="perm-label">命令</span>
        <code class="perm-cmd">{{ currentRequest.tool_args?.command || currentRequest.path }}</code>
      </div>
      <div v-else class="perm-row">
        <span class="perm-label">路径</span>
        <code class="perm-path">{{ currentRequest.path }}</code>
      </div>
      <div class="perm-row">
        <span class="perm-label">工具</span>
        <code class="perm-tool">{{ currentRequest.tool_name }}</code>
      </div>
    </div>
    <div class="perm-countdown-row">
      <div class="perm-countdown-track">
        <div class="perm-countdown-bar" :style="{ width: barPct + '%' }"></div>
      </div>
      <span class="perm-countdown-hint">未操作将在 {{ remainingSec }} 秒后自动拒绝</span>
    </div>
    <div class="perm-actions">
      <button class="btn-perm btn-perm-deny" @click="deny">拒绝</button>
      <button class="btn-perm btn-perm-allow-once" @click="allow(false)">允许本次</button>
      <button class="btn-perm btn-perm-allow-always" @click="allow(true)">允许并记住</button>
    </div>
  </div>
</template>


<style scoped src="../styles/chat/permissionDialog.css"></style>