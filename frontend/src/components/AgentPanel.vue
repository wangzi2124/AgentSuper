<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AgentStreamData } from '../types'

const props = defineProps<{ agent: AgentStreamData }>()

const stepsExpanded = ref(false)
const expandedResults = ref<Record<string, boolean>>({})
const expandedArgs = ref<Record<string, boolean>>({})

const isRunning = computed(() => props.agent.status === 'running')
const isCompleted = computed(() => props.agent.status === 'completed')
const isFailed = computed(() => props.agent.status === 'failed')

function getStatusIcon(s: string): string {
  if (s === 'completed') return '✅'
  if (s === 'failed') return '❌'
  return '⏳'
}

function formatDuration(ms?: number): string {
  if (ms == null) return ''
  return ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(1)}s`
}

function formatArgs(args: Record<string, unknown>): string {
  return JSON.stringify(args, null, 2)
}

function argsSummary(args: Record<string, unknown>): string {
  const keys = Object.keys(args)
  if (keys.length === 0) return ''
  return keys.map(k => { const v = args[k]; return typeof v === 'string' ? (v.length > 25 ? v.slice(0, 25) + '...' : v) : String(v) }).join(', ').slice(0, 50)
}

function toggleResult(id: string) { expandedResults.value[id] = !expandedResults.value[id] }
function toggleArgs(id: string) { expandedArgs.value[id] = !expandedArgs.value[id] }
</script>

<template>
  <div class="agent-panel" :class="{ running: isRunning, completed: isCompleted, failed: isFailed }">
    <div class="agent-header">
      <span class="agent-avatar">{{ agent.agent_avatar || '🤖' }}</span>
      <span class="agent-name">{{ agent.agent_name }}</span>
      <span v-if="isRunning" class="agent-status">● running</span>
      <span v-else-if="isCompleted" class="agent-status done">✓ done</span>
      <span v-else-if="isFailed" class="agent-status err">✗ failed</span>
    </div>

    <div v-if="agent.steps.length" class="agent-steps">
      <div class="steps-header" @click="stepsExpanded = !stepsExpanded">
        <span class="steps-toggle">{{ stepsExpanded ? '▾' : '▸' }}</span>
        <span class="steps-label">Steps ({{ agent.steps.length }})</span>
      </div>
      <div v-if="stepsExpanded" class="steps-list">
        <div v-for="s in agent.steps" :key="s.step_id" class="step-item" :class="s.status">
          <div class="step-row">
            <span class="step-icon">{{ getStatusIcon(s.status) }}</span>
            <span class="step-name">{{ s.name }}</span>
            <span v-if="s.detail" class="step-detail">{{ s.detail }}</span>
            <span v-if="s.duration_ms != null" class="step-time">{{ formatDuration(s.duration_ms) }}</span>
          </div>
          <div v-if="s.tool_name" class="step-tool">
            🔧 {{ s.tool_name }}
            <span v-if="s.tool_args && Object.keys(s.tool_args).length" class="step-args-toggle" @click.stop="toggleArgs(s.step_id)">
              {{ expandedArgs[s.step_id] ? 'hide args' : argsSummary(s.tool_args) }}
            </span>
          </div>
          <div v-if="s.tool_args && expandedArgs[s.step_id]" class="step-args" @click.stop>
            <pre>{{ formatArgs(s.tool_args) }}</pre>
          </div>
          <div v-if="s.tool_result" class="step-result-toggle" @click.stop="toggleResult(s.step_id)">
            {{ expandedResults[s.step_id] ? 'hide result' : 'view result' }}
          </div>
          <div v-if="s.tool_result && expandedResults[s.step_id]" class="step-result" @click.stop>
            <pre>{{ s.tool_result }}</pre>
          </div>
        </div>
      </div>
    </div>

    <div v-if="isRunning && !agent.content" class="agent-thinking">
      <div class="dot-pulse"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
      <span>Thinking...</span>
    </div>

    <div v-if="agent.content" class="agent-content" v-text="agent.content"></div>
    <div v-if="isFailed && agent.error" class="agent-error">⚠️ {{ agent.error }}</div>
  </div>
</template>

<style scoped>
.agent-panel { border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin-bottom: 12px; background: var(--surface); transition: border-color 0.2s; }
.agent-panel.running { border-left: 3px solid var(--primary); }
.agent-panel.completed { border-left: 3px solid #22c55e; }
.agent-panel.failed { border-left: 3px solid #ef4444; }
.agent-header { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.agent-avatar { font-size: 20px; }
.agent-name { font-weight: 600; font-size: 14px; color: var(--text); }
.agent-status { font-size: 11px; padding: 2px 8px; border-radius: 10px; margin-left: auto; background: rgba(99,102,241,0.12); color: var(--primary); animation: pulse 1.5s infinite; }
.agent-status.done { background: rgba(34,197,94,0.12); color: #22c55e; animation: none; }
.agent-status.err { background: rgba(239,68,68,0.12); color: #ef4444; animation: none; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.5; } }
.agent-steps { margin-bottom: 8px; }
.steps-header { display: flex; align-items: center; gap: 6px; padding: 4px 0; cursor: pointer; font-size: 12px; color: var(--text-secondary); user-select: none; }
.steps-header:hover { opacity: 0.8; }
.steps-toggle { font-size: 10px; width: 12px; }
.steps-label { font-weight: 500; }
.steps-list { display: flex; flex-direction: column; gap: 4px; padding: 4px 0 4px 16px; }
.step-item { padding: 4px 6px; border-radius: 4px; font-size: 12px; }
.step-item.completed { background: rgba(16,185,129,0.04); }
.step-item.failed { background: rgba(239,68,68,0.04); }
.step-item.running { background: rgba(99,102,241,0.04); }
.step-row { display: flex; align-items: center; gap: 6px; }
.step-icon { font-size: 11px; flex-shrink: 0; }
.step-name { font-weight: 500; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.step-detail { color: var(--text-secondary); font-size: 11px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-time { margin-left: auto; color: var(--text-secondary); font-size: 11px; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.step-tool { font-size: 11px; color: var(--primary); font-family: monospace; margin-top: 2px; padding-left: 17px; }
.step-args-toggle { color: var(--text-secondary); cursor: pointer; text-decoration: underline; margin-left: 4px; font-family: inherit; }
.step-args { margin: 4px 0 0 17px; padding: 6px 8px; background: #f8f9fa; border: 1px solid var(--border); border-radius: 4px; max-height: 200px; overflow-y: auto; }
.step-args pre { margin: 0; font-size: 11px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; color: var(--text); font-family: monospace; }
.step-result-toggle { font-size: 11px; color: var(--primary); cursor: pointer; text-decoration: underline; margin-top: 2px; padding-left: 17px; }
.step-result { margin: 4px 0 0 17px; padding: 6px 8px; background: var(--bg); border: 1px solid var(--border); border-radius: 4px; max-height: 200px; overflow-y: auto; }
.step-result pre { margin: 0; font-size: 11px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; color: var(--text-secondary); font-family: monospace; }
.agent-thinking { display: flex; align-items: center; gap: 8px; padding: 8px 0; font-size: 13px; color: var(--text-secondary); }
.dot-pulse { display: flex; gap: 4px; }
.dot { width: 6px; height: 6px; border-radius: 50%; background: var(--primary); animation: bounce 1.4s infinite ease-in-out; }
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce { 0%,80%,100% { opacity: 0.3; transform: scale(0.8); } 40% { opacity: 1; transform: scale(1); } }
.agent-content { white-space: pre-wrap; word-break: break-word; font-size: 14px; line-height: 1.6; padding: 8px 0; }
.agent-error { margin-top: 8px; padding: 8px 12px; background: rgba(239,68,68,0.06); border: 1px solid rgba(239,68,68,0.2); border-radius: 8px; font-size: 13px; color: #ef4444; }
</style>
