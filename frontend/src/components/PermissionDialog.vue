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
        <span class="perm-op">{{ currentRequest.operation === 'write' ? '写入文件' : currentRequest.operation }}</span>
      </div>
      <div class="perm-row">
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

<style scoped>
.perm-card {
  margin: 8px 8px 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 10px 12px;
  font-size: 12px;
  line-height: 1.5;
  animation: slideIn 0.2s ease;
}
@keyframes slideIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

.perm-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.perm-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--bg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  flex-shrink: 0;
}
.perm-head-text {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
.perm-agent {
  font-weight: 600;
  font-size: 12px;
  color: var(--text);
}
.perm-action-label {
  font-size: 11px;
  color: var(--text-secondary);
}
.perm-detail {
  background: var(--bg);
  border-radius: 6px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  margin-bottom: 10px;
}
.perm-row {
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.perm-label {
  color: var(--text-secondary);
  flex-shrink: 0;
  min-width: 28px;
  font-size: 11px;
}
.perm-op {
  font-size: 11px;
  font-weight: 600;
  color: var(--primary);
  background: rgba(79, 70, 229, 0.08);
  padding: 0 6px;
  border-radius: 4px;
}
.perm-path,
.perm-tool {
  font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
  font-size: 11px;
  color: var(--text);
  word-break: break-all;
}
.perm-path {
  background: rgba(0,0,0,0.04);
  padding: 1px 4px;
  border-radius: 3px;
}
.perm-actions {
  display: flex;
  gap: 6px;
}
.btn-perm {
  flex: 1;
  padding: 6px 0;
  border-radius: 6px;
  border: 1px solid var(--border);
  font-size: 11px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  text-align: center;
  background: var(--surface);
  color: var(--text);
}
.btn-perm:hover {
  filter: brightness(0.95);
  transform: translateY(-1px);
}
.btn-perm:active {
  transform: translateY(0);
}
.btn-perm-deny {
  color: var(--text-secondary);
}
.btn-perm-deny:hover {
  background: rgba(239, 68, 68, 0.06);
  border-color: rgba(239, 68, 68, 0.3);
  color: var(--danger);
}
.btn-perm-allow-once {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}
.btn-perm-allow-once:hover {
  background: var(--primary-hover);
  border-color: var(--primary-hover);
}
.btn-perm-allow-always {
  background: transparent;
  color: var(--primary);
  border-style: dashed;
  font-size: 10px;
}
.btn-perm-allow-always:hover {
  background: rgba(79, 70, 229, 0.06);
}
</style>
