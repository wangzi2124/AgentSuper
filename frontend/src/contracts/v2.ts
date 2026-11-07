export type Delivery = 'steer' | 'queue'
export type SessionState = 'idle' | 'busy' | 'retry'
export type SessionOutcome = 'succeeded' | 'failed' | 'interrupted'
export type PermissionReply = 'once' | 'always' | 'reject'
export type ToolStatus = 'streaming' | 'running' | 'completed' | 'error'

export interface ModelRef {
  id: string
  provider_id: string
  variant?: string | null
}

export interface TokenUsage {
  input: number
  output: number
  reasoning: number
  cache_read: number
  cache_write: number
}

export interface SessionTime {
  created: number
  updated: number
  busy?: number | null
  idle?: number | null
  viewed?: number | null
  archived?: number | null
}

export interface LocationRef {
  directory: string
  project_id?: string | null
}

export interface SessionInfoV2 {
  id: string
  parent_id?: string | null
  project_id: string
  directory: string
  title: string
  agent?: string | null
  model?: ModelRef | null
  status: SessionState
  outcome?: SessionOutcome | null
  cost: number
  tokens: TokenUsage
  time: SessionTime
  metadata: Record<string, unknown>
}

export interface StructuredError {
  type: string
  message: string
  status?: number | null
  retryable?: boolean | null
  provider?: string | null
  code?: string | null
}

export interface FileAttachment {
  type: 'file'
  uri: string
  mime?: string | null
  name?: string | null
  data?: string | null
}

export interface AgentAttachment {
  type: 'agent'
  id: string
}

export interface SkillAttachment {
  type: 'skill'
  id: string
}

export interface VoiceAttachment {
  id: string
  url: string
  duration: number
  waveform: number[]
  text: string
}

export interface PromptInput {
  id?: string | null
  text: string
  files?: FileAttachment[]
  agents?: AgentAttachment[]
  skills?: SkillAttachment[]
  metadata?: Record<string, unknown>
  voice?: VoiceAttachment | null
  delivery?: Delivery
  resume?: boolean
}

export interface TextToolContent {
  type: 'text'
  text: string
}

export interface FileToolContent {
  type: 'file'
  uri: string
  mime?: string | null
  name?: string | null
}

export type ToolContent = TextToolContent | FileToolContent

export interface ToolStateStreaming {
  status: 'streaming'
  input: string
}

export interface ToolStateRunning {
  status: 'running'
  input: Record<string, unknown>
  metadata?: Record<string, unknown>
}

export interface ToolStateCompleted {
  status: 'completed'
  input: Record<string, unknown>
  content?: ToolContent[]
  metadata?: Record<string, unknown>
}

export interface ToolStateError {
  status: 'error'
  input: Record<string, unknown>
  error: StructuredError
  content?: ToolContent[]
  metadata?: Record<string, unknown>
}

export type ToolState = ToolStateStreaming | ToolStateRunning | ToolStateCompleted | ToolStateError

export interface TextPart {
  type: 'text'
  text: string
  native?: Record<string, unknown> | null
}

export interface ReasoningPart {
  type: 'reasoning'
  text: string
  native?: Record<string, unknown> | null
}

export interface ToolPart {
  type: 'tool'
  id: string
  name: string
  state: ToolState
  time_created?: number | null
}

export type MessagePart = TextPart | ReasoningPart | ToolPart

export interface UserMessage {
  type: 'user'
  id: string
  time_created: number
  text: string
  files?: FileAttachment[]
  agents?: AgentAttachment[]
  skills?: SkillAttachment[]
  metadata?: Record<string, unknown>
  voice?: VoiceAttachment | null
}

export interface AssistantMessage {
  type: 'assistant'
  id: string
  time_created: number
  time_completed?: number | null
  agent?: string | null
  model?: ModelRef | null
  content: MessagePart[]
  finish?: string | null
  cost?: number | null
  tokens?: TokenUsage | null
  error?: StructuredError | null
}

