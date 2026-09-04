<script setup lang="ts">
import { computed } from 'vue'
import type { MultiAgentMessage, AgentStep, AgentOutputPart } from '../types'
import MarkdownContent from './MarkdownContent.vue'

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

// opencode 风格状态符号
function getStatusIcon(status: string): string {
  if (status === 'completed') return '✓'
  if (status === 'failed') return '✕'
  return '•'
}

function formatDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// 工具/步骤卡片标题（对齐 opencode BasicTool trigger 的子标题文案）
function stepTitle(step: AgentStep): string {
  if (step.detail) return step.detail
  if (step.tool_name) return step.tool_name
  return step.name
}

// 极简工具名展示（去掉 tool_ 前缀，对齐 opencode 工具名可读性）
function shortToolName(name?: string): string {
  if (!name) return '工具'
  return name.replace(/^plugin_[^_]+_/, '').replace(/^tool_/, '')
}

// 按输出顺序排列的展示部件：优先 parts（真实交错），否则组装 steps（含最终正文）
function orderedParts(agent: {
  parts?: AgentOutputPart[]
  steps: AgentStep[]
  content: string
}): AgentOutputPart[] {
  if (agent.parts && agent.parts.length > 0) return agent.parts
  // 回退：历史回放（服务端只存 steps+content）——
  // 组装 [工具卡片…, 最终正文]，最大程度贴近 opencode 的"工具在前、答案收尾"。
  const parts: AgentOutputPart[] = (agent.steps || []).map((s, i) => ({
    seq: i,
    kind: 'tool' as const,
    step: s,
  }))
  if (agent.content) {
    parts.push({ seq: parts.length, kind: 'text' as const, text: agent.content })
  }
  return parts
}
</script>

<template>
  <div class="response" :class="{ loading: isLast && !!routingStatus, error: message.isError }">
    <!-- waiting for response -->
    <div v-if="isLast && !routingStatus && !message.agents.length && !message.content && !message.isError" class="routing">
      <span class="spinner"></span>
      正在思考...
    </div>

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

        <!-- 输出部件：按 agent 真实输出顺序交错（正文 ↔ 工具卡片，对齐 opencode Part 渲染） -->
        <div v-if="orderedParts(a).length" class="o-parts">
          <template v-for="p in orderedParts(a)" :key="p.seq">
            <!-- 正文块 -->
            <div v-if="p.kind === 'text'" class="text o-text">
              <MarkdownContent :text="p.text || ''" />
            </div>
            <!-- 极简工具卡片（无参数 / 无结果展开） -->
            <div v-else-if="p.kind === 'tool' && p.step" class="o-tool" :class="p.step.status">
              <span class="o-tool-icon" :class="p.step.status">{{ getStatusIcon(p.step.status) }}</span>
              <span class="o-tool-name">{{ shortToolName(p.step.tool_name) }}</span>
              <span class="o-tool-title">{{ stepTitle(p.step) }}</span>
              <span v-if="p.step.duration_ms != null" class="o-tool-time">{{ formatDuration(p.step.duration_ms) }}</span>
              <span v-else-if="p.step.status === 'running'" class="o-tool-spin"></span>
            </div>
          </template>
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

        <div v-if="a.error" class="err">⚠️ {{ a.error }}</div>
      </div>
    </div>

    <!-- final answer -->
    <div v-if="showFinalAnswer" class="text"><MarkdownContent :text="message.content" /></div>

    <!-- error -->
    <div v-if="message.isError && message.errorInfo" class="err">
      ⚠️ {{ message.errorInfo.message }}
    </div>
  </div>
</template>


<style scoped src="../styles/chat/multiAgentResponse.css"></style>
