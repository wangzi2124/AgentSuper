<script setup lang="ts">
import { ref } from 'vue'
import type { MultiAgentMessage, AgentStep } from '../types'

const props = defineProps<{
  message: MultiAgentMessage
  routingStatus: string
  isLast: boolean
}>()

defineEmits<{ undo: [] }>()

const expandedResults = ref<Record<string, boolean>>({})
const expandedArgs = ref<Record<string, boolean>>({})

function toggleResult(key: string) {
  expandedResults.value[key] = !expandedResults.value[key]
}

function toggleArgs(key: string) {
  expandedArgs.value[key] = !expandedArgs.value[key]
}

function getStatusIcon(status: string): string {
  if (status === 'completed') return '✅'
  if (status === 'failed') return '❌'
  return '⏳'
}

function formatDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatArgs(args: Record<string, unknown>): string {
  return JSON.stringify(args, null, 2)
}

function argsSummary(args: Record<string, unknown>): string {
  const keys = Object.keys(args)
  if (keys.length === 0) return '()'
  const parts = keys.map(k => {
    const v = args[k]
    if (typeof v === 'string') return v.length > 30 ? v.slice(0, 30) + '...' : v
    return String(v)
  })
  return '(' + parts.join(', ').slice(0, 60) + ')'
}

function stepKey(a: string, s: AgentStep): string {
  return `${a}:${s.step_id}`
}
</script>

<template>
  <div class="response" :class="{ loading: isLast && !!routingStatus, error: message.isError }">
    <!-- routing -->
    <div v-if="isLast && routingStatus" class="routing">
      <span class="spinner"></span>
      {{ routingStatus }}
    </div>

    <!-- per-agent panels -->
    <div v-if="message.agents.length" class="agents">
      <div v-for="a in message.agents" :key="a.agent_id" class="agent" :class="a.status">
        <div class="agent-h">
          <span class="avatar">{{ a.agent_avatar || '🤖' }}</span>
          <span class="name">{{ a.agent_name }}</span>
          <span class="badge" :class="a.status">{{ a.status === 'running' ? '● running' : a.status === 'completed' ? '✓ done' : '✗ failed' }}</span>
        </div>

        <!-- realtime steps -->
        <div v-if="a.steps.length" class="steps">
          <div v-for="s in a.steps" :key="s.step_id" class="step" :class="s.status">
            <span class="step-icon">{{ getStatusIcon(s.status) }}</span>
            <div class="step-content">
              <div class="step-top">
                <span class="step-name">{{ s.name }}</span>
                <span v-if="s.detail" class="step-detail">{{ s.detail }}</span>
                <span v-if="s.duration_ms != null" class="step-duration">{{ formatDuration(s.duration_ms) }}</span>
                <span v-else-if="s.status === 'running'" class="step-duration spinning">...</span>
              </div>
              <div v-if="s.tool_name" class="step-tool">
                🔧 {{ s.tool_name }}
                <span v-if="s.tool_args && Object.keys(s.tool_args).length" class="step-toggle" @click.stop="toggleArgs(stepKey(a.agent_id, s))">
                  {{ expandedArgs[stepKey(a.agent_id, s)] ? '收起参数' : argsSummary(s.tool_args) }}
                </span>
                <span v-if="s.tool_result && s.status === 'completed'" class="step-toggle" @click.stop="toggleResult(stepKey(a.agent_id, s))">
                  {{ expandedResults[stepKey(a.agent_id, s)] ? '收起结果' : '查看结果' }}
                </span>
              </div>
            </div>
            <div v-if="s.tool_args && expandedArgs[stepKey(a.agent_id, s)]" class="step-box" @click.stop>
              <div class="step-box-label">参数</div>
              <pre>{{ formatArgs(s.tool_args) }}</pre>
            </div>
            <div v-if="s.tool_result && expandedResults[stepKey(a.agent_id, s)]" class="step-box" @click.stop>
              <div class="step-box-label">结果</div>
              <pre>{{ s.tool_result }}</pre>
            </div>
          </div>
        </div>
        <div v-else-if="a.status === 'running'" class="steps">
          <div class="step pending">
            <span class="step-icon">⏳</span>
            <div class="step-content">
              <span class="step-name">正在初始化...</span>
            </div>
            <span class="step-duration spinning">...</span>
          </div>
        </div>

        <div v-if="a.content" class="text" v-text="a.content"></div>
        <div v-if="a.error" class="err">⚠️ {{ a.error }}</div>
      </div>
    </div>

    <!-- final answer -->
    <div v-if="message.content && (!message.agents.length || !isLast)" class="text" v-text="message.content"></div>

    <!-- error -->
    <div v-if="message.isError && message.errorInfo" class="err">
      ⚠️ {{ message.errorInfo.message }}
    </div>
  </div>
</template>

<style scoped>
.response { font-size: 14px; line-height: 1.6; }
.routing { display: flex; align-items: center; gap: 8px; padding: 8px 0; font-size: 13px; color: var(--text-secondary); }
.spinner { width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--primary); border-radius: 50%; animation: spin 0.8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.text { white-space: pre-wrap; word-break: break-word; }
.agents { display: flex; flex-direction: column; gap: 8px; }
.agent { border: 1px solid var(--border); border-radius: 10px; padding: 12px; }
.agent-h { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; font-size: 13px; }
.avatar { font-size: 18px; }
.name { font-weight: 600; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; margin-left: auto; }
.badge.running { background: rgba(99,102,241,0.12); color: var(--primary); }
.badge.completed { background: rgba(34,197,94,0.12); color: #22c55e; }
.badge.failed { background: rgba(239,68,68,0.12); color: #ef4444; }
.steps { display: flex; flex-direction: column; gap: 4px; margin: 6px 0 2px; }
.step { display: flex; flex-wrap: wrap; align-items: flex-start; gap: 8px; padding: 6px 8px; border-radius: 6px; background: var(--bg); font-size: 12px; }
.step.running { border-left: 3px solid var(--primary); }
.step.completed { border-left: 3px solid var(--success); }
.step.failed { border-left: 3px solid var(--danger); }
.step.pending { opacity: 0.6; }
.step-icon { font-size: 12px; }
.step-content { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.step-top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.step-name { font-weight: 500; color: var(--text); }
.step-detail { font-size: 11px; color: var(--text-secondary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.step-duration { font-size: 11px; color: var(--text-secondary); font-variant-numeric: tabular-nums; white-space: nowrap; }
.step-duration.spinning { color: var(--primary); animation: spin 0.8s linear infinite; }
.step-tool { font-size: 11px; color: var(--primary); font-family: monospace; }
.step-toggle { color: var(--text-secondary); cursor: pointer; text-decoration: underline; margin-left: 4px; font-family: inherit; }
.step-toggle:hover { color: var(--primary); }
.step-box { margin: 4px 0 0 20px; padding: 6px 8px; background: var(--surface); border: 1px solid var(--border); border-radius: 6px; max-height: 200px; overflow-y: auto; width: calc(100% - 20px); }
.step-box-label { font-size: 10px; font-weight: 600; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 4px; }
.step-box pre { margin: 0; font-size: 11px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; color: var(--text); font-family: monospace; }
.err { padding: 8px 12px; background: rgba(239,68,68,0.06); border: 1px solid rgba(239,68,68,0.2); border-radius: 8px; font-size: 13px; color: #ef4444; margin-top: 4px; }
</style>
