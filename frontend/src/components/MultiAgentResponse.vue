<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MultiAgentMessage, AgentStep, AgentOutputPart } from '../types'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{
  message: MultiAgentMessage
  routingStatus: string
  isLast: boolean
}>()

const emit = defineEmits<{ undo: []; restore: [] }>()

// [C8] 计划文件路径：整块可点击（点一下即复制，便于粘到编辑器/资源管理器打开），
// 浏览器无法直接打开后端本地文件，故用「点击复制 + 显式提示」而非伪链接
// （href="file://" 在 http(s) 页面会被浏览器拦截）。navigator.clipboard 在非安全
// 上下文可能不可用 → 降级为 prompt 让用户手动复制。
const copied = ref(false)
async function copyPlanPath(): Promise<void> {
  const path = props.message.plan_path
  if (!path) return
  try {
    await navigator.clipboard.writeText(path)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    copied.value = false
    window.prompt('请复制计划文件路径：', path)
  }
}

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

// 文件改动状态 → 中文徽标文案
function statusLabel(status: string): string {
  if (status === 'added') return '新增'
  if (status === 'deleted') return '删除'
  return '修改'
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

    <!-- [C8] 计划文件（plan Agent 落盘产物）：点击路径即复制，便于在编辑器/资源管理器打开 -->
    <div v-if="message.plan_path" class="plan-file" role="button" tabindex="0"
         title="点击复制计划文件路径" @click="copyPlanPath" @keyup.enter="copyPlanPath">
      <span class="pf-icon">📋</span>
      <span class="pf-label">已生成计划文件</span>
      <code class="pf-path">{{ message.plan_path }}</code>
      <span class="pf-copy">{{ copied ? '已复制' : '点击复制路径' }}</span>
    </div>

    <!-- files changed（快照 diff：本次轮次改动了哪些文件 + 行数） -->
    <div v-if="message.files_changed?.length" class="files-changed">
      <div class="fc-title">
        <span>文件改动 · {{ message.files_changed.length }}</span>
        <button
          class="fc-restore"
          :disabled="message.snapshotRestored"
          :class="{ restored: message.snapshotRestored }"
          @click="emit('restore')"
        >{{ message.snapshotRestored ? '已撤回' : '撤回本轮改动' }}</button>
      </div>
      <div v-for="fc in message.files_changed" :key="fc.file" class="fc-row">
        <span class="fc-badge" :class="fc.status">{{ statusLabel(fc.status) }}</span>
        <span class="fc-file" :title="fc.file">{{ fc.file }}</span>
        <span v-if="fc.external" class="fc-lines binary">外部路径</span>
        <span v-else-if="fc.binary" class="fc-lines binary">二进制</span>
        <span v-else class="fc-lines">
          <span class="add">+{{ fc.additions ?? 0 }}</span>
          <span class="del">-{{ fc.deletions ?? 0 }}</span>
        </span>
      </div>
    </div>

    <!-- error -->
    <div v-if="message.isError && message.errorInfo" class="err">
      ⚠️ {{ message.errorInfo.message }}
    </div>
  </div>
</template>


<style scoped src="../styles/chat/multiAgentResponse.css"></style>

<style scoped>
.plan-file {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: 12px;
  background: var(--surface-elevated);
  font-size: 13px;
  cursor: pointer;
}
.plan-file:hover {
  border-color: var(--primary);
}
.pf-label {
  color: var(--text-secondary);
  font-weight: 600;
}
.pf-path {
  flex: 1 1 240px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-primary);
  user-select: all;
}
.pf-copy {
  padding: 3px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--primary);
  font-size: 12px;
}
.plan-file:hover .pf-copy {
  border-color: var(--primary);
}

.files-changed {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface-elevated);
  font-size: 13px;
}
.fc-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.fc-restore {
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: var(--radius-pill);
  padding: 2px 10px;
  cursor: pointer;
  flex-shrink: 0;
}
.fc-restore:hover:not(:disabled) { background: var(--primary-glow); }
.fc-restore:disabled { opacity: 0.6; cursor: default; }
.fc-restore.restored { color: var(--text-secondary); }
.fc-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.fc-badge {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: var(--radius-pill);
  font-weight: 600;
  flex-shrink: 0;
}
.fc-badge.added { background: var(--success-soft); color: var(--success); }
.fc-badge.modified { background: var(--primary-glow); color: var(--primary); }
.fc-badge.deleted { background: var(--danger-soft); color: var(--danger); }
.fc-file {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 12.5px;
}
.fc-lines { flex-shrink: 0; font-family: var(--font-mono); font-size: 12.5px; }
.fc-lines .add { color: var(--success); margin-right: 6px; }
.fc-lines .del { color: var(--danger); }
.fc-lines.binary { color: var(--text-secondary); }
</style>
