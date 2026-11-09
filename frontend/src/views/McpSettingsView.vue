<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { fetchWithTimeout } from '../api/fetch'
import McpServerEditModal from '../components/McpServerEditModal.vue'

interface MCPPromptArgument {
  name: string
  description?: string
  required?: boolean
}

interface MCPPrompt {
  name: string
  description?: string
  arguments?: MCPPromptArgument[]
}

interface MCPServer {
  name: string
  transport: string
  enabled: boolean
  status: string
  error: string
  tools: string[]
  resources: Array<Record<string, unknown>>
  prompts?: MCPPrompt[]
  registered: string[]
  has_instructions: boolean
  oauth?: boolean
  authorized?: boolean
  auth_required?: boolean
}

const sessionId = ref('')
const servers = ref<MCPServer[]>([])
const tools = ref<Record<string, string[]>>({})
const resources = ref<Record<string, Array<Record<string, unknown>>>>({})
const prompts = ref<Record<string, MCPPrompt[]>>({})
const promptForms = ref<Record<string, Record<string, string>>>({})
const promptResults = ref<Record<string, unknown>>({})
const promptBusy = ref('')
const instructions = ref<Record<string, string>>({})
const error = ref('')
const busy = ref(false)
const authPollers = new Map<string, number>()
const showEditor = ref(false)
const editingServer = ref<Record<string, unknown> | null>(null)
const editingScope = ref('')

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetchWithTimeout(url, init)
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(body?.detail || `${res.status} ${res.statusText}`)
  return body as T
}

function query(): string {
  return sessionId.value ? `?session_id=${encodeURIComponent(sessionId.value)}` : ''
}

function promptKey(server: string, prompt: string): string {
  return `${server}::${prompt}`
}

function statusClass(server: MCPServer): string {
  if (server.status === 'connected') return 'badge-enabled'
  return 'badge-disabled'
}

function setPrompts(server: string, items: MCPPrompt[]) {
  prompts.value = { ...prompts.value, [server]: items }
  const forms = { ...promptForms.value }
  for (const prompt of items) {
    const key = promptKey(server, prompt.name)
    if (!forms[key]) forms[key] = {}
  }
  promptForms.value = forms
}

