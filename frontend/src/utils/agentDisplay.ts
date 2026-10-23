import { AGENT_LABELS, NODE_LABELS } from '../constants/agentLabels'
import type { AgentStep, AgentOutputPart } from '../types'

/** opencode 风格状态符号 */
export function getStatusIcon(status: string): string {
  if (status === 'completed') return '✓'
  if (status === 'failed') return '✕'
  return '•'
}

export function formatDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** 极简工具名展示（去掉 plugin_/tool_ 前缀，对齐 opencode 工具名可读性） */
export function shortToolName(name?: string): string {
  if (!name) return '工具'
  return name.replace(/^plugin_[^_]+_/, '').replace(/^tool_/, '')
}

/** 工具/步骤卡片标题（对齐 opencode BasicTool trigger 的子标题文案） */
export function stepTitle(step: AgentStep): string {
  // 图节点进度（无 tool_name）不展示 detail —— 那是原始 JSON，对用户无意义。
  if (!step.tool_name) return ''
  return step.detail || step.tool_name
}

/** 卡片标签：真实工具调用显示工具名；图节点进度显示中文阶段名。 */
export function cardLabel(step: AgentStep): string {
  if (step.tool_name) return shortToolName(step.tool_name)
  return NODE_LABELS[step.name] || '处理中'
}

/** 累加各 Agent 步骤耗时（ms）。 */
export function sumDurationMs(agents: { steps?: AgentStep[] }[]): number {
  let ms = 0
  for (const a of agents) for (const s of a.steps || []) ms += s.duration_ms || 0
  return ms
}

export function agentLabel(agent: { agent_id: string; agent_name?: string }): string {
  return AGENT_LABELS[agent.agent_id] || agent.agent_name || agent.agent_id
}

/**
 * 统一实时流与历史回放路径：优先 parts（真实交错），否则用 steps + content 组装。
 * 数据进组件前先 normalize，组件内不再判断。
 */
export function normalizeAgent(agent: {
  parts?: AgentOutputPart[]
  steps: AgentStep[]
  content: string
}): { parts: AgentOutputPart[] } {
  if (agent.parts?.length) return { parts: agent.parts }
  const parts: AgentOutputPart[] = (agent.steps || []).map((s, i) => ({
    seq: i,
    kind: 'tool',
    step: s,
  }))
  if (agent.content) {
    parts.push({ seq: parts.length, kind: 'text', text: agent.content })
  }
  return { parts }
}
