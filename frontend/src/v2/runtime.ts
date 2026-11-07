import type {
  AssistantMessage,
  FormInfo,
  InboxInfo,
  InboxItem,
  MessagePart,
  MessageV2,
  PermissionRequest,
  SessionInfoV2,
  SessionStatusState,
  StructuredError,
  ToolPart,
  V2Event,
} from '../contracts/v2'

export interface V2SessionRuntime {
  id: string
  info: SessionInfoV2 | null
  status: SessionStatusState
  messages: MessageV2[]
  inbox: InboxInfo[]
  permissions: PermissionRequest[]
  forms: FormInfo[]
  error: StructuredError | null
  hydrated: boolean
  seenEventIds: string[]
}

const MAX_SEEN_EVENTS = 500

export function emptyRuntime(id: string): V2SessionRuntime {
  return {
    id,
    info: null,
    status: { type: 'idle' },
    messages: [],
    inbox: [],
    permissions: [],
    forms: [],
    error: null,
    hydrated: false,
    seenEventIds: [],
  }
}

export function eventSessionId(event: V2Event): string | undefined {
  if (event.type === 'session.created') return event.data.session.id
  const data = event.data as unknown as Record<string, unknown>
  const value = data.session_id
  return typeof value === 'string' ? value : undefined
}

function clone(runtime: V2SessionRuntime): V2SessionRuntime {
  return {
    ...runtime,
    info: runtime.info ? { ...runtime.info, tokens: { ...runtime.info.tokens } } : null,
    status: { ...runtime.status },
    messages: runtime.messages.slice(),
    inbox: runtime.inbox.slice(),
    permissions: runtime.permissions.slice(),
    forms: runtime.forms.slice(),
    error: runtime.error,
    seenEventIds: runtime.seenEventIds.slice(),
  }
}

function messageIndex(runtime: V2SessionRuntime, id: string): number {
  return runtime.messages.findIndex((message) => message.id === id)
}

function ensureAssistant(runtime: V2SessionRuntime, id: string): AssistantMessage {
  const index = messageIndex(runtime, id)
  if (index >= 0) {
    const message = runtime.messages[index]!
    if (message.type === 'assistant') return message
  }
  const message: AssistantMessage = {
    type: 'assistant',
    id,
    time_created: Date.now(),
    content: [],
  }
  if (index >= 0) runtime.messages[index] = message
  else runtime.messages.push(message)
  return message
}

function setAssistant(runtime: V2SessionRuntime, message: AssistantMessage): void {
  const index = messageIndex(runtime, message.id)
  if (index >= 0) runtime.messages[index] = message
  else runtime.messages.push(message)
}

function updateTextPart(
  message: AssistantMessage,
  ordinal: number,
  type: 'text' | 'reasoning',
  text: string,
  mode: 'start' | 'delta' | 'end',
): AssistantMessage {
  const content = message.content.slice()
  let index = content.findIndex(
    (part) => (part.type === 'text' || part.type === 'reasoning') && (part as any).ordinal === ordinal,
  )
  if (index < 0) {
    content.push({ type, text: mode === 'delta' ? text : '' } as MessagePart)
    index = content.length - 1
  }
  const current = content[index] as any
  if (mode === 'start') current.text = current.text || ''
  else if (mode === 'delta') current.text = String(current.text || '') + text
  else current.text = text
  ;(current as any).ordinal = ordinal
  return { ...message, content }
}

function ensureToolPart(
  message: AssistantMessage,
  id: string,
  name: string,
): AssistantMessage {
  const content = message.content.slice()
  if (content.some((part) => part.type === 'tool' && part.id === id)) {
    return { ...message, content }
  }
  content.push({
    type: 'tool',
    id,
    name,
    state: { status: 'streaming', input: '' },
  })
  return { ...message, content }
}

function updateToolPart(
  message: AssistantMessage,
  id: string,
  name: string,
  patch: (part: ToolPart) => ToolPart,
): AssistantMessage {
  const base = ensureToolPart(message, id, name)
  const content = base.content.map((part) =>
    part.type === 'tool' && part.id === id ? patch(part) : part,
  )
  return { ...base, content }
}