export interface SyntheticMessage {
  type: 'synthetic'
  id: string
  time_created: number
  text: string
  description?: string | null
  metadata?: Record<string, unknown>
}

export interface SystemMessage {
  type: 'system'
  id: string
  time_created: number
  text: string
}

export interface SkillMessage {
  type: 'skill'
  id: string
  time_created: number
  skill: string
  name: string
  text: string
}

export interface ShellOutput {
  output: string
  cursor: number
  size: number
  truncated: boolean
}

export interface ShellMessage {
  type: 'shell'
  id: string
  time_created: number
  shell_id: string
  command: string
  status: 'running' | 'exited' | 'timeout' | 'killed'
  exit?: number | null
  output?: ShellOutput | null
}

export interface CompactionMessage {
  type: 'compaction'
  id: string
  time_created: number
  status: 'running' | 'completed' | 'failed'
  reason: 'auto' | 'manual'
  summary: string
  model?: ModelRef | null
  cost?: number | null
  tokens?: TokenUsage | null
  error?: StructuredError | null
}

export interface IdleMessage {
  type: 'idle'
  id: string
  time_created: number
  outcome: SessionOutcome
  error?: StructuredError | null
}

export interface AgentSelectedMessage {
  type: 'agent-switched'
  id: string
  time_created: number
  agent: string
}

export interface ModelSelectedMessage {
  type: 'model-switched'
  id: string
  time_created: number
  model: ModelRef
}

export interface LocationSwitchedMessage {
  type: 'location-switched'
  id: string
  time_created: number
  location: LocationRef
}

export type MessageV2 =
  | UserMessage
  | AssistantMessage
  | SyntheticMessage
  | SystemMessage
  | SkillMessage
  | ShellMessage
  | CompactionMessage
  | IdleMessage
  | AgentSelectedMessage
  | ModelSelectedMessage
  | LocationSwitchedMessage

export interface InboxUserPayload {
  text: string
  files?: FileAttachment[]
  agents?: AgentAttachment[]
  skills?: SkillAttachment[]
  metadata?: Record<string, unknown>
}

export interface InboxCompactionPayload {
  reason: 'auto' | 'manual'
}

export interface InboxMovePayload {
  location: LocationRef
  project_id?: string | null
}

export interface InboxUserItem {
  type: 'user'
  payload: InboxUserPayload
  delivery: Delivery
}

export interface InboxCompactionItem {
  type: 'compaction'
  payload: InboxCompactionPayload
  delivery: Delivery
}

export interface InboxMoveItem {
  type: 'move'
  payload: InboxMovePayload
  delivery: Delivery
}

export type InboxItem = InboxUserItem | InboxCompactionItem | InboxMoveItem

export interface InboxInfo {
  id: string
  session_id: string
  item: InboxItem
  status: 'pending' | 'delivered' | 'cancelled'
  admitted_seq: number
  promoted_seq?: number | null
  time_created: number
  time_delivered?: number | null
}

export interface PermissionSource {
  type: 'tool'
  message_id: string
  id: string
}

export interface PermissionRequest {
  id: string
  session_id: string
  action: string
  resources: string[]
  save: string[]
  metadata: Record<string, unknown>
  source?: PermissionSource | null
  message?: string | null
}

export interface PermissionReplied {
  session_id: string
  request_id: string
  reply: PermissionReply
}

export interface FormField {
  id: string
  type: string
  label: string
  required: boolean
  options: string[]
}

export interface FormInfo {
  id: string
  session_id: string
  title: string
  fields: FormField[]
  state: 'pending' | 'replied' | 'cancelled'
  response: Record<string, unknown>
}

export interface RevertState {
  message_id: string
  files: boolean
}

export interface SessionStatusState {
  type: SessionState
  attempt?: number | null
  message?: string | null
  next?: number | null
}

export interface DurableRef {
  aggregate_id: string
  seq: number
  version: number
}

