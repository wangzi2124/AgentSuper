// 检索来源信息
export interface Source {
  document_id: string
  content: string
  score: number
}

// Agent 执行步骤信息
export interface AgentStep {
  type: 'step_start' | 'step_end' | 'tool_start' | 'tool_end'
  step_id: string
  name: string
  status: 'running' | 'completed' | 'failed'
  detail?: string
  duration_ms?: number
  tool_name?: string
  tool_args?: Record<string, unknown>
  tool_result?: string
  tool_output?: string
  part_id?: string
}

// 消息部件（对齐 opencode Part：正文/推理/工具/步骤 以 Part 承载）
export interface Part {
  id: string
  type: 'text' | 'reasoning' | 'tool' | 'step-start' | 'step-finish' | 'file' | 'patch' | 'agent' | 'compaction'
  data: Record<string, any>
}

// 聊天响应
export interface ChatResponse {
  answer: string
  sources: Source[]
  conversation_id: string
  steps?: AgentStep[]
}

// SSE 流式事件
export interface SSEEvent {
  type: 'step_start' | 'step_end' | 'tool_start' | 'tool_end' | 'done' | 'error' | 'permission_request' | 'tool_output' | 'tool_heartbeat' | 'queued' | 'text_delta'
  step_id?: string
  name?: string
  status?: string
  detail?: string
  duration_ms?: number
  tool_name?: string
  tool_args?: Record<string, unknown>
  tool_result?: string
  delta?: string
  answer?: string
  sources?: Source[]
  conversation_id?: string
  title?: string
  steps?: AgentStep[]
  parts?: Part[]
  part_id?: string
  error?: string
  retryable?: boolean
  status_code?: number
  error_type?: string
  request_id?: string
  path?: string
  operation?: string
  source?: string
  line?: string
  elapsed_seconds?: number
  user_msg_id?: string
  assistant_msg_id?: string
  queue_position?: number
  task?: {
    task_id: string
    status: string
    step: number
    total_tokens: number
    tool_calls_count: number
  }
}

// 文件内容（用于多模态消息）
export interface FileContent {
  filename: string
  data: string
  mime_type: string
}

// 权限审批请求
export interface PermissionRequest {
  id: string
  path: string
  operation: string
  tool_name: string
  tool_args: Record<string, unknown>
  created_at: string
}

// 聊天请求参数
export interface ChatRequest {
  message: string
  conversation_id?: string
  model?: string
  use_vector_db?: boolean
  files?: FileContent[]
  /** 会话绑定的工作目录（opencode ctx.directory），首条消息创建会话时生效 */
  directory?: string
}

// 文档信息
export interface Document {
  id: string
  filename: string
  size: number
  chunk_count: number
  created_at: string
}

// 文档列表响应
export interface DocumentListResponse {
  documents: Document[]
  total: number
}

// 单个文档响应
export interface DocumentResponse {
  id: string
  filename: string
  size: number
  chunk_count: number
  created_at: string
}

// 上传任务 ID 响应
export interface UploadResponse {
  task_id: string
}

// 文档处理任务进度
export interface TaskProgress {
  task_id: string
  filename: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  progress: number
  stage: string
  result?: DocumentResponse | null
  error?: string | null
}

// 删除操作响应
export interface DeleteResponse {
  message: string
}

// 向量分块
export interface Chunk {
  id: string
  text: string
  metadata: Record<string, unknown>
}

// 向量分块列表响应
export interface ChunkListResponse {
  chunks: Chunk[]
  total: number
  offset: number
  limit: number
}

// 技能信息
export interface Skill {
  name: string
  description: string
  path: string
  enabled: boolean
}

// 插件信息
export interface Plugin {
  name: string
  version: string
  description: string
  enabled: boolean
}

// 生成的文件信息
export interface GeneratedFile {
  filename: string
  size: number
  created_at: string
}

// 生成文件列表响应
export interface GeneratedFileList {
  files: GeneratedFile[]
  total: number
}

// 监控统计数据
export interface MonitorStats {
  requests: {
    total: number
    by_path: Record<string, number>
    by_status: Record<string, number>
  }
  model_calls: {
    total: number
    by_model: Record<string, number>
    total_prompt_tokens: number
    total_completion_tokens: number
    total_duration_ms: number
    avg_duration_ms: number
    tool_rounds_total: number
    avg_tool_rounds: number
  }
}

// 聊天错误分类
export interface ChatError {
  type: 'rate_limit' | 'server_error' | 'network' | 'timeout' | 'unknown'
  message: string
  retryable: boolean
  statusCode?: number
}

// 聊天消息
export interface Message {
  id: string
  role: 'user' | 'assistant' | 'system' | 'tool'
  content: string
  sources?: Source[]
  steps?: AgentStep[]
  parts?: Part[]
  files?: { filename: string; mime_type: string }[]
  timestamp: Date
  isError?: boolean
  errorInfo?: ChatError
  live?: boolean
}

// ===== Multi-Agent Types =====

export interface AgentStreamData {
  agent_id: string
  agent_name: string
  agent_avatar?: string
  status: 'running' | 'completed' | 'failed'
  content: string
  steps: AgentStep[]
  error?: string
}

export interface MultiAgentSSEEvent {
  type: 'routing' | 'agent_start' | 'agent_step' | 'agent_stream' | 'agent_done' | 'agent_error' | 'permission_request' | 'done' | 'error' | 'queued'
  agent_id: string
  agent_name?: string
  agent_avatar?: string
  step?: AgentStep
  content?: string
  answer?: string
  detail?: string
  error?: string
  conversation_id?: string
  title?: string
  retryable?: boolean
  status_code?: number
  error_type?: string
  queue_position?: number
  agents?: AgentStreamData[]
  user_msg_id?: string
  assistant_msg_id?: string
  request_id?: string
  path?: string
  operation?: string
  tool_name?: string
  tool_args?: Record<string, unknown>
}

export interface MultiAgentChatRequest {
  message: string
  conversation_id?: string
  model?: string
  use_vector_db?: boolean
  /** 会话绑定的工作目录（opencode ctx.directory），首条消息创建会话时生效 */
  directory?: string
  /** [S2/B4] 客户端消息幂等 id：自动/手动重试复用同一 id，服务端据此去重 */
  client_msg_id?: string
}

export interface MultiAgentMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  agents: AgentStreamData[]
  timestamp: Date
  isError?: boolean
  errorInfo?: ChatError
  /** 流式中的占位消息（未完成），持久化/合并时过滤 */
  live?: boolean
  /** [S2/B4] 幂等 id：user 消息首次发送时生成，重试时复用，保证服务端去重 */
  clientMsgId?: string
}
