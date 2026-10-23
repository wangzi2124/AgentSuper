<script setup lang="ts">
import type { AgentStep } from '../types'
import { cardLabel, formatDuration, getStatusIcon, stepTitle } from '../utils/agentDisplay'

defineProps<{ step: AgentStep }>()
</script>

<template>
  <div class="o-tool" :class="[step.status, { 'is-step': !step.tool_name }]">
    <span class="o-tool-icon" :class="step.status">{{ getStatusIcon(step.status) }}</span>
    <span class="o-tool-name">{{ cardLabel(step) }}</span>
    <span class="o-tool-title">{{ stepTitle(step) }}</span>
    <span v-if="step.duration_ms != null" class="o-tool-time">{{ formatDuration(step.duration_ms) }}</span>
    <span v-else-if="step.status === 'running'" class="o-tool-spin"></span>
  </div>
</template>

<style scoped>
.o-tool {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 10px;
  border-radius: 8px;
  font-size: 13px;
  background: color-mix(in srgb, var(--bg-subtle) 60%, transparent);
  border: 1px solid transparent;
  transition: border-color var(--duration) var(--ease), background var(--duration) var(--ease);
}
.o-tool.running { background: color-mix(in srgb, var(--primary) 8%, transparent); border-color: color-mix(in srgb, var(--primary) 22%, transparent); }
.o-tool.failed { background: var(--danger-soft); border-color: color-mix(in srgb, var(--danger) 22%, transparent); }
.o-tool-icon { font-size: 12px; flex-shrink: 0; width: 12px; text-align: center; color: var(--text-secondary); }
.o-tool-icon.completed { color: var(--success); }
.o-tool-icon.failed { color: var(--danger); }
.o-tool-icon.running { color: var(--primary); }
.o-tool-name {
  font-weight: 600;
  color: var(--primary);
  flex-shrink: 0;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
}
.o-tool.is-step .o-tool-name {
  font-family: inherit;
  color: var(--text-secondary);
}
.o-tool-title {
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
  flex: 1;
}
.o-tool-time { margin-left: auto; color: var(--text-secondary); font-size: 12px; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.o-tool-spin { width: 10px; height: 10px; margin-left: auto; border: 1.5px solid var(--border); border-top-color: var(--primary); border-radius: 50%; animation: spin 0.8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