export interface SessionCreatedData {
  session: SessionInfoV2
}
export interface SessionRenamedData {
  session_id: string
  title: string
}
export interface SessionRefData {
  session_id: string
}
export interface SessionMovedData {
  session_id: string
  location: LocationRef
  project_id?: string | null
}
export interface SessionForkedData {
  session_id: string
  parent_id: string
  boundary?: string | null
}
export interface SessionUpdatedData {
  session_id: string
  title?: string | null
  status?: SessionState | null
}
export interface SessionStatusData {
  session_id: string
  status: SessionStatusState
}
export interface SessionIdleData {
  session_id: string
  outcome?: SessionOutcome | null
}
export interface ExecutionFailedData {
  session_id: string
  error: StructuredError
}
export interface ExecutionInterruptedData {
  session_id: string
  reason: 'user' | 'shutdown' | 'superseded' | 'inactivity'
}
export interface RetryScheduledData {
  session_id: string
  assistant_message_id: string
  attempt: number
  at: number
  error: StructuredError
}
export interface InboxEnqueuedData {
  session_id: string
  inbox_id: string
  item: InboxItem
}
export interface InboxDeliveredData {
  session_id: string
  inbox_id: string
}
export interface InboxDeliveryChangedData {
  session_id: string
  inbox_id: string
  delivery: Delivery
}
export interface StepStartedData {
  session_id: string
  assistant_message_id: string
  agent?: string | null
  model?: ModelRef | null
  started: number
}
export interface StepStreamedData {
  session_id: string
  assistant_message_id: string
}
export interface StepEndedData {
  session_id: string
  assistant_message_id: string
  finish?: string | null
  cost?: number | null
  tokens?: TokenUsage | null
}
export interface StepFailedData extends StepEndedData {
  error: StructuredError
}
export interface TextStartedData {
  session_id: string
  assistant_message_id: string
  ordinal: number
}
export interface TextDeltaData extends TextStartedData {
  delta: string
}
export interface TextEndedData extends TextStartedData {
  text: string
  native?: Record<string, unknown> | null
}
export interface ToolInputStartedData {
  session_id: string
  assistant_message_id: string
  id: string
  name: string
}
export interface ToolInputDeltaData {
  session_id: string
  assistant_message_id: string
  id: string
  delta: string
}
export interface ToolInputEndedData {
  session_id: string
  assistant_message_id: string
  id: string
  text: string
}
export interface ToolCalledData {
  session_id: string
  assistant_message_id: string
  id: string
  name?: string
  input: Record<string, unknown>
  executed: boolean
}
export interface ToolProgressData {
  session_id: string
  assistant_message_id: string
  id: string
  name?: string
  metadata: Record<string, unknown>
}
export interface ToolSuccessData {
  session_id: string
  assistant_message_id: string
  id: string
  name?: string
  content: ToolContent[]
  metadata: Record<string, unknown>
  executed: boolean
}
export interface ToolFailedData {
  session_id: string
  assistant_message_id: string
  id: string
  name?: string
  error: StructuredError
  content: ToolContent[]
  metadata: Record<string, unknown>
  executed: boolean
}
export interface CompactionStartedData {
  session_id: string
  reason: 'auto' | 'manual'
  input_id?: string | null
}
export interface CompactionDeltaData {
  session_id: string
  text: string
}
export interface CompactionEndedData {
  session_id: string
  reason: 'auto' | 'manual'
  text: string
  model?: ModelRef | null
  cost?: number | null
  tokens?: TokenUsage | null
}
export interface CompactionFailedData {
  session_id: string
  reason: 'auto' | 'manual'
  error: StructuredError
  input_id?: string | null
  cost?: number | null
  tokens?: TokenUsage | null
}
export interface MessageContentUpdatedData {
  session_id: string
  message_id: string
  content: MessagePart[]
}
export interface UsageUpdatedData {
  session_id: string
  cost: number
  tokens: TokenUsage
}
export interface UsageRecordedData {
  session_id: string
  source: 'title' | 'compaction'
  cost: number
  tokens: TokenUsage
}
export interface SyntheticData {
  session_id: string
  text: string
  description?: string | null
  metadata: Record<string, unknown>
}
export interface SkillActivatedData {
  session_id: string
  id: string
  name: string
  text: string
}
export interface ShellStartedData {
  session_id: string
  shell: Record<string, unknown>
}
export interface ShellEndedData {
  session_id: string
  shell: Record<string, unknown>
  output: ShellOutput
}
export interface RevertStagedData {
  session_id: string
  revert: RevertState
}
export interface RevertCommittedData {
  session_id: string
  revert: RevertState
}
export interface ModelSelectedData {
  session_id: string
  model: ModelRef
}
export interface AgentSelectedData {
  session_id: string
  agent: string
}
export interface FormRepliedData {
  session_id: string
  form_id: string
  response: Record<string, unknown>
}
export interface FormRefData {
  session_id: string
  form_id: string
}
export interface ServerConnectedData {
  version?: string | null
}

