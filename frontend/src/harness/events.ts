import type {
  AgentStep,
  AgentStreamData,
  ChatError,
  FileChange,
  MultiAgentSSEEvent,
} from '../types'

export type TurnPhase = 'idle' | 'queued' | 'routing' | 'running' | 'done' | 'error'

export interface PendingPermission {
  id: string
  path: string
  operation: string
  tool_name: string
  tool_args?: Record<string, unknown>
  created_at: string
  expires_at?: string
}

export interface TurnState {
  turnId: string
  sessionId?: string
  phase: TurnPhase
  lastSeq: number
  routingDetail: string
  queuePosition: number | null
  model?: string
  assistantContent: string
  answer: string
  agents: Record<string, AgentStreamData>
  pendingPermissions: PendingPermission[]
  error?: ChatError
  completed: boolean
  turnDone: boolean
  filesChanged?: FileChange[]
}

export type HarnessEventType =
  | 'routing'
  | 'agent_start'
  | 'agent_step'
  | 'agent_done'
  | 'agent_error'
  | 'context_truncated'
  | 'text_delta'
  | 'permission_request'
  | 'queued'
  | 'queue_drain'
  | 'model_switched'
  | 'done'
  | 'turn_done'
  | 'error'
  | 'heartbeat'

export type HarnessEvent = MultiAgentSSEEvent & {
  type: HarnessEventType
  turn_id?: string
  session_id?: string
  seq?: number
  error_code?: string
}

export interface AgentPartEvent {
  type: 'agent_start' | 'agent_step' | 'agent_done' | 'agent_error'
  agent_id: string
  agent_name?: string
  step?: AgentStep
  content?: string
  error?: string
}

export interface TurnTerminal {
  type: 'done' | 'turn_done'
  answer?: string
  conversation_id?: string
  model?: string
  files_changed?: FileChange[]
  agents?: AgentStreamData[]
  turn_id?: string
  seq?: number
  queued_turn?: boolean
}
