<script setup lang="ts">
import type { AgentStep } from '../types'
import { cardLabel, formatDuration, getStatusIcon, stepTitle } from '../utils/agentDisplay'

const props = defineProps<{ step: AgentStep; agentName?: string }>()
</script>

<template>
  <details class="tool-row" :class="step.status">
    <summary class="tool-summary">
      <span class="tool-dot" :class="step.status">{{ getStatusIcon(step.status) }}</span>
      <span class="tool-name">{{ step.tool_name || cardLabel(step) }}</span>
      <span class="tool-title">{{ stepTitle(step) }}</span>
      <span v-if="agentName" class="tool-agent">{{ agentName }}</span>
      <span v-if="step.duration_ms != null" class="tool-time">{{ formatDuration(step.duration_ms) }}</span>
      <span v-else-if="step.status === 'running'" class="tool-spin" aria-label="运行中"></span>
    </summary>
    <div class="tool-detail">
      <div v-if="step.tool_args && Object.keys(step.tool_args).length" class="tool-block">
        <span class="tool-label">input</span>
        <pre>{{ JSON.stringify(step.tool_args, null, 2) }}</pre>
      </div>
      <div v-if="step.tool_result || step.tool_output" class="tool-block">
        <span class="tool-label">output</span>
        <pre>{{ step.tool_result || step.tool_output }}</pre>
      </div>
      <div v-if="step.detail && !step.tool_result" class="tool-block">
        <span class="tool-label">detail</span>
        <pre>{{ step.detail }}</pre>
      </div>
    </div>
  </details>
</template>

<style scoped>
.tool-row { border-radius: 8px; border: 1px solid transparent; background: color-mix(in srgb, var(--bg-subtle) 55%, transparent); }
.tool-row.running { border-color: color-mix(in srgb, var(--primary) 22%, transparent); background: color-mix(in srgb, var(--primary) 6%, transparent); }
.tool-row.failed { border-color: color-mix(in srgb, var(--danger) 24%, transparent); background: var(--danger-soft); }
.tool-summary { display: flex; align-items: center; gap: 8px; padding: 5px 10px; cursor: pointer; list-style: none; font-size: 13px; }
.tool-summary::-webkit-details-marker { display: none; }
.tool-dot { width: 14px; text-align: center; flex-shrink: 0; color: var(--text-secondary); }
.tool-dot.completed { color: var(--success); }
.tool-dot.failed { color: var(--danger); }
.tool-dot.running { color: var(--primary); }
.tool-name { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12px; font-weight: 600; color: var(--primary); flex-shrink: 0; }
.tool-title { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--text-secondary); }
.tool-agent { flex-shrink: 0; font-size: 11px; color: var(--text-muted); border: 1px solid var(--border); border-radius: 999px; padding: 0 6px; }
.tool-time { flex-shrink: 0; font-size: 12px; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.tool-spin { width: 10px; height: 10px; border: 1.5px solid var(--border); border-top-color: var(--primary); border-radius: 50%; animation: spin .8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.tool-detail { padding: 0 10px 10px; display: flex; flex-direction: column; gap: 8px; }
.tool-block { display: flex; flex-direction: column; gap: 4px; }
.tool-label { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--text-muted); }
.tool-block pre { margin: 0; padding: 8px 10px; max-height: 280px; overflow: auto; border-radius: 6px; background: var(--bg); border: 1px solid var(--border); font-size: 12px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
@media (prefers-reduced-motion: reduce) { .tool-spin { animation: none; } }
</style>