export interface V2EventDataMap {
  'server.connected': ServerConnectedData
  'session.created': SessionCreatedData
  'session.renamed': SessionRenamedData
  'session.archived': SessionRefData
  'session.unarchived': SessionRefData
  'session.moved': SessionMovedData
  'session.deleted': SessionRefData
  'session.forked': SessionForkedData
  'session.viewed': SessionRefData
  'session.updated': SessionUpdatedData
  'session.status': SessionStatusData
  'session.idle': SessionIdleData
  'session.execution.started': SessionRefData
  'session.execution.succeeded': SessionRefData
  'session.execution.failed': ExecutionFailedData
  'session.execution.interrupted': ExecutionInterruptedData
  'session.retry.scheduled': RetryScheduledData
  'session.inbox.enqueued': InboxEnqueuedData
  'session.inbox.delivered': InboxDeliveredData
  'session.inbox.cancelled': InboxDeliveredData
  'session.inbox.delivery.changed': InboxDeliveryChangedData
  'session.step.started': StepStartedData
  'session.step.streamed': StepStreamedData
  'session.step.ended': StepEndedData
  'session.step.failed': StepFailedData
  'session.text.started': TextStartedData
  'session.text.delta': TextDeltaData
  'session.text.ended': TextEndedData
  'session.reasoning.started': TextStartedData
  'session.reasoning.delta': TextDeltaData
  'session.reasoning.ended': TextEndedData
  'session.tool.input.started': ToolInputStartedData
  'session.tool.input.delta': ToolInputDeltaData
  'session.tool.input.ended': ToolInputEndedData
  'session.tool.called': ToolCalledData
  'session.tool.progress': ToolProgressData
  'session.tool.success': ToolSuccessData
  'session.tool.failed': ToolFailedData
  'session.compaction.started': CompactionStartedData
  'session.compaction.delta': CompactionDeltaData
  'session.compaction.ended': CompactionEndedData
  'session.compaction.failed': CompactionFailedData
  'session.message.content.updated': MessageContentUpdatedData
  'session.usage.updated': UsageUpdatedData
  'session.usage.recorded': UsageRecordedData
  'session.synthetic': SyntheticData
  'session.skill.activated': SkillActivatedData
  'session.shell.started': ShellStartedData
  'session.shell.ended': ShellEndedData
  'session.revert.staged': RevertStagedData
  'session.revert.committed': RevertCommittedData
  'session.revert.cleared': SessionRefData
  'session.model.selected': ModelSelectedData
  'session.agent.selected': AgentSelectedData
  'permission.asked': PermissionRequest
  'permission.replied': PermissionReplied
  'form.created': FormInfo
  'form.replied': FormRepliedData
  'form.cancelled': FormRefData
}

export type V2EventType = keyof V2EventDataMap

