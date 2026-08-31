<script setup lang="ts">
import { computed } from 'vue'
import { usePermissionStore } from '../stores/permission'

const perm = usePermissionStore()
const currentRequest = computed(() => perm.pendingRequests[0] ?? null)

function allow(remember: boolean = false) {
  if (!currentRequest.value) return
  perm.respond(currentRequest.value.id, 'allowed', remember)
}

function deny() {
  if (!currentRequest.value) return
  perm.respond(currentRequest.value.id, 'denied')
}
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
    <div class="perm-actions">
      <button class="btn-perm btn-perm-deny" @click="deny">拒绝</button>
      <button class="btn-perm btn-perm-allow-once" @click="allow(false)">允许本次</button>
      <button class="btn-perm btn-perm-allow-always" @click="allow(true)">允许并记住</button>
    </div>
  </div>
</template>


<style scoped src="../styles/chat/permissionDialog.css"></style>
