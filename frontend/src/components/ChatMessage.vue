<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Message } from '../types'

// 定义组件事件：复制、撤销、删除、重试
const emit = defineEmits<{ copy: [text: string]; undo: [index: number]; delete: [id: string]; retry: [id: string] }>()
// 定义组件属性：消息对象和索引
const props = defineProps<{ message: Message; index: number }>()

// 复制成功的状态标记
const copied = ref(false)
// 步骤列表展开/折叠状态
const stepsExpanded = ref(false)
// 推理片段展开/折叠状态
const reasoningExpanded = ref(false)
// 单个步骤结果展开状态
const expandedResults = ref<Record<string, boolean>>({})
// 单个步骤参数展开状态
const expandedArgs = ref<Record<string, boolean>>({})

// 消息是否携带 parts（对齐 opencode Part 渲染路径）
const hasParts = computed(() => !!props.message.parts?.length)

// 系统消息（compaction/epoch/tool 等）：渲染为居中横幅而非气泡
const isSystem = computed(() => props.message.role === 'system' || props.message.role === 'tool')

// 正文：优先取 text parts（对齐设计 §3：正文在 Part），否则回退 message.content
const displayContent = computed(() => {
  if (hasParts.value) {
    const text = (props.message.parts || [])
      .filter(p => p.type === 'text')
      .map(p => p.data.text || '')
      .join('\n')
    if (text) return text
  }
  return props.message.content
})

// 推理片段（reasoning part，可折叠）
const reasoningParts = computed(() => (props.message.parts || []).filter(p => p.type === 'reasoning'))

// 从 parts 重建步骤列表（tool part 一张卡、step-start/step-finish 按 step_id 合并）
const partSteps = computed(() => {
  const byId = new Map<string, any>()
  const order: string[] = []
  for (const p of props.message.parts || []) {
    const d = p.data || {}
    if (p.type === 'tool') {
      const id = d.step_id || p.id
      const step: any = {
        type: d.state === 'running' ? 'tool_start' : 'tool_end',
        step_id: id,
        name: d.name || '调用工具',
        status: d.state === 'running' ? 'running' : d.state === 'error' ? 'failed' : 'completed',
        tool_name: d.name,
        tool_args: d.args,
        tool_result: d.output,
        part_id: p.id,
      }
      if (!byId.has(id)) order.push(id)
      byId.set(id, step)
    } else if (p.type === 'step-start' || p.type === 'step-finish') {
      const id = d.step_id || p.id
      const existing = byId.get(id)
      const step: any = {
        type: p.type === 'step-start' ? 'step_start' : 'step_end',
        step_id: id,
        name: d.name || existing?.name || '',
        status: d.state === 'running' ? 'running' : d.state === 'error' ? 'failed' : 'completed',
        detail: d.detail,
        duration_ms: d.duration_ms,
      }
      if (!existing) order.push(id)
      byId.set(id, step)
    }
  }
  return order.map(id => byId.get(id)).filter(Boolean)
})

// 计算思考步骤的耗时
const thoughtDuration = computed(() => {
  const steps = hasParts.value ? partSteps.value : props.message.steps
  if (!steps) return null
  const genStep = steps.find((s: any) => s.step_id === 'generate' || s.name === '生成回答')
  return genStep?.duration_ms ?? null
})

// 复制消息内容到剪贴板
async function handleCopy() {
  const text = displayContent.value
  if (!text) return
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
    } else {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
    }
    emit('copy', text)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    copied.value = false
  }
}

// 撤销到当前消息
function handleUndo() {
  emit('undo', props.index)
}

// 删除当前消息
function handleDelete() {
  emit('delete', props.message.id)
}

// 重试当前消息
function handleRetry() {
  emit('retry', props.message.id)
}

// 切换步骤列表展开状态
function toggleSteps() {
  stepsExpanded.value = !stepsExpanded.value
}

// 切换单个步骤结果展开状态
function toggleResult(stepId: string) {
  expandedResults.value[stepId] = !expandedResults.value[stepId]
}