export const V2_EVENT_TYPES = [
  'server.connected',
  'session.created',
  'session.renamed',
  'session.archived',
  'session.unarchived',
  'session.moved',
  'session.deleted',
  'session.forked',
  'session.viewed',
  'session.updated',
  'session.status',
  'session.idle',
  'session.execution.started',
  'session.execution.succeeded',
  'session.execution.failed',
  'session.execution.interrupted',
  'session.retry.scheduled',
  'session.inbox.enqueued',
  'session.inbox.delivered',
  'session.inbox.cancelled',
  'session.inbox.delivery.changed',
  'session.step.started',
  'session.step.streamed',
  'session.step.ended',
  'session.step.failed',
  'session.text.started',
  'session.text.delta',
  'session.text.ended',
  'session.reasoning.started',
  'session.reasoning.delta',
  'session.reasoning.ended',
  'session.tool.input.started',
  'session.tool.input.delta',
  'session.tool.input.ended',
  'session.tool.called',
  'session.tool.progress',
  'session.tool.success',
  'session.tool.failed',
  'session.compaction.started',
  'session.compaction.delta',
  'session.compaction.ended',
  'session.compaction.failed',
  'session.message.content.updated',
  'session.usage.updated',
  'session.usage.recorded',
  'session.synthetic',
  'session.skill.activated',
  'session.shell.started',
  'session.shell.ended',
  'session.revert.staged',
  'session.revert.committed',
  'session.revert.cleared',
  'session.model.selected',
  'session.agent.selected',
  'permission.asked',
  'permission.replied',
  'form.created',
  'form.replied',
  'form.cancelled',
] as const satisfies readonly V2EventType[]

type MissingEventType = Exclude<V2EventType, (typeof V2_EVENT_TYPES)[number]>
const _allEventTypesCovered: MissingEventType extends never ? true : never = true
void _allEventTypesCovered

export interface EventEnvelope<TType extends V2EventType = V2EventType> {
  id: string
  created: number
  type: TType
  location?: LocationRef | null
  durable?: DurableRef | null
  data: V2EventDataMap[TType]
}

export type V2Event = {
  [K in V2EventType]: EventEnvelope<K>
}[V2EventType]

export interface SseFrame {
  id?: string | null
  event: string
  data: V2Event
}

export const DURABLE_EVENT_TYPES: ReadonlySet<V2EventType> = new Set<V2EventType>([
  'session.created',
  'session.renamed',
  'session.archived',
  'session.unarchived',
  'session.moved',
  'session.deleted',
  'session.forked',
  'session.viewed',
  'session.execution.started',
  'session.execution.succeeded',
  'session.execution.failed',
  'session.execution.interrupted',
  'session.retry.scheduled',
  'session.inbox.enqueued',
  'session.inbox.delivered',
  'session.inbox.cancelled',
  'session.inbox.delivery.changed',
  'session.step.started',
  'session.step.streamed',
  'session.step.ended',
  'session.step.failed',
  'session.text.started',
  'session.text.ended',
  'session.reasoning.started',
  'session.reasoning.ended',
  'session.tool.input.started',
  'session.tool.input.ended',
  'session.tool.called',
  'session.tool.success',
  'session.tool.failed',
  'session.compaction.started',
  'session.compaction.ended',
  'session.compaction.failed',
  'session.message.content.updated',
  'session.usage.recorded',
  'session.synthetic',
  'session.skill.activated',
  'session.shell.started',
  'session.shell.ended',
  'session.revert.staged',
  'session.revert.committed',
  'session.revert.cleared',
  'session.model.selected',
  'session.agent.selected',
])

export const VOLATILE_EVENT_TYPES: ReadonlySet<V2EventType> = new Set<V2EventType>([
  'server.connected',
  'session.status',
  'session.idle',
  'session.updated',
  'session.text.delta',
  'session.reasoning.delta',
  'session.tool.input.delta',
  'session.tool.progress',
  'session.compaction.delta',
  'session.usage.updated',
  'permission.asked',
  'permission.replied',
  'form.created',
  'form.replied',
  'form.cancelled',
])
