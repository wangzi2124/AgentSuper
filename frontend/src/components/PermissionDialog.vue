<script setup lang="ts">
import { computed } from 'vue'
import { usePermissionStore } from '../stores/permission'

// 权限状态管理
const perm = usePermissionStore()

// 当前待处理的权限请求
const currentRequest = computed(() => perm.pendingRequests[0] ?? null)

// 允许权限请求
function allow(remember: boolean = false) {
  if (!currentRequest.value) return
  perm.respond(currentRequest.value.id, 'allowed', remember)
}

// 拒绝权限请求
function deny() {
  if (!currentRequest.value) return
  perm.respond(currentRequest.value.id, 'denied')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="perm.showDialog && currentRequest" class="overlay" @click.self="perm.dismiss">
      <div class="dialog">
        <div class="dialog-header">
          <span class="icon">🔒</span>
          <h3>需要权限确认</h3>
        </div>
        <div class="dialog-body">
          <p>AI 助手想要执行以下操作：</p>
          <div class="detail">
            <div class="row">
              <span class="label">操作</span>
              <span class="value op-badge" :class="currentRequest.operation">
                {{ currentRequest.operation === 'write' ? '写入文件' : currentRequest.operation }}
              </span>
            </div>
            <div class="row">
              <span class="label">路径</span>
              <span class="value path">{{ currentRequest.path }}</span>
            </div>
            <div class="row">
              <span class="label">工具</span>
              <span class="value">{{ currentRequest.tool_name }}</span>
            </div>
          </div>
        </div>
        <div class="dialog-footer">
          <div class="remember-row">
            <button class="btn btn-allow-session" @click="allow(true)">
              <span class="btn-icon">✅</span>
              允许并记住此路径
            </button>
          </div>
          <div class="action-row">
            <button class="btn btn-deny" @click="deny">拒绝</button>
            <button class="btn btn-allow" @click="allow(false)">允许本次</button>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}
.dialog {
  background: var(--surface, #fff);
  border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2);
  width: 480px;
  max-width: 90vw;
  overflow: hidden;
}
.dialog-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 20px 24px 0;
}
.dialog-header .icon { font-size: 24px; }
.dialog-header h3 { margin: 0; font-size: 18px; }
.dialog-body {
  padding: 16px 24px;
}
.dialog-body p {
  margin: 0 0 12px;
  font-size: 14px;
  color: var(--text-secondary, #666);
}
.detail {
  background: var(--bg, #f5f5f5);
  border-radius: 8px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
}
.label {
  color: var(--text-secondary, #666);
  flex-shrink: 0;
  min-width: 48px;
}
.value {
  word-break: break-all;
  color: var(--text, #333);
}
.path {
  font-family: monospace;
  font-size: 12px;
  background: rgba(0,0,0,0.05);
  padding: 2px 6px;
  border-radius: 4px;
}
.op-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 600;
}
.op-badge.write { background: #fff3cd; color: #856404; }
.dialog-footer {
  padding: 16px 24px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.remember-row { 
  display: flex; 
  justify-content: center; 
}
.action-row {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}
.btn {
  padding: 8px 20px;
  border-radius: 8px;
  border: 1px solid var(--border, #ddd);
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 6px;
  transition: all 0.15s;
}
.btn-icon { font-size: 16px; }
.btn:hover { transform: translateY(-1px); }
.btn-deny {
  background: var(--bg, #f5f5f5);
  color: var(--text, #333);
}
.btn-deny:hover { background: #fee; color: #c00; }
.btn-allow {
  background: var(--primary, #4f46e5);
  color: #fff;
  border-color: var(--primary, #4f46e5);
}
.btn-allow:hover { opacity: 0.9; }
.btn-allow-session {
  background: transparent;
  color: var(--primary, #4f46e5);
  border: 1px dashed var(--primary, #4f46e5);
  font-size: 13px;
}
.btn-allow-session:hover { background: rgba(79, 70, 229, 0.05); }
</style>