// 切换单个步骤参数展开状态
function toggleArgs(stepId: string) {
  expandedArgs.value[stepId] = !expandedArgs.value[stepId]
}

// 获取状态图标
function getStatusIcon(status: string): string {
  if (status === 'completed') return '✅'
  if (status === 'failed') return '❌'
  return '⏳'
}

// 格式化耗时
function formatDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// 格式化参数为可读 JSON
function formatArgs(args: Record<string, unknown>): string {
  return JSON.stringify(args, null, 2)
}

// 获取参数摘要
function argsSummary(args: Record<string, unknown>): string {
  const keys = Object.keys(args)
  if (keys.length === 0) return ''
  const parts = keys.map(k => {
    const v = args[k]
    if (typeof v === 'string') return v.length > 25 ? v.slice(0, 25) + '...' : v
    return String(v)
  })
  return parts.join(', ').slice(0, 50)
}

// 只显示有意义的步骤（工具调用或有耗时的步骤）
const meaningfulSteps = computed(() => {
  if (hasParts.value) {
    return partSteps.value.filter((s: any) => s.tool_name || s.duration_ms || s.detail)
  }
  if (!props.message.steps) return []
  return props.message.steps.filter(s =>
    s.tool_name || s.duration_ms || s.detail
  )
})
</script>