function upsertInbox(runtime: V2SessionRuntime, item: InboxInfo): void {
  const index = runtime.inbox.findIndex((entry) => entry.id === item.id)
  if (index >= 0) runtime.inbox[index] = item
  else runtime.inbox.push(item)
}

function removeInbox(runtime: V2SessionRuntime, id: string): void {
  runtime.inbox = runtime.inbox.filter((item) => item.id !== id)
}

function upsertPermission(runtime: V2SessionRuntime, request: PermissionRequest): void {
  const index = runtime.permissions.findIndex((entry) => entry.id === request.id)
  if (index >= 0) runtime.permissions[index] = request
  else runtime.permissions.push(request)
}

function upsertForm(runtime: V2SessionRuntime, form: FormInfo): void {
  const index = runtime.forms.findIndex((entry) => entry.id === form.id)
  if (index >= 0) runtime.forms[index] = form
  else runtime.forms.push(form)
}

export function applyV2Event(
  runtime: V2SessionRuntime,
  event: V2Event,
): V2SessionRuntime {
  if (runtime.seenEventIds.includes(event.id)) return runtime
  const next = clone(runtime)
  next.seenEventIds.push(event.id)
  if (next.seenEventIds.length > MAX_SEEN_EVENTS) {
    next.seenEventIds.splice(0, next.seenEventIds.length - MAX_SEEN_EVENTS)
  }

  switch (event.type) {
    case 'session.created':
      next.info = event.data.session
      next.status = { type: event.data.session.status }
      break
    case 'session.renamed':
      if (next.info) next.info = { ...next.info, title: event.data.title }
      break
    case 'session.archived':
      if (next.info) next.info = { ...next.info, time: { ...next.info.time, archived: event.created } }
      break
    case 'session.unarchived':
      if (next.info) next.info = { ...next.info, time: { ...next.info.time, archived: null } }
      break
    case 'session.updated':
      if (next.info) {
        next.info = {
          ...next.info,
          title: event.data.title ?? next.info.title,
          status: event.data.status ?? next.info.status,
        }
      }
      break
    case 'session.deleted':
      next.info = null
      next.status = { type: 'idle' }
      break
    case 'session.status':
      next.status = event.data.status
      if (next.info) next.info = { ...next.info, status: event.data.status.type }
      break
    case 'session.idle':
      next.status = { type: 'idle' }
      if (next.info && event.data.outcome) next.info = { ...next.info, outcome: event.data.outcome }
      break
    case 'session.execution.started':
      next.status = { type: 'busy' }
      next.error = null
      if (next.info) next.info = { ...next.info, status: 'busy' }
      break
    case 'session.execution.succeeded':
    case 'session.execution.interrupted':
      next.status = { type: 'idle' }
      if (next.info) {
        next.info = {
          ...next.info,
          status: 'idle',
          outcome: event.type === 'session.execution.interrupted' ? 'interrupted' : 'succeeded',
        }
      }
      break
    case 'session.execution.failed':
      next.status = { type: 'idle' }
      next.error = event.data.error
      if (next.info) next.info = { ...next.info, status: 'idle', outcome: 'failed' }
      break
    case 'session.retry.scheduled':
      next.status = {
        type: 'retry',
        attempt: event.data.attempt,
        message: event.data.error.message,
        next: event.data.at,
      }
      break
    case 'session.inbox.enqueued': {
      const item: InboxItem = event.data.item
      upsertInbox(next, {
        id: event.data.inbox_id,
        session_id: event.data.session_id,
        item,
        status: 'pending',
        admitted_seq: 0,
        promoted_seq: null,
        time_created: event.created,
        time_delivered: null,
      })
      break
    }
    case 'session.inbox.delivered':
      removeInbox(next, event.data.inbox_id)
      break
    case 'session.inbox.cancelled':
      removeInbox(next, event.data.inbox_id)
      // 取消的输入不会落库；同时移除发送时乐观插入的用户消息。
      next.messages = next.messages.filter((message) => message.id !== event.data.inbox_id)
      break
    case 'session.inbox.delivery.changed': {
      const index = next.inbox.findIndex((item) => item.id === event.data.inbox_id)
      if (index >= 0) {
        const item = next.inbox[index]!
        next.inbox[index] = {
          ...item,
          item: { ...item.item, delivery: event.data.delivery } as InboxItem,
        }
      }
      break
    }
    case 'session.text.started':
      setAssistant(next, updateTextPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.ordinal,
        'text',
        '',
        'start',
      ))
      break
    case 'session.text.delta':
      setAssistant(next, updateTextPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.ordinal,
        'text',
        event.data.delta,
        'delta',
      ))
      break
    case 'session.text.ended':
      setAssistant(next, updateTextPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.ordinal,
        'text',
        event.data.text,
        'end',
      ))
      break
    case 'session.reasoning.started':
      setAssistant(next, updateTextPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.ordinal,
        'reasoning',
        '',
        'start',
      ))
      break
    case 'session.reasoning.delta':
      setAssistant(next, updateTextPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.ordinal,
        'reasoning',
        event.data.delta,
        'delta',
      ))
      break
    case 'session.reasoning.ended':
      setAssistant(next, updateTextPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.ordinal,
        'reasoning',
        event.data.text,
        'end',
      ))
      break
    case 'session.tool.input.started':
      setAssistant(next, ensureToolPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.id,
        event.data.name,
      ))
      break
    case 'session.tool.input.delta':
      setAssistant(next, updateToolPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.id,
        'tool',
        (part) => ({
          ...part,
          state: part.state.status === 'streaming'
            ? { status: 'streaming', input: String(part.state.input || '') + event.data.delta }
            : part.state,
        }),
      ))
      break
    case 'session.tool.input.ended':
      setAssistant(next, updateToolPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.id,
        'tool',
        (part) => ({ ...part, state: { status: 'streaming', input: event.data.text } }),
      ))
      break
    case 'session.tool.called':
      setAssistant(next, updateToolPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.id,
        'tool',
        (part) => ({
          ...part,
          state: { status: 'running', input: event.data.input, metadata: {} },
        }),
      ))
      break
    case 'session.tool.progress':
      setAssistant(next, updateToolPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.id,
        'tool',
        (part) => ({
          ...part,
          state: part.state.status === 'running'
            ? { ...part.state, metadata: event.data.metadata }
            : part.state,
        }),
      ))
      break
    case 'session.tool.success':
      setAssistant(next, updateToolPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.id,
        'tool',
        (part) => ({
          ...part,
          state: {
            status: 'completed',
            input: part.state.status === 'running' ? part.state.input : {},
            content: event.data.content,
            metadata: event.data.metadata,
          },
        }),
      ))
      break
    case 'session.tool.failed':
      setAssistant(next, updateToolPart(
        ensureAssistant(next, event.data.assistant_message_id),
        event.data.id,
        'tool',
        (part) => ({
          ...part,
          state: {
            status: 'error',
            input: part.state.status === 'running' ? part.state.input : {},
            error: event.data.error,
            content: event.data.content,
            metadata: event.data.metadata,
          },
        }),
      ))
      break
    case 'session.message.content.updated': {
      const index = messageIndex(next, event.data.message_id)
      if (index >= 0) {
        const message = next.messages[index]!
        if (message.type === 'assistant') {
          next.messages[index] = { ...message, content: event.data.content }
        }
      }
      break
    }
    case 'session.usage.updated':
      if (next.info) {
        next.info = {
          ...next.info,
          cost: event.data.cost,
          tokens: event.data.tokens,
        }
      }
      break
    case 'permission.asked':
      upsertPermission(next, event.data)
      break
    case 'permission.replied':
      next.permissions = next.permissions.filter((item) => item.id !== event.data.request_id)
      break
    case 'form.created':
      upsertForm(next, event.data)
      break
    case 'form.replied':
    case 'form.cancelled':
      next.forms = next.forms.filter((item) => item.id !== event.data.form_id)
      break
    default:
      break
  }
  return next
}
