<script setup lang="ts">
import { computed, ref } from 'vue'
import type { AgentStep } from '../types'

// 定义组件事件：撤销
const emit = defineEmits<{ undo: [] }>()
// 定义组件属性：步骤列表、运行状态、撤销回调
const props = defineProps<{
  steps: AgentStep[]
  isRunning: boolean
  onUndo?: () => void
}>()

// 步骤执行顺序
const stepOrder = ['retrieve', 'rerank', 'generate']

// 按顺序排序步骤列表
const sortedSteps = computed(() => {
  return [...props.steps].sort((a, b) => {
    const ai = stepOrder.indexOf(a.step_id)
    const bi = stepOrder.indexOf(b.step_id)
    if (ai !== -1 && bi !== -1) return ai - bi
    if (ai !== -1) return -1
    if (bi !== -1) return 1
    return 0
  })
})

// 展开/收起结果的状态记录
const expandedResults = ref<Record<string, boolean>>({})
// 展开/收起参数的状态记录
const expandedArgs = ref<Record<string, boolean>>({})

// 切换步骤结果的展开状态
function toggleResult(stepId: string) {
  expandedResults.value[stepId] = !expandedResults.value[stepId]
}

// 切换步骤参数的展开状态
function toggleArgs(stepId: string) {
  expandedArgs.value[stepId] = !expandedArgs.value[stepId]
}

// 格式化参数为可读 JSON
function formatArgs(args: Record<string, unknown>): string {
  return JSON.stringify(args, null, 2)
}

// 获取参数摘要（简短预览）
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

// 根据状态返回对应图标
function getStatusIcon(status: string): string {
  if (status === 'completed') return '✅'
  if (status === 'failed') return '❌'
  return '⏳'
}

// 格式化耗时显示
function formatDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}


</script>

<template>
  <div class="step-task-list">
    <div class="task-header">
      <span class="task-title">执行任务</span>
      <span v-if="isRunning" class="task-status running">进行中</span>
      <span v-else class="task-status done">完成</span>
      <button v-if="onUndo" class="btn btn-ghost btn-sm" @click="$emit('undo')" title="撤销上一步">
        ↩ 撤销
      </button>
    </div>
    <div class="task-list">
      <div v-for="s in sortedSteps" :key="s.step_id" class="task-item" :class="s.status">
        <span class="task-icon">{{ getStatusIcon(s.status) }}</span>
        <div class="task-content">
          <span class="task-name">{{ s.name }}</span>
          <span v-if="s.detail" class="task-detail">{{ s.detail }}</span>
          <span v-if="s.tool_name" class="task-tool">
            🔧 {{ s.tool_name }}
            <span v-if="s.tool_args && Object.keys(s.tool_args).length" class="task-args-toggle" @click.stop="toggleArgs(s.step_id)">
              {{ expandedArgs[s.step_id] ? '收起参数' : argsSummary(s.tool_args) }}
            </span>
          </span>
          <span v-if="s.tool_result && s.status === 'completed'" class="task-view-result" @click.stop="toggleResult(s.step_id)">
            {{ expandedResults[s.step_id] ? '收起' : '查看结果' }}
          </span>
        </div>
        <span v-if="s.duration_ms != null" class="task-duration">{{ formatDuration(s.duration_ms) }}</span>
        <span v-else-if="isRunning && s.status === 'running'" class="task-duration spinning">...</span>
        <div v-if="s.tool_output" class="task-output" @click.stop>
          <pre>{{ s.tool_output }}</pre>
        </div>
        <div v-if="s.tool_args && expandedArgs[s.step_id]" class="task-args" @click.stop>
          <div class="task-args-label">参数</div>
          <pre>{{ formatArgs(s.tool_args) }}</pre>
        </div>
        <div v-if="s.tool_result && expandedResults[s.step_id]" class="task-result" @click.stop>
          <pre>{{ s.tool_result }}</pre>
        </div>
      </div>
      
      <div v-if="!props.steps.length && isRunning" class="task-item pending">
        <span class="task-icon">⏳</span>
        <div class="task-content">
          <span class="task-name">正在初始化...</span>
        </div>
        <span class="task-duration spinning">...</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.step-task-list {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 12px 16px;
  margin: 8px 0;
  animation: slideDown 0.2s ease-out;
}
@keyframes slideDown {
  from { opacity: 0; transform: translateY(-8px); }
  to { opacity: 1; transform: translateY(0); }
}
.task-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.task-title {
  font-weight: 600;
  font-size: 13px;
  color: var(--text);
}
.task-status {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}
.task-status.running {
  background: rgba(99, 102, 241, 0.15);
  color: var(--primary);
  animation: pulse 1.5s infinite;
}
.task-status.done {
  background: rgba(16, 185, 129, 0.15);
  color: var(--success);
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
.task-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.task-item {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--bg);
  transition: all 0.2s;
}
.task-item.completed {
  border-left: 3px solid var(--success);
}
.task-item.failed {
  border-left: 3px solid var(--danger);
}
.task-item.running {
  border-left: 3px solid var(--primary);
  background: rgba(99, 102, 241, 0.05);
}
.task-item.pending {
  opacity: 0.6;
}
.task-icon { font-size: 14px; }
.task-content { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.task-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.task-detail {
  font-size: 11px;
  color: var(--text-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.task-tool {
  font-size: 11px;
  color: var(--primary);
  font-family: monospace;
}
.task-args-toggle {
  color: var(--text-secondary);
  cursor: pointer;
  text-decoration: underline;
  margin-left: 4px;
  font-family: inherit;
}
.task-args-toggle:hover {
  color: var(--primary);
}
.task-args {
  margin: 4px 0 0 24px;
  padding: 8px 10px;
  background: #f8f9fa;
  border: 1px solid var(--border);
  border-radius: 6px;
  max-height: 300px;
  overflow-y: auto;
  width: calc(100% - 24px);
}
.task-args-label {
  font-size: 10px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  margin-bottom: 4px;
}
.task-args pre {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
  font-family: monospace;
}
.task-duration {
  font-size: 11px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.task-duration.spinning {
  color: var(--primary);
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.task-view-result {
  font-size: 11px;
  color: var(--primary);
  cursor: pointer;
  text-decoration: underline;
  margin-left: 8px;
  flex-shrink: 0;
}
.task-view-result:hover {
  opacity: 0.8;
}
.task-output {
  margin: 4px 0 0 24px;
  padding: 8px 10px;
  background: #0d1117;
  border: 1px solid var(--border);
  border-radius: 6px;
  max-height: 200px;
  overflow-y: auto;
  width: calc(100% - 24px);
}
.task-output pre {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  color: #c9d1d9;
  font-family: monospace;
}
.task-result {
  margin: 4px 0 0 24px;
  padding: 8px 10px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  max-height: 300px;
  overflow-y: auto;
  width: calc(100% - 24px);
}
.task-result pre {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text-secondary);
  font-family: monospace;
}
</style>