async function load() {
  busy.value = true
  error.value = ''
  try {
    const body = await request<{ servers: MCPServer[] }>(`/api/v2/mcp${query()}`)
    servers.value = body.servers
    for (const server of body.servers) setPrompts(server.name, server.prompts || [])
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

function openCreate() {
  editingServer.value = null
  editingScope.value = ''
  showEditor.value = true
}

async function openEdit(server: MCPServer) {
  busy.value = true
  error.value = ''
  try {
    const body = await request<{ config: Record<string, unknown>; scope: string }>(
      `/api/v2/mcp/servers/${encodeURIComponent(server.name)}/config${query()}`,
    )
    editingServer.value = { ...body.config, name: body.config.name || server.name }
    editingScope.value = body.scope
    showEditor.value = true
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function saveServer(payload: Record<string, unknown>) {
  busy.value = true
  error.value = ''
  const target = editingServer.value?.name as string | undefined
  const scope = target
    ? editingScope.value || (sessionId.value ? 'project' : 'global')
    : sessionId.value
      ? 'project'
      : 'global'
  const body = { ...payload, scope }
  try {
    const url = target
      ? `/api/v2/mcp/servers/${encodeURIComponent(target)}${query()}`
      : `/api/v2/mcp/servers${query()}`
    await request(url, {
      method: target ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    showEditor.value = false
    editingServer.value = null
    editingScope.value = ''
    await load()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function connect(name: string) {
  stopAuthPoll(name)
  busy.value = true
  try {
    await request(`/api/v2/mcp/${encodeURIComponent(name)}/connect`, { method: 'POST' })
    await load()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function disconnect(name: string) {
  stopAuthPoll(name)
  busy.value = true
  try {
    await request(`/api/v2/mcp/${encodeURIComponent(name)}/disconnect`, { method: 'POST' })
    await load()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

function stopAuthPoll(name: string) {
  const id = authPollers.get(name)
  if (id !== undefined) {
    window.clearInterval(id)
    authPollers.delete(name)
  }
}

async function authorize(name: string) {
  busy.value = true
  error.value = ''
  const popup = window.open('about:blank', '_blank')
  try {
    const body = await request<{ authorization_url: string }>(
      `/api/v2/mcp/servers/${encodeURIComponent(name)}/auth${query()}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: '{}',
      },
    )
    if (!popup) {
      error.value = `浏览器拦截了授权窗口，请手动打开：${body.authorization_url}`
      return
    }
    try {
      popup.opener = null
      popup.location.href = body.authorization_url
    } catch {
      popup.close()
      error.value = `请手动打开授权链接：${body.authorization_url}`
      return
    }
    stopAuthPoll(name)
    const started = Date.now()
    const timer = window.setInterval(async () => {
      try {
        const status = await request<{ authorized: boolean }>(
          `/api/v2/mcp/servers/${encodeURIComponent(name)}/auth${query()}`,
        )
        if (status.authorized) {
          stopAuthPoll(name)
          await connect(name)
        } else if (Date.now() - started > 180000) {
          stopAuthPoll(name)
          error.value = 'OAuth 授权超时，请重试'
        }
      } catch (err) {
        stopAuthPoll(name)
        error.value = String((err as Error).message || err)
      }
    }, 2000)
    authPollers.set(name, timer)
  } catch (err) {
    if (popup && !popup.closed) popup.close()
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function clearAuth(name: string) {
  stopAuthPoll(name)
  busy.value = true
  error.value = ''
  try {
    await request(`/api/v2/mcp/servers/${encodeURIComponent(name)}/auth${query()}`, {
      method: 'DELETE',
    })
    await load()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function remove(name: string) {
  if (!confirm(`删除 MCP 服务器 ${name}？`)) return
  stopAuthPoll(name)
  busy.value = true
  try {
    const scope = sessionId.value ? 'project' : 'global'
    await request(
      `/api/v2/mcp/servers/${encodeURIComponent(name)}${query()}${sessionId.value ? '&' : '?'}scope=${scope}`,
      { method: 'DELETE' },
    )
    await load()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function showTools(name: string) {
  const body = await request<{ tools: string[] }>(
    `/api/v2/mcp/servers/${encodeURIComponent(name)}/tools`,
  )
  tools.value = { ...tools.value, [name]: body.tools }
}

async function showResources(name: string) {
  const body = await request<{ resources: Array<Record<string, unknown>> }>(
    `/api/v2/mcp/servers/${encodeURIComponent(name)}/resources`,
  )
  resources.value = { ...resources.value, [name]: body.resources }
}

async function showInstructions(name: string) {
  const body = await request<{ instructions: string }>(
    `/api/v2/mcp/servers/${encodeURIComponent(name)}/instructions`,
  )
  instructions.value = { ...instructions.value, [name]: body.instructions }
}

async function showPrompts(name: string) {
  busy.value = true
  error.value = ''
  try {
    const body = await request<{ prompts: MCPPrompt[] }>(
      `/api/v2/mcp/servers/${encodeURIComponent(name)}/prompts`,
    )
    setPrompts(name, body.prompts)
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function runPrompt(server: string, prompt: MCPPrompt) {
  const key = promptKey(server, prompt.name)
  promptBusy.value = key
  error.value = ''
  try {
    const body = await request<{ prompt: unknown }>(
      `/api/v2/mcp/servers/${encodeURIComponent(server)}/prompts/get`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          prompt: prompt.name,
          arguments: promptForms.value[key] || {},
        }),
      },
    )
    promptResults.value = { ...promptResults.value, [key]: body.prompt }
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    promptBusy.value = ''
  }
}

function promptText(result: unknown): string {
  const messages = (result as { messages?: unknown })?.messages
  if (Array.isArray(messages)) {
    return messages
      .map((raw) => {
        const message = raw as { role?: string; content?: unknown }
        const content = message.content
        let text = ''
        if (typeof content === 'string') {
          text = content
        } else if (content && typeof content === 'object') {
          const block = content as { type?: string; text?: string; mimeType?: string; resource?: { text?: string } }
          if (block.type === 'text') text = block.text || ''
          else if (block.type === 'resource') text = block.resource?.text || JSON.stringify(block.resource)
          else if (block.type === 'image') text = `[image ${block.mimeType || ''}]`.trim()
          else text = JSON.stringify(block)
        } else {
          text = JSON.stringify(content)
        }
        return `【${message.role || 'unknown'}】\n${text}`
      })
      .join('\n\n')
  }
  return JSON.stringify(result, null, 2)
}

onMounted(load)
onUnmounted(() => {
  for (const id of authPollers.values()) window.clearInterval(id)
  authPollers.clear()
})
</script>

<template>
  <div class="page-header">
    <div class="header-row">
      <div>
        <h2>MCP 服务器</h2>
        <p>工具与资源来自真实 MCP 握手；连接失败会显示错误，不会伪造成功。</p>
      </div>
      <div class="head-actions">
        <input v-model="sessionId" class="ctrl session-input" placeholder="会话 ID（可选，项目级）" @change="load" />
        <button class="btn" :disabled="busy" @click="load">刷新</button>
      </div>
    </div>
  </div>

  <div class="page-content mcp-settings">
    <p v-if="error" class="form-error">{{ error }}</p>

    <div class="section-head">
      <div class="section-title">
        已配置服务器
        <span v-if="servers.length" class="badge type-badge">{{ servers.length }}</span>
      </div>
      <button class="btn btn-primary" type="button" :disabled="busy" @click="openCreate">
        + 新增服务器
      </button>
    </div>

    <div v-if="servers.length" class="list-meta">{{ servers.length }} 个 MCP 服务器</div>
    <div v-if="servers.length" class="server-list">
      <div v-for="server in servers" :key="server.name" class="card server-card">
        <div class="server-main">
          <div class="server-avatar">🔌</div>
          <div class="server-info">
            <div class="server-title">
              {{ server.name }}
              <span class="badge type-badge">{{ server.transport }}</span>
              <span v-if="server.has_instructions" class="badge type-badge">instructions</span>
              <span v-if="server.oauth && (server.authorized || server.auth_required)" class="badge type-badge">
                {{ server.authorized ? 'oauth' : 'oauth-required' }}
              </span>
            </div>
            <div class="server-sub">注册工具：{{ server.registered.join(', ') || '无' }}</div>
          </div>
          <span class="badge" :class="statusClass(server)">{{ server.status }}</span>
          <div class="server-actions">
            <button class="btn btn-sm" type="button" @click="showTools(server.name)">工具</button>
            <button class="btn btn-sm" type="button" @click="showResources(server.name)">资源</button>
            <button class="btn btn-sm" type="button" @click="showPrompts(server.name)">Prompts</button>
            <button v-if="server.has_instructions" class="btn btn-sm" type="button" @click="showInstructions(server.name)">指令</button>
            <button
              v-if="server.enabled && server.oauth && !server.authorized"
              class="btn btn-sm"
              type="button"
              @click="authorize(server.name)"
            >OAuth 授权</button>
            <button
              v-else-if="server.enabled && server.oauth && server.auth_required"
              class="btn btn-sm"
              type="button"
              @click="authorize(server.name)"
            >重新授权</button>
            <button
              v-if="server.oauth && server.authorized"
              class="btn btn-sm"
              type="button"
              @click="clearAuth(server.name)"
            >清除授权</button>
            <button
              v-if="server.status === 'connected'"
              class="btn btn-sm"
              type="button"
              @click="disconnect(server.name)"
            >断开</button>
            <button v-else class="btn btn-sm btn-primary" type="button" @click="connect(server.name)">连接</button>
            <button class="btn btn-sm" type="button" @click="openEdit(server)">编辑</button>
            <button class="btn btn-sm btn-danger" type="button" @click="remove(server.name)">删除</button>
          </div>
        </div>

        <p v-if="server.error" class="form-error">{{ server.error }}</p>
        <p v-if="tools[server.name]" class="muted">可用工具：{{ tools[server.name].join(', ') || '无' }}</p>
        <div v-if="resources[server.name]" class="muted">
          资源：
          <span v-for="(res, idx) in resources[server.name]" :key="idx">
            {{ res.uri || res.name || 'unknown' }}
          </span>
        </div>
        <div v-if="prompts[server.name]" class="prompt-panel">
          <p v-if="!prompts[server.name].length" class="muted">该服务器未暴露 prompts</p>
          <div v-for="prompt in prompts[server.name]" :key="prompt.name" class="prompt-item">
            <div class="prompt-head">
              <strong>{{ prompt.name }}</strong>
              <span v-if="prompt.description" class="muted">{{ prompt.description }}</span>
              <span class="spacer" />
              <button
                class="btn btn-sm btn-primary"
                type="button"
                :disabled="promptBusy === promptKey(server.name, prompt.name) || server.status !== 'connected'"
                :title="server.status === 'connected' ? '调用 prompts/get' : '请先连接服务器'"
                @click="runPrompt(server.name, prompt)"
              >{{ promptBusy === promptKey(server.name, prompt.name) ? '获取中…' : '获取' }}</button>
            </div>
            <div v-if="prompt.arguments?.length" class="prompt-args">
              <label v-for="arg in prompt.arguments" :key="arg.name">
                <span>{{ arg.name }}<span v-if="arg.required" class="required">*</span></span>
                <input
                  v-model="promptForms[promptKey(server.name, prompt.name)][arg.name]"
                  class="ctrl"
                  :placeholder="arg.description || ''"
                />
              </label>
            </div>
            <pre v-if="promptResults[promptKey(server.name, prompt.name)]" class="prompt-result">{{ promptText(promptResults[promptKey(server.name, prompt.name)]) }}</pre>
          </div>
        </div>
        <pre v-if="instructions[server.name]" class="muted">{{ instructions[server.name] }}</pre>
      </div>
      <p class="muted">HTTP 服务器支持 OAuth 2.1（PKCE + 动态注册）；需要登录时点击「OAuth 授权」。Claude 风格的 headers 仍可手动配置。</p>
    </div>
    <div v-else class="empty-state">
      <div class="icon">🔌</div>
      <p>未配置 MCP 服务器</p>
      <p style="font-size: 13px; margin-top: 4px">点击「新增服务器」添加。</p>
    </div>

    <McpServerEditModal
      :show="showEditor"
      :server="editingServer"
      :busy="busy"
      @close="showEditor = false"
      @submit="saveServer"
    />
  </div>
</template>

<style scoped>
.mcp-settings {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.header-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.head-actions { display: flex; gap: 8px; align-items: center; }
.session-input { width: 240px; }

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.section-title {
  font-size: 16px;
  font-weight: 800;
  letter-spacing: -0.02em;
  display: flex;
  align-items: center;
  gap: 8px;
}
.list-meta { font-size: 13px; color: var(--text-secondary); font-weight: 500; margin-top: -8px; }

.server-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  animation: fadeSlideUp 0.4s var(--ease);
}
.server-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-radius: var(--radius-lg);
  transition: all var(--duration) var(--ease);
}
.server-card:hover {
  box-shadow: var(--shadow-md);
  border-color: color-mix(in srgb, var(--primary) 25%, var(--border));
  transform: translateY(-1px);
}
.server-main {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}
.server-avatar {
  font-size: 20px;
  width: 48px;
  height: 48px;
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  background: color-mix(in srgb, var(--primary) 10%, var(--bg-subtle));
  border: 1px solid var(--border-subtle);
}
.server-info { flex: 1; min-width: 0; }
.server-title {
  font-weight: 700;
  font-size: 14px;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.server-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.server-actions { display: flex; gap: 8px; flex-wrap: wrap; flex-shrink: 0; }

.type-badge {
  font-size: 10px;
  padding: 2px 8px;
  border-radius: var(--radius-pill);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  border: 1px solid var(--border-subtle);
}

.btn-sm { padding: 7px 14px; font-size: 12px; }

.ctrl {
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: all var(--duration) var(--ease);
  box-sizing: border-box;
}
.ctrl:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-glow);
}

.spacer { flex: 1; }
.muted { color: var(--text-secondary); font-size: 12px; margin: 4px 0 0; line-height: 1.6; }
.form-error { color: var(--danger); font-size: 12.5px; }

.prompt-panel {
  margin-top: 4px;
  padding: 12px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius);
  background: var(--bg-subtle);
}
.prompt-item + .prompt-item { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--border); }
.prompt-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.prompt-head .muted { margin: 0; }
.prompt-args { display: flex; flex-direction: column; gap: 8px; margin-top: 8px; }
.prompt-args label { display: flex; flex-direction: column; gap: 3px; font-size: 12px; color: var(--text-secondary); }
.required { color: var(--danger); margin-left: 2px; }
.prompt-result {
  margin: 8px 0 0;
  padding: 10px;
  border-radius: var(--radius-sm);
  background: var(--surface);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 280px;
  overflow: auto;
}

@media (max-width: 768px) {
  .header-row { flex-direction: column; }
  .session-input { width: 100%; }
  .server-actions { width: 100%; }
}
</style>
