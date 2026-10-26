import type { AgentStep, AgentStreamData, ChatError } from '../types'
import type { HarnessEvent, TurnState } from './events'

export function createTurnState(turnId = 'local', sessionId?: string): TurnState {
  return {
    turnId,
    sessionId,
    phase: 'idle',
    lastSeq: 0,
    routingDetail: '',
    queuePosition: null,
    assistantContent: '',
    answer: '',
    agents: {},
    pendingPermissions: [],
    completed: false,
    turnDone: false,
  }
}

function cloneAgent(agent: AgentStreamData): AgentStreamData {
  return {
    ...agent,
    steps: [...(agent.steps || [])],
    parts: [...(agent.parts || [])],
  }
}

function ensureAgent(state: TurnState, event: HarnessEvent): AgentStreamData {
  const id = event.agent_id || 'build'
  const existing = state.agents[id]
  if (existing) return cloneAgent(existing)
  return {
    agent_id: id,
    agent_name: event.agent_name || id,
    agent_avatar: event.agent_avatar,
    status: 'running',
    content: '',
    steps: [],
    parts: [],
  }
}

export function appendAgentTextPart(agent: AgentStreamData, delta: string): void {
  if (!agent.parts) agent.parts = []
  const last = agent.parts[agent.parts.length - 1]
  if (last?.kind === 'text') {
    last.text = (last.text || '') + delta
  } else {
    agent.parts.push({ seq: agent.parts.length, kind: 'text', text: delta })
  }
}

export function appendAgentFinalText(agent: AgentStreamData, fullText: string): void {
  if (!agent.parts || agent.parts.length === 0) {
    agent.parts = [{ seq: 0, kind: 'text', text: fullText }]
    return
  }
  agent.parts = agent.parts.filter(
    p => !(p.kind === 'text' && ['', '{}', '[]'].includes((p.text || '').trim())),
  )
  if (agent.parts.length === 0) {
    agent.parts = [{ seq: 0, kind: 'text', text: fullText }]
    return
  }
  const last = agent.parts[agent.parts.length - 1]
  if (last.kind === 'text') last.text = fullText
  else agent.parts.push({ seq: agent.parts.length, kind: 'text', text: fullText })
}

export function appendAgentToolPart(agent: AgentStreamData, step: AgentStep): void {
  if (!agent.parts) agent.parts = []
  const i = agent.parts.findIndex(p => p.kind === 'tool' && p.step?.step_id === step.step_id)
  if (i >= 0 && agent.parts[i].step) agent.parts[i].step = step
  else agent.parts.push({ seq: agent.parts.length, kind: 'tool', step })
}

function errorFromEvent(event: HarnessEvent): ChatError {
  const retryable = Boolean(event.retryable)
  const statusCode = event.status_code
  const type = retryable
    ? statusCode === 429 ? 'rate_limit' : 'server_error'
    : 'unknown'
  return {
    type,
    message: event.error || event.detail || 'Unknown error',
    retryable,
    statusCode,
    errorType: event.error_type,
    errorCode: event.error_code,
  }
}

export function applyHarnessEvent(state: TurnState, event: HarnessEvent): TurnState {
  if (
    event.turn_id &&
    state.turnId !== 'local' &&
    event.turn_id !== state.turnId
  ) {
    return state
  }
  if (typeof event.seq === 'number' && event.seq <= state.lastSeq) {
    return state
  }

  const next: TurnState = {
    ...state,
    turnId: event.turn_id || state.turnId,
    sessionId: event.session_id || state.sessionId,
    lastSeq: typeof event.seq === 'number' ? event.seq : state.lastSeq,
  }

  switch (event.type) {
    case 'queued':
      next.phase = 'queued'
      next.queuePosition = event.queue_position ?? null
      break
    case 'routing':
      next.phase = 'routing'
      next.routingDetail = event.detail || 'Routing...'
      break
    case 'model_switched':
      next.model = event.model_ref?.name || event.model_ref?.id || next.model
      break
    case 'agent_start': {
      const agent = ensureAgent(next, event)
      next.agents = { ...next.agents, [agent.agent_id]: agent }
      next.phase = 'running'
      break
    }
    case 'agent_step': {
      if (!event.step?.step_id) break
      const agent = ensureAgent(next, event)
      const i = agent.steps.findIndex(s => s.step_id === event.step!.step_id)
      if (i >= 0) agent.steps[i] = event.step
      else agent.steps.push(event.step)
      appendAgentToolPart(agent, event.step)
      next.agents = { ...next.agents, [agent.agent_id]: agent }
      next.phase = 'running'
      break
    }
    case 'text_delta': {
      if (!event.delta) break
      next.assistantContent += event.delta
      if (event.agent_id && next.agents[event.agent_id]) {
        const agent = cloneAgent(next.agents[event.agent_id])
        agent.content += event.delta
        appendAgentTextPart(agent, event.delta)
        next.agents = { ...next.agents, [agent.agent_id]: agent }
      }
      next.phase = 'running'
      break
    }
    case 'agent_done': {
      const agent = ensureAgent(next, event)
      agent.status = 'completed'
      if (event.content) {
        agent.content = event.content
        appendAgentFinalText(agent, event.content)
      }
      next.agents = { ...next.agents, [agent.agent_id]: agent }
      break
    }
    case 'agent_error': {
      const agent = ensureAgent(next, event)
      agent.status = 'failed'
      agent.error = event.error
      next.agents = { ...next.agents, [agent.agent_id]: agent }
      break
    }
    case 'context_truncated': {
      const agent = ensureAgent(next, event)
      if (!agent.parts) agent.parts = []
      const notice = {
        model: event.model,
        window: event.window,
        budget: event.budget,
        reserve: event.reserve,
        estimated_before: event.estimated_before,
        estimated_after: event.estimated_after,
        dropped_messages: event.dropped_messages,
      }
      const duplicate = agent.parts.some(
        p => p.kind === 'notice' && p.notice?.window === notice.window,
      )
      if (!duplicate) {
        agent.parts.push({
          seq: agent.parts.length,
          kind: 'notice',
          text: `上下文已裁剪：${notice.estimated_before ?? '—'} → ${notice.estimated_after ?? '—'} tokens（窗口 ${notice.window ?? '—'}）`,
          notice,
        })
      }
      next.agents = { ...next.agents, [agent.agent_id]: agent }
      break
    }
    case 'permission_request': {
      if (event.request_id) {
        next.pendingPermissions = [
          ...next.pendingPermissions,
          {
            id: event.request_id,
            path: event.path || '',
            operation: event.operation || '',
            tool_name: event.tool_name || '',
            tool_args: event.tool_args as Record<string, unknown> | undefined,
            created_at: event.created_at || new Date().toISOString(),
            expires_at: event.expires_at,
          },
        ]
      }
      break
    }
    case 'queue_drain':
      if (event.queue_size != null) next.queuePosition = event.queue_size
      break
    case 'done':
    case 'turn_done':
      next.phase = 'done'
      next.completed = true
      next.turnDone = event.type === 'turn_done'
      next.answer = event.answer || next.assistantContent
      next.model = event.model || next.model
      if (event.files_changed) next.filesChanged = event.files_changed
      break
    case 'error':
      next.phase = 'error'
      next.error = errorFromEvent(event)
      break
    case 'heartbeat':
    default:
      break
  }
  return next
}
