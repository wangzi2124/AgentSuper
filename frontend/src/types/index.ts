export interface Source {
  document_id: string
  content: string
  score: number
}

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
}

export interface ChatResponse {
  answer: string
  sources: Source[]
  conversation_id: string
  steps?: AgentStep[]
}

export interface SSEEvent {
  type: 'step_start' | 'step_end' | 'tool_start' | 'tool_end' | 'done' | 'error'
  step_id?: string
  name?: string
  status?: string
  detail?: string
  duration_ms?: number
  tool_name?: string
  tool_args?: Record<string, unknown>
  tool_result?: string
  answer?: string
  sources?: Source[]
  conversation_id?: string
  steps?: AgentStep[]
  error?: string
}

export interface FileContent {
  filename: string
  data: string
  mime_type: string
}

export interface ChatRequest {
  message: string
  conversation_id?: string
  model?: string
  use_vector_db?: boolean
  files?: FileContent[]
}

export interface Document {
  id: string
  filename: string
  size: number
  chunk_count: number
  created_at: string
}

export interface DocumentListResponse {
  documents: Document[]
  total: number
}

export interface DocumentResponse {
  id: string
  filename: string
  size: number
  chunk_count: number
  created_at: string
}

export interface UploadResponse {
  task_id: string
}

export interface TaskProgress {
  task_id: string
  filename: string
  status: 'pending' | 'processing' | 'completed' | 'failed'
  progress: number
  stage: string
  result?: DocumentResponse | null
  error?: string | null
}

export interface DeleteResponse {
  message: string
}

export interface Chunk {
  id: string
  text: string
  metadata: Record<string, unknown>
}

export interface ChunkListResponse {
  chunks: Chunk[]
  total: number
  offset: number
  limit: number
}

export interface Skill {
  name: string
  description: string
  path: string
  enabled: boolean
}

export interface Plugin {
  name: string
  version: string
  description: string
  enabled: boolean
}

export interface GeneratedFile {
  filename: string
  size: number
  created_at: string
}

export interface GeneratedFileList {
  files: GeneratedFile[]
  total: number
}

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

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  steps?: AgentStep[]
  files?: { filename: string; mime_type: string }[]
  timestamp: Date
}
