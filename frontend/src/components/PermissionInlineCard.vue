<script setup lang="ts">
import { computed } from 'vue'
import { usePermissionStore } from '../stores/permission'

const perm = usePermissionStore()
const request = computed(() => perm.pendingRequests[0] ?? null)
function allow() {
  if (request.value) perm.respond(request.value.id, 'allowed')
}
function deny() {
  if (request.value) perm.respond(request.value.id, 'denied')
}
</script>

<template>
  <div v-if="request" class="perm-inline" role="alertdialog" aria-live="assertive">
    <div class="perm-inline-main">
      <span class="perm-icon">!</span>
      <div class="perm-copy">
        <div class="perm-title">
          需要确认：{{ request.operation === 'command' ? '执行命令' : request.operation === 'write' ? '写入文件' : '读取文件' }}
        </div>
        <code class="perm-target">{{ request.tool_args?.command || request.path }}</code>
        <span class="perm-tool">{{ request.tool_name }}</span>
      </div>
    </div>
    <div class="perm-actions">
      <button type="button" class="perm-btn deny" @click="deny">拒绝</button>
      <button type="button" class="perm-btn once" @click="allow">允许本回合</button>
    </div>
  </div>
</template>

<style scoped>
.perm-inline { display: flex; align-items: center; gap: 12px; width: 100%; max-width: 920px; margin: 0 auto 8px; padding: 10px 12px; border: 1px solid color-mix(in srgb, var(--warning, #d97706) 35%, var(--border)); border-radius: 10px; background: color-mix(in srgb, var(--warning, #d97706) 7%, var(--surface-elevated)); }
.perm-inline-main { display: flex; align-items: flex-start; gap: 9px; min-width: 0; flex: 1; }
.perm-icon { width: 20px; height: 20px; border-radius: 50%; background: var(--warning, #d97706); color: #fff; display: inline-flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; }
.perm-copy { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.perm-title { font-size: 12px; font-weight: 600; color: var(--text); }
.perm-target { font-size: 11px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
.perm-tool { font-size: 10px; color: var(--text-muted); font-family: 'JetBrains Mono', Consolas, monospace; }
.perm-actions { display: flex; gap: 6px; flex-shrink: 0; }
.perm-btn { border: 1px solid var(--border); background: var(--surface); color: var(--text-secondary); border-radius: 7px; padding: 5px 9px; font: inherit; font-size: 12px; cursor: pointer; }
.perm-btn.once { color: var(--primary); border-color: color-mix(in srgb, var(--primary) 35%, var(--border)); }
.perm-btn.deny { color: var(--danger); }
@media (max-width: 640px) { .perm-inline { flex-direction: column; align-items: stretch; } .perm-actions { justify-content: flex-end; } }
</style>
