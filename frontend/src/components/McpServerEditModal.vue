<script setup lang="ts">
import { ref, watch } from 'vue'

interface RawServer {
  name?: string
  type?: string
  transport?: string
  command?: string
  args?: string[] | string
  env?: Record<string, string>
  url?: string
  headers?: Record<string, string>
  enabled?: boolean
  oauth?: Record<string, unknown>
  client_id?: string
  client_secret?: string
  scopes?: string[] | string
  redirect_uri?: string
}

const props = defineProps<{
  show: boolean
  server?: RawServer | null
  busy?: boolean
}>()

const emit = defineEmits<{
  close: []
  submit: [payload: Record<string, unknown>]
}>()

function defaults() {
  return {
    name: '',
    type: 'stdio',
    command: '',
    args: '',
    url: '',
    env: '',
    headers: '',
    enabled: true,
    oauthClientId: '',
    oauthClientSecret: '',
    oauthScopes: '',
    oauthRedirectUri: '',
  }
}

const form = ref(defaults())

function lines(mapping?: Record<string, string>): string {
  if (!mapping || typeof mapping !== 'object') return ''
  return Object.entries(mapping)
    .map(([key, value]) => `${key}=${value}`)
    .join('\n')
}

function oauthValue(server: RawServer, key: string): string {
  const nested = server.oauth && typeof server.oauth === 'object' ? server.oauth[key] : undefined
  const flat = (server as Record<string, unknown>)[key]
  const value = nested ?? flat
  return value != null ? String(value) : ''
}

function scopesText(server: RawServer): string {
  const nested = server.oauth && typeof server.oauth === 'object' ? server.oauth.scopes : undefined
  const raw = server.scopes ?? nested
  if (Array.isArray(raw)) return raw.join(' ')
  if (typeof raw === 'string') return raw
  return ''
}

watch(
  () => props.show,
  (visible) => {
    if (!visible) return
    const server = props.server
    if (!server) {
      form.value = defaults()
      return
    }
    const rawType = String(server.type || server.transport || 'stdio').toLowerCase()
    form.value = {
      name: server.name || '',
      type: rawType === 'stdio' ? 'stdio' : 'http',
      command: server.command || '',
      args: Array.isArray(server.args) ? server.args.join(' ') : server.args || '',
      url: server.url || '',
      env: lines(server.env),
      headers: lines(server.headers),
      enabled: server.enabled !== false,
      oauthClientId: oauthValue(server, 'client_id'),
      oauthClientSecret: oauthValue(server, 'client_secret'),
      oauthScopes: scopesText(server),
      oauthRedirectUri: oauthValue(server, 'redirect_uri'),
    }
  },
)

function parseLines(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const idx = line.indexOf('=')
    if (idx > 0) out[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
  }
  return out
}

function submit() {
  if (!form.value.name.trim()) return
  const payload: Record<string, unknown> = {
    name: form.value.name.trim(),
    type: form.value.type,
    enabled: form.value.enabled,
  }
  if (form.value.type === 'stdio') {
    payload.command = form.value.command.trim()
    payload.args = form.value.args.split(/\s+/).filter(Boolean)
    payload.env = parseLines(form.value.env)
  } else {
    payload.url = form.value.url.trim()
    payload.headers = parseLines(form.value.headers)
    const oauth: Record<string, unknown> = {}
    if (form.value.oauthClientId.trim()) oauth.client_id = form.value.oauthClientId.trim()
    if (form.value.oauthClientSecret.trim()) oauth.client_secret = form.value.oauthClientSecret.trim()
    if (form.value.oauthScopes.trim())
      oauth.scopes = form.value.oauthScopes.trim().split(/\s+/).filter(Boolean)
    if (form.value.oauthRedirectUri.trim())
      oauth.redirect_uri = form.value.oauthRedirectUri.trim()
    if (Object.keys(oauth).length) payload.oauth = oauth
  }
  emit('submit', payload)
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="mcp-modal-overlay" @click.self="emit('close')">
      <div class="mcp-modal" role="dialog" aria-modal="true">
        <div class="mcp-modal-header">
          <span class="mcp-modal-title">{{ server ? '编辑 MCP 服务器' : '新增 MCP 服务器' }}</span>
          <button class="mcp-modal-close" type="button" title="关闭" @click="emit('close')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>

        <form class="mcp-modal-body" @submit.prevent="submit">
          <label>名称<input v-model="form.name" class="ctrl" :disabled="!!server" required /></label>
          <label>传输
            <select v-model="form.type" class="ctrl">
              <option value="stdio">stdio</option>
              <option value="http">Streamable HTTP</option>
            </select>
          </label>
          <template v-if="form.type === 'stdio'">
            <label>命令<input v-model="form.command" class="ctrl" placeholder="npx" required /></label>
            <label>参数<input v-model="form.args" class="ctrl" placeholder="-y @modelcontextprotocol/server-filesystem ." /></label>
            <label>环境变量（每行 KEY=VALUE）<textarea v-model="form.env" rows="2" class="ctrl" /></label>
          </template>
          <template v-else>
            <label>URL<input v-model="form.url" class="ctrl" placeholder="https://example.com/mcp" required /></label>
            <label>Headers（每行 KEY=VALUE）<textarea v-model="form.headers" rows="2" class="ctrl" /></label>
            <label>OAuth Client ID（可选，留空自动动态注册）<input v-model="form.oauthClientId" class="ctrl" /></label>
            <label>OAuth Client Secret（可选）<input v-model="form.oauthClientSecret" type="password" class="ctrl" /></label>
            <label>OAuth Scopes（空格分隔，可选）<input v-model="form.oauthScopes" class="ctrl" placeholder="read write" /></label>
            <label>OAuth Redirect URI（可选）<input v-model="form.oauthRedirectUri" class="ctrl" placeholder="默认使用当前 API 回调" /></label>
          </template>
          <label class="check"><input v-model="form.enabled" type="checkbox" /> 启用该服务器</label>

          <div class="mcp-modal-footer">
            <button class="btn" type="button" @click="emit('close')">取消</button>
            <button class="btn btn-primary" type="submit" :disabled="busy">{{ server ? '保存修改' : '保存并连接' }}</button>
          </div>
        </form>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.mcp-modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  animation: fadeIn 0.18s var(--ease);
}
.mcp-modal {
  width: min(560px, 100%);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background: var(--surface);
  color: var(--text);
  box-shadow: var(--shadow-2xl);
  overflow: hidden;
  animation: scaleIn 0.2s var(--ease-spring);
}
.mcp-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--border);
}
.mcp-modal-title { font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.mcp-modal-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all var(--duration) var(--ease);
}
.mcp-modal-close:hover { background: var(--bg-subtle); color: var(--text); }
.mcp-modal-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  overflow-y: auto;
}
.mcp-modal-body label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
}
.mcp-modal-body label.check {
  flex-direction: row;
  align-items: center;
  gap: 6px;
}
.ctrl {
  width: 100%;
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: all var(--duration) var(--ease);
}
.ctrl:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-glow);
}
.mcp-modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 4px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.96); }
  to { opacity: 1; transform: scale(1); }
}
</style>