<template>
  <div v-if="isSystem" class="chat-message system-msg">
    <div class="system-banner">
      <span class="system-badge">{{ message.role === 'tool' ? '🔧' : '📌' }}</span>
      <span class="system-text">{{ displayContent }}</span>
    </div>
  </div>
  <div v-else class="chat-message" :class="[message.role, { 'is-error': message.isError }]">
    <div class="avatar">
      {{ message.role === 'user' ? '👤' : (message.isError ? '⚠️' : '🤖') }}
    </div>
    <div class="bubble">
      <div v-if="message.isError" class="error-banner">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
      </div>
      <div v-if="message.role === 'assistant' && meaningfulSteps.length > 0" class="steps-section">
        <div class="steps-header" @click="toggleSteps">
          <span class="steps-toggle">{{ stepsExpanded ? '▾' : '▸' }}</span>
          <span class="steps-label">执行步骤 ({{ meaningfulSteps.length }})</span>
          <span v-if="thoughtDuration != null" class="steps-duration">{{ formatDuration(thoughtDuration) }}</span>
        </div>
        <div v-if="stepsExpanded" class="steps-list">
          <div v-for="s in meaningfulSteps" :key="s.step_id" class="step-item" :class="s.status">
            <div class="step-row">
              <span class="step-icon">{{ getStatusIcon(s.status) }}</span>
              <span class="step-name">{{ s.name }}</span>
              <span v-if="s.detail" class="step-detail">{{ s.detail }}</span>
              <span v-if="s.duration_ms != null" class="step-time">{{ formatDuration(s.duration_ms) }}</span>
            </div>
            <div v-if="s.tool_name" class="step-tool">
              🔧 {{ s.tool_name }}
              <span v-if="s.tool_args && Object.keys(s.tool_args).length" class="step-args-toggle" @click.stop="toggleArgs(s.step_id)">
                {{ expandedArgs[s.step_id] ? '收起参数' : argsSummary(s.tool_args) }}
              </span>
            </div>
            <div v-if="s.tool_args && expandedArgs[s.step_id]" class="step-args" @click.stop>
              <pre>{{ formatArgs(s.tool_args) }}</pre>
            </div>
            <div v-if="s.tool_result" class="step-result-toggle" @click.stop="toggleResult(s.step_id)">
              {{ expandedResults[s.step_id] ? '收起结果' : '查看结果' }}
            </div>
            <div v-if="s.tool_result && expandedResults[s.step_id]" class="step-result" @click.stop>
              <pre>{{ s.tool_result }}</pre>
            </div>
          </div>
        </div>
      </div>
      <div v-if="reasoningParts.length > 0" class="reasoning-section">
        <div class="reasoning-header" @click="reasoningExpanded = !reasoningExpanded">
          <span class="reasoning-toggle">{{ reasoningExpanded ? '▾' : '▸' }}</span>
          <span class="reasoning-label">🤔 思考过程</span>
        </div>
        <div v-if="reasoningExpanded" class="reasoning-content">
          <pre v-for="(r, i) in reasoningParts" :key="i">{{ r.data.text }}</pre>
        </div>
      </div>
      <div v-if="displayContent" class="content">{{ displayContent }}</div>
      <div v-if="message.files && message.files.length > 0" class="attachments">
        <div v-for="(f, i) in message.files" :key="i" class="attachment-badge">
          {{ f.filename }}
        </div>
      </div>
      <div v-if="message.sources && message.sources.length > 0" class="sources">
        <div class="sources-title">来源：</div>
        <div v-for="(s, i) in message.sources" :key="i" class="source-item">
          <span class="source-score">{{ (s.score * 100).toFixed(0) }}%</span>
          <span class="source-text">{{ s.content.slice(0, 120) }}...</span>
        </div>
      </div>
      <div class="message-footer">
        <span class="time">{{ message.timestamp.toLocaleTimeString() }}</span>
        <div class="message-actions">
          <button v-if="message.isError && message.errorInfo?.retryable" class="retry-btn" @click="handleRetry" title="重试">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="23 4 23 10 17 10"></polyline>
              <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
            </svg>
            <span>重试</span>
          </button>
          <div class="btn-wrapper">
            <button class="icon-btn" @click="handleCopy" title="复制">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
            </button>
            <span v-if="copied" class="copy-toast">复制成功</span>
          </div>
          <button v-if="message.role === 'user'" class="icon-btn" @click="handleUndo" title="撤销到此处">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 7v6h6"></path>
              <path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"></path>
            </svg>
          </button>
          <button class="icon-btn delete-btn" @click="handleDelete" title="删除消息">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="3 6 5 6 21 6"></polyline>
              <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-message {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.system-msg {
  display: flex;
  justify-content: center;
  padding: 2px 0;
}
.system-banner {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 90%;
  padding: 4px 12px;
  border-radius: 12px;
  background: rgba(99, 102, 241, 0.06);
  border: 1px dashed var(--border);
  font-size: 12px;
  color: var(--text-secondary);
}
.system-badge { flex-shrink: 0; }
.system-text {
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
.chat-message.user { flex-direction: row-reverse; }
.avatar {
  width: 36px; height: 36px;
  border-radius: 50%;
  background: var(--bg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  line-height: 1.6;
  font-size: 14px;
}
.user .bubble {
  background: var(--primary);
  color: white;
  border-color: var(--primary);
}
.content { white-space: pre-wrap; word-break: break-word; }
.reasoning-section {
  margin-bottom: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.reasoning-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  background: var(--bg);
  font-size: 12px;
  color: var(--text-secondary);
  user-select: none;
}
.reasoning-toggle { font-size: 10px; width: 12px; }
.reasoning-content {
  padding: 8px 10px;
  background: var(--bg);
  border-top: 1px solid var(--border);
}
.reasoning-content pre {
  margin: 0 0 6px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
  color: var(--text-secondary);
  font-family: inherit;
}
.reasoning-content pre:last-child { margin-bottom: 0; }
.steps-section {
  margin-bottom: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}
.steps-header {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  cursor: pointer;
  background: var(--bg);
  font-size: 12px;
  user-select: none;
  transition: background 0.15s;
}
.steps-header:hover {
  background: rgba(99, 102, 241, 0.05);
}
.steps-toggle {
  color: var(--text-secondary);
  font-size: 10px;
  width: 12px;
}
.steps-label {
  font-weight: 500;
  color: var(--text-secondary);
}
.steps-duration {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.steps-list {
  padding: 4px 8px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.step-item {
  padding: 4px 6px;
  border-radius: 4px;
  font-size: 12px;
}
.step-item.completed { background: rgba(16, 185, 129, 0.04); }
.step-item.failed { background: rgba(239, 68, 68, 0.04); }
.step-item.running { background: rgba(99, 102, 241, 0.04); }
.step-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.step-icon { font-size: 11px; flex-shrink: 0; }
.step-name {
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.step-detail {
  color: var(--text-secondary);
  font-size: 11px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.step-time {
  margin-left: auto;
  color: var(--text-secondary);
  font-size: 11px;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.step-tool {
  font-size: 11px;
  color: var(--primary);
  font-family: monospace;
  margin-top: 2px;
  padding-left: 17px;
}
.step-args-toggle {
  color: var(--text-secondary);
  cursor: pointer;
  text-decoration: underline;
  margin-left: 4px;
  font-family: inherit;
}
.step-args-toggle:hover { color: var(--primary); }
.step-args {
  margin: 4px 0 0 17px;
  padding: 6px 8px;
  background: #f8f9fa;
  border: 1px solid var(--border);
  border-radius: 4px;
  max-height: 200px;
  overflow-y: auto;
}
.step-args pre {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
  font-family: monospace;
}
.step-result-toggle {
  font-size: 11px;
  color: var(--primary);
  cursor: pointer;
  text-decoration: underline;
  margin-top: 2px;
  padding-left: 17px;
}
.step-result-toggle:hover { opacity: 0.8; }
.step-result {
  margin: 4px 0 0 17px;
  padding: 6px 8px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  max-height: 200px;
  overflow-y: auto;
}
.step-result pre {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-secondary);
  font-family: monospace;
}
.attachments {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 6px 0 0;
}
.attachment-badge {
  font-size: 11px;
  padding: 2px 8px;
  background: rgba(255,255,255,0.15);
  border-radius: 10px;
  white-space: nowrap;
}
.user .attachment-badge { color: rgba(255,255,255,0.85); }
.sources {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.user .sources { border-top-color: rgba(255,255,255,0.2); }
.sources-title {
  font-size: 12px; font-weight: 600; margin-bottom: 6px; color: var(--text-secondary);
}
.user .sources-title { color: rgba(255,255,255,0.8); }
.source-item {
  display: flex; gap: 8px; align-items: flex-start;
  padding: 4px 0; font-size: 12px; color: var(--text-secondary);
}
.user .source-item { color: rgba(255,255,255,0.8); }
.source-score { font-weight: 600; color: var(--success); flex-shrink: 0; }
.source-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.message-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--border);
  font-size: 11px;
}
.user .message-footer { border-top-color: rgba(255,255,255,0.2); }
.time {
  color: var(--text-secondary);
  opacity: 0.7;
}
.user .time { color: rgba(255,255,255,0.6); }
.message-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}
.btn-wrapper {
  position: relative;
  display: inline-flex;
}
.copy-toast {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 4px);
  transform: translateX(-50%);
  white-space: nowrap;
  font-size: 11px;
  background: var(--text);
  color: var(--bg);
  padding: 2px 8px;
  border-radius: 4px;
  pointer-events: none;
  animation: fadeInOut 1.5s ease-in-out;
}
@keyframes fadeInOut {
  0% { opacity: 0; transform: translateX(-50%) translateY(4px); }
  15% { opacity: 1; transform: translateX(-50%) translateY(0); }
  85% { opacity: 1; transform: translateX(-50%) translateY(0); }
  100% { opacity: 0; transform: translateX(-50%) translateY(-4px); }
}
.icon-btn {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: inherit;
  opacity: 0.5;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.icon-btn:hover {
  opacity: 1;
  background: rgba(255,255,255,0.1);
}
.user .icon-btn:hover { background: rgba(255,255,255,0.2); }
.delete-btn:hover {
  color: #ef4444 !important;
}
/* Error message styling */
.is-error .bubble {
  background: rgba(239, 68, 68, 0.06);
  border-color: rgba(239, 68, 68, 0.3);
}
.error-banner {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #ef4444;
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 500;
}
.retry-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  border-radius: 6px;
  border: 1px solid rgba(239, 68, 68, 0.3);
  background: rgba(239, 68, 68, 0.06);
  color: #ef4444;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.retry-btn:hover {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.5);
}
.retry-btn svg {
  flex-shrink: 0;
}
</style>
