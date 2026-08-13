<script setup lang="ts">
import { ref, computed } from 'vue'
import type { MultiAgentMessage, AgentStep } from '../types'

const props = defineProps<{
  message: MultiAgentMessage
  routingStatus: string
  isLast: boolean
}>()

defineEmits<{ undo: [] }>()

// 单 Agent 路由时，最终答案与 Agent 面板内容完全一致，
// 再渲染一遍会造成"两条最终答案"的重复。仅在内容有新增信息时才展示。
const showFinalAnswer = computed(() => {
  const content = props.message.content
  if (!content) return false
  const agents = props.message.agents
  if (agents.length === 1 && agents[0].content && agents[0].content.trim() === content.trim()) {
    return false
  }
  return true
})

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
          <span class="badge" :class="a.status">{{ a.status === 'running' ? '● 运行中' : a.status === 'completed' ? '✓ 完成' : '✗ 失败' }}</span>
        </div>

        <!-- realtime steps -->
        <div v-if="a.steps.length" class="steps">
          <div v-for="s in a.steps" :key="s.step_id" class="step-item" :class="s.status">
            <div class="step-row">
              <span class="step-icon">{{ getStatusIcon(s.status) }}</span>
              <span class="step-name">{{ s.name }}</span>
              <span v-if="s.detail" class="step-detail">{{ s.detail }}</span>
              <span v-if="s.duration_ms != null" class="step-time">{{ formatDuration(s.duration_ms) }}</span>
              <span v-else-if="s.status === 'running'" class="step-time spinning">...</span>
            </div>
            <div v-if="s.tool_name" class="step-tool">
              🔧 {{ s.tool_name }}
              <span v-if="s.tool_args && Object.keys(s.tool_args).length" class="step-args-toggle" @click.stop="toggleArgs(stepKey(a.agent_id, s))">
                {{ expandedArgs[stepKey(a.agent_id, s)] ? '收起参数' : argsSummary(s.tool_args) }}
              </span>
              <span v-if="s.tool_result && s.status === 'completed'" class="step-result-toggle" @click.stop="toggleResult(stepKey(a.agent_id, s))">
                {{ expandedResults[stepKey(a.agent_id, s)] ? '收起结果' : '查看结果' }}
              </span>
            </div>
            <div v-if="s.tool_args && expandedArgs[stepKey(a.agent_id, s)]" class="step-args" @click.stop>
              <pre>{{ formatArgs(s.tool_args) }}</pre>
            </div>
            <div v-if="s.tool_result && expandedResults[stepKey(a.agent_id, s)]" class="step-result" @click.stop>
              <pre>{{ s.tool_result }}</pre>
            </div>
          </div>
        </div>
        <div v-else-if="a.status === 'running'" class="steps">
          <div class="step-item running">
            <div class="step-row">
              <span class="step-icon">⏳</span>
              <span class="step-name">正在初始化...</span>
              <span class="step-time spinning">...</span>
            </div>
          </div>
        </div>

        <div v-if="a.content" class="text" v-text="a.content"></div>
        <div v-if="a.error" class="err">⚠️ {{ a.error }}</div>
      </div>
    </div>

    <!-- final answer -->
    <div v-if="showFinalAnswer" class="text" v-text="message.content"></div>

    <!-- error -->
    <div v-if="message.isError && message.errorInfo" class="err">
      ⚠️ {{ message.errorInfo.message }}
    </div>
  </div>
</template>

<style scoped>
.response { font-size: 15px; line-height: 1.8; }
.routing { display: flex; align-items: center; gap: 8px; padding: 8px 0; font-size: 13px; color: var(--text-secondary); }
.spinner { width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--primary); border-radius: 50%; animation: spin 0.8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.text { white-space: pre-wrap; word-break: break-word; }
.agents { display: flex; flex-direction: column; gap: 12px; }
.agent { border: 1px solid var(--border); border-radius: 12px; padding: 16px 18px; background: var(--bg); }
.agent-h { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 15px; }
.avatar { font-size: 22px; }
.name { font-weight: 600; }
.badge { font-size: 13px; padding: 3px 10px; border-radius: 10px; margin-left: auto; }
.badge.running { background: rgba(99,102,241,0.12); color: var(--primary); }
.badge.completed { background: rgba(34,197,94,0.12); color: #22c55e; }
.badge.failed { background: rgba(239,68,68,0.12); color: #ef4444; }
.steps { display: flex; flex-direction: column; gap: 8px; margin: 10px 0 4px; }
.step-item { padding: 8px 10px; border-radius: 8px; font-size: 15px; }
.step-item.completed { background: rgba(16, 185, 129, 0.04); }
.step-item.failed { background: rgba(239, 68, 68, 0.04); }
.step-item.running { background: rgba(99, 102, 241, 0.04); }
.step-row { display: flex; align-items: center; gap: 8px; }
.step-icon { font-size: 15px; flex-shrink: 0; }
.step-name { font-weight: 500; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.step-detail { color: var(--text-secondary); font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.step-time { margin-left: auto; color: var(--text-secondary); font-size: 14px; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.step-time.spinning { color: var(--primary); animation: spin 0.8s linear infinite; }
.step-tool { font-size: 14px; color: var(--primary); font-family: monospace; margin-top: 4px; padding-left: 23px; }
.step-args-toggle { color: var(--text-secondary); cursor: pointer; text-decoration: underline; margin-left: 4px; font-family: inherit; }
.step-args-toggle:hover { color: var(--primary); }
.step-args { margin: 4px 0 0 23px; padding: 8px 10px; background: #f8f9fa; border: 1px solid var(--border); border-radius: 6px; max-height: 200px; overflow-y: auto; }
.step-args pre { margin: 0; font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; color: var(--text); font-family: monospace; }
.step-result-toggle { font-size: 14px; color: var(--primary); cursor: pointer; text-decoration: underline; margin-top: 4px; padding-left: 23px; }
.step-result-toggle:hover { opacity: 0.8; }
.step-result { margin: 4px 0 0 23px; padding: 8px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; max-height: 200px; overflow-y: auto; }
.step-result pre { margin: 0; font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; color: var(--text-secondary); font-family: monospace; }
.err { padding: 8px 12px; background: rgba(239,68,68,0.06); border: 1px solid rgba(239,68,68,0.2); border-radius: 8px; font-size: 13px; color: #ef4444; margin-top: 4px; }
</style>
