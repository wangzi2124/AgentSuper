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
      <span v-if="isRunning" class="agent-status">● 运行中</span>
      <span v-else-if="isCompleted" class="agent-status done">✓ 完成</span>
      <span v-else-if="isFailed" class="agent-status err">✗ 失败</span>
    </div>

    <div v-if="agent.steps.length" class="agent-steps">
      <div class="steps-header" @click="stepsExpanded = !stepsExpanded">
        <span class="steps-toggle">{{ stepsExpanded ? '▾' : '▸' }}</span>
        <span class="steps-label">步骤（{{ agent.steps.length }}）</span>
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
              {{ expandedArgs[s.step_id] ? '隐藏参数' : argsSummary(s.tool_args) }}
            </span>
          </div>
          <div v-if="s.tool_args && expandedArgs[s.step_id]" class="step-args" @click.stop>
            <pre>{{ formatArgs(s.tool_args) }}</pre>
          </div>
          <div v-if="s.tool_result" class="step-result-toggle" @click.stop="toggleResult(s.step_id)">
            {{ expandedResults[s.step_id] ? '隐藏结果' : '查看结果' }}
          </div>
          <div v-if="s.tool_result && expandedResults[s.step_id]" class="step-result" @click.stop>
            <pre>{{ s.tool_result }}</pre>
          </div>
        </div>
      </div>
    </div>

    <div v-if="isRunning && !agent.content" class="agent-thinking">
      <div class="dot-pulse"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>
      <span>思考中...</span>
    </div>

    <div v-if="agent.content" class="agent-content" v-text="agent.content"></div>
    <div v-if="isFailed && agent.error" class="agent-error">⚠️ {{ agent.error }}</div>
  </div>
</template>


<style scoped src="../styles/chat/agentPanel.css"></style>
