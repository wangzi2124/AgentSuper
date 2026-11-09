<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { fetchWithTimeout } from '../api/fetch'

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
const form = ref({
  name: '',
  type: 'stdio',
  command: '',
  args: '',
  url: '',
  env: '',
  headers: '',
  oauthClientId: '',
  oauthClientSecret: '',
  oauthScopes: '',
  oauthRedirectUri: '',
})

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

function parseLines(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const idx = line.indexOf('=')
    if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
  }
  return out
}

async function createServer() {
  busy.value = true
  error.value = ''
  try {
    const payload: Record<string, unknown> = {
      name: form.value.name,
      type: form.value.type,
      enabled: true,
    }
    if (form.value.type === 'stdio') {
      payload.command = form.value.command
      payload.args = form.value.args.split(/\s+/).filter(Boolean)
      payload.env = parseLines(form.value.env)
    } else {
      payload.url = form.value.url
      payload.headers = parseLines(form.value.headers)
      const oauth: Record<string, unknown> = {}
      if (form.value.oauthClientId.trim()) oauth.client_id = form.value.oauthClientId.trim()
      if (form.value.oauthClientSecret.trim()) oauth.client_secret = form.value.oauthClientSecret.trim()
      if (form.value.oauthScopes.trim()) oauth.scopes = form.value.oauthScopes.trim().split(/\s+/).filter(Boolean)
      if (form.value.oauthRedirectUri.trim()) oauth.redirect_uri = form.value.oauthRedirectUri.trim()
      if (Object.keys(oauth).length) payload.oauth = oauth
    }
    await request(`/api/v2/mcp/servers${query()}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    form.value = {
      name: '',
      type: 'stdio',
      command: '',
      args: '',
      url: '',
      env: '',
      headers: '',
      oauthClientId: '',
      oauthClientSecret: '',
      oauthScopes: '',
      oauthRedirectUri: '',
    }
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
  <div class="mcp-settings">
    <header class="page-head">
      <div>
        <h1>MCP 服务器</h1>
        <p>工具与资源来自真实 MCP 握手；连接失败会显示错误，不会伪造成功。</p>
      </div>
      <div class="head-actions">
        <input v-model="sessionId" class="text-input" placeholder="会话 ID（可选，项目级）" @change="load" />
        <button class="btn" :disabled="busy" @click="load">刷新</button>
      </div>
    </header>

    <p v-if="error" class="error">{{ error }}</p>

    <section class="card">
      <h2>已配置服务器</h2>
      <ul class="server-list">
        <li v-for="server in servers" :key="server.name">
          <div class="server-line">
            <strong>{{ server.name }}</strong>
            <span class="tag">{{ server.transport }}</span>
            <span class="tag" :class="server.status === 'error' ? 'danger' : ''">{{ server.status }}</span>
            <span v-if="server.has_instructions" class="tag">instructions</span>
            <span v-if="server.oauth && (server.authorized || server.auth_required)" class="tag">{{ server.authorized ? 'oauth' : 'oauth-required' }}</span>
            <span class="spacer" />
            <button class="link" type="button" @click="showTools(server.name)">工具</button>
            <button class="link" type="button" @click="showResources(server.name)">资源</button>
            <button class="link" type="button" @click="showPrompts(server.name)">Prompts</button>
            <button v-if="server.has_instructions" class="link" type="button" @click="showInstructions(server.name)">指令</button>
            <button
              v-if="server.enabled && server.oauth && !server.authorized"
              class="link"
              type="button"
              @click="authorize(server.name)"
            >OAuth 授权</button>
            <button
              v-else-if="server.enabled && server.oauth && server.auth_required"
              class="link"
              type="button"
              @click="authorize(server.name)"
            >重新授权</button>
            <button
              v-if="server.oauth && server.authorized"
              class="link"
              type="button"
              @click="clearAuth(server.name)"
            >清除授权</button>
            <button
              v-if="server.status === 'connected'"
              class="link"
              type="button"
              @click="disconnect(server.name)"
            >断开</button>
            <button v-else class="link" type="button" @click="connect(server.name)">连接</button>
            <button class="link danger" type="button" @click="remove(server.name)">删除</button>
          </div>
          <p v-if="server.error" class="error">{{ server.error }}</p>
          <p class="muted">注册工具：{{ server.registered.join(', ') || '无' }}</p>
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
                  class="btn"
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
                    class="text-input"
                    :placeholder="arg.description || ''"
                  />
                </label>
              </div>
              <pre v-if="promptResults[promptKey(server.name, prompt.name)]" class="prompt-result">{{ promptText(promptResults[promptKey(server.name, prompt.name)]) }}</pre>
            </div>
          </div>
          <pre v-if="instructions[server.name]" class="muted">{{ instructions[server.name] }}</pre>
        </li>
        <li v-if="!servers.length" class="muted">未配置 MCP 服务器</li>
      </ul>
      <p class="muted">HTTP 服务器支持 OAuth 2.1（PKCE + 动态注册）；需要登录时点击「OAuth 授权」。Claude 风格的 headers 仍可手动配置。</p>
    </section>

    <section class="card">
      <h2>新增服务器</h2>
      <form class="server-form" @submit.prevent="createServer">
        <label>名称<input v-model="form.name" class="text-input" required /></label>
        <label>传输
          <select v-model="form.type" class="text-input">
            <option value="stdio">stdio</option>
            <option value="http">Streamable HTTP</option>
          </select>
        </label>
        <template v-if="form.type === 'stdio'">
          <label>命令<input v-model="form.command" class="text-input" placeholder="npx" required /></label>
          <label>参数<input v-model="form.args" class="text-input" placeholder="-y @modelcontextprotocol/server-filesystem ." /></label>
          <label>环境变量（每行 KEY=VALUE）<textarea v-model="form.env" rows="2" class="text-input" /></label>
        </template>
        <template v-else>
          <label>URL<input v-model="form.url" class="text-input" placeholder="https://example.com/mcp" required /></label>
          <label>Headers（每行 KEY=VALUE）<textarea v-model="form.headers" rows="2" class="text-input" /></label>
          <label>OAuth Client ID（可选，留空自动动态注册）<input v-model="form.oauthClientId" class="text-input" /></label>
          <label>OAuth Client Secret（可选）<input v-model="form.oauthClientSecret" type="password" class="text-input" /></label>
          <label>OAuth Scopes（空格分隔，可选）<input v-model="form.oauthScopes" class="text-input" placeholder="read write" /></label>
          <label>OAuth Redirect URI（可选）<input v-model="form.oauthRedirectUri" class="text-input" placeholder="默认使用当前 API 回调" /></label>
        </template>
        <button class="btn primary" type="submit" :disabled="busy">保存并连接</button>
      </form>
    </section>
  </div>
</template>

<style scoped>
.mcp-settings { padding: 24px; max-width: 1000px; margin: 0 auto; color: var(--text); }
.page-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.page-head h1 { margin: 0 0 4px; font-size: 20px; }
.page-head p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.head-actions { display: flex; gap: 8px; }
.card { margin-top: 18px; padding: 16px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
.card h2 { margin: 0 0 12px; font-size: 15px; }
.server-list { list-style: none; margin: 0; padding: 0; }
.server-list li { padding: 10px 0; border-bottom: 1px solid var(--border); }
.server-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.spacer { flex: 1; }
.server-form { display: flex; flex-direction: column; gap: 8px; max-width: 520px; }
.server-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-secondary); }
.text-input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text); font-size: 13px; }
.btn { padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); cursor: pointer; }
.btn.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.tag { font-size: 11px; padding: 1px 6px; border: 1px solid var(--border); border-radius: 4px; color: var(--text-secondary); }
.tag.danger { color: var(--danger, #d33); border-color: currentColor; }
.link { border: 0; background: none; color: var(--primary); cursor: pointer; font-size: 12px; }
.link.danger { color: var(--danger, #d33); }
.muted { color: var(--text-secondary); font-size: 12px; margin: 4px 0 0; }
.error { color: var(--danger, #d33); font-size: 12px; }
.prompt-panel { margin-top: 8px; padding: 8px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg); }
.prompt-item + .prompt-item { margin-top: 8px; padding-top: 8px; border-top: 1px solid var(--border); }
.prompt-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.prompt-head .muted { margin: 0; }
.prompt-args { display: flex; flex-direction: column; gap: 6px; margin-top: 6px; }
.prompt-args label { display: flex; flex-direction: column; gap: 2px; font-size: 12px; color: var(--text-secondary); }
.required { color: var(--danger, #d33); margin-left: 2px; }
.prompt-result { margin: 6px 0 0; padding: 8px; border-radius: 6px; background: var(--surface); font-size: 12px; white-space: pre-wrap; word-break: break-word; max-height: 280px; overflow: auto; }
</style>
