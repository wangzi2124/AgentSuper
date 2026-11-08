<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchWithTimeout } from '../api/fetch'

interface MCPServer {
  name: string
  transport: string
  enabled: boolean
  status: string
  error: string
  tools: string[]
  resources: Array<Record<string, unknown>>
  registered: string[]
  has_instructions: boolean
}

const sessionId = ref('')
const servers = ref<MCPServer[]>([])
const tools = ref<Record<string, string[]>>({})
const resources = ref<Record<string, Array<Record<string, unknown>>>>({})
const instructions = ref<Record<string, string>>({})
const error = ref('')
const busy = ref(false)
const form = ref({
  name: '',
  type: 'stdio',
  command: '',
  args: '',
  url: '',
  env: '',
  headers: '',
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

async function load() {
  busy.value = true
  error.value = ''
  try {
    const body = await request<{ servers: MCPServer[] }>(`/api/v2/mcp${query()}`)
    servers.value = body.servers
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
    }
    await request(`/api/v2/mcp/servers${query()}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    form.value = { name: '', type: 'stdio', command: '', args: '', url: '', env: '', headers: '' }
    await load()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function connect(name: string) {
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

async function remove(name: string) {
  if (!confirm(`删除 MCP 服务器 ${name}？`)) return
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

onMounted(load)
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
            <span class="spacer" />
            <button class="link" type="button" @click="showTools(server.name)">工具</button>
            <button class="link" type="button" @click="showResources(server.name)">资源</button>
            <button v-if="server.has_instructions" class="link" type="button" @click="showInstructions(server.name)">指令</button>
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
          <pre v-if="instructions[server.name]" class="muted">{{ instructions[server.name] }}</pre>
        </li>
        <li v-if="!servers.length" class="muted">未配置 MCP 服务器</li>
      </ul>
      <p class="muted">OAuth 尚未支持：POST /api/v2/mcp/servers/{name}/auth 会明确返回 501，请手动配置 headers。</p>
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
</style>
