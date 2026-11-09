<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchWithTimeout } from '../api/fetch'
import AgentEditModal from '../components/AgentEditModal.vue'
import InstructionFilePicker from '../components/InstructionFilePicker.vue'
import { addInstructionEntry, listInstructions, removeInstructionEntry, setInstructionAutoDiscover } from '../api/instructions'

interface AgentInfo {
  name: string
  description: string
  mode: string
  tier: string
  model: string
  tools: string[]
  permission: Record<string, string>
  hidden: boolean
  disabled: boolean
  color: string
  steps: number
  source: string
}
interface CommandInfo {
  name: string
  description: string
  agent: string
  model: string
  source: string
}
interface PolicyInfo {
  action: string
  resource: string
  effect: string
  source: string
}
interface InstructionSource {
  id: string
  kind: string
  location: string
  scope: string
  loaded: boolean
  skipped_reason: string
  chars: number
  tokens: number
}
interface SettingEntry {
  name: string
  type: string
  default: unknown
  sensitive: boolean
}
interface ReferenceInfo {
  name: string
  kind: string
  description: string
  hidden: boolean
  source: string
  status: string
  error: string
}
interface WebSearchStatus {
  enabled: boolean
  provider: string
  available: string[]
  cooldown: Record<string, number>
}

const sessionId = ref('')
const agents = ref<AgentInfo[]>([])
const commands = ref<CommandInfo[]>([])
const policies = ref<PolicyInfo[]>([])
const instructions = ref<InstructionSource[]>([])
const configSchema = ref<Record<string, SettingEntry[]>>({})
const configValues = ref<Record<string, unknown>>({})
const configDraft = ref<Record<string, unknown>>({})
const references = ref<ReferenceInfo[]>([])
const policyDraft = ref<Array<{ action: string; resource: string; effect: string }>>([])
const newRule = ref({ action: 'tool.use', resource: '*', effect: 'deny' })
const websearch = ref<WebSearchStatus | null>(null)
const referenceForm = ref({ name: '', path: '', repository: '', branch: '', description: '' })
const error = ref('')
const busy = ref(false)

const showEditor = ref(false)
const editingAgent = ref<AgentInfo | null>(null)
const instructionEntries = ref<string[]>([])
const instructionWorkspace = ref('')
const instructionAutoDiscover = ref(true)
const showFilePicker = ref(false)

const scopeLabel = computed(() => (sessionId.value ? '项目级（当前会话工作区）' : '全局'))

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetchWithTimeout(url, init)
  const body = await res.json().catch(() => null)
  if (!res.ok) throw new Error(body?.detail || `${res.status} ${res.statusText}`)
  return body as T
}

function query(): string {
  return sessionId.value ? `?session_id=${encodeURIComponent(sessionId.value)}` : ''
}

async function loadAll() {
  busy.value = true
  error.value = ''
  try {
    const [agentBody, commandBody, policyBody, instructionBody, configBody, referenceBody, websearchBody] = await Promise.all([
      request<{ agents: AgentInfo[] }>(`/api/v2/agents${query()}`),
      request<{ commands: CommandInfo[] }>(`/api/v2/commands${query()}`),
      request<{ statements: PolicyInfo[] }>('/api/v2/policies'),
      listInstructions(sessionId.value || undefined),
      request<{ schema: Record<string, SettingEntry[]>; values: Record<string, unknown> }>(
        '/api/v2/config',
      ),
      request<{ references: ReferenceInfo[] }>(`/api/v2/references${query()}`),
      request<WebSearchStatus>('/api/v2/websearch'),
    ])
    agents.value = agentBody.agents
    commands.value = commandBody.commands
    policies.value = policyBody.statements
    instructions.value = instructionBody.sources
    instructionEntries.value = instructionBody.entries || []
    instructionWorkspace.value = instructionBody.workspace || ''
    instructionAutoDiscover.value = instructionBody.auto_discover ?? true
    configSchema.value = configBody.schema
    configValues.value = configBody.values
    configDraft.value = JSON.parse(JSON.stringify(configBody.values))
    references.value = referenceBody.references
    websearch.value = websearchBody
    const rules = (configBody.values.policies || []) as Array<Record<string, string>>
    policyDraft.value = rules.map((rule) => ({
      action: String(rule.action || ''),
      resource: String(rule.resource || ''),
      effect: String(rule.effect || 'deny'),
    }))
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

function openCreate() {
  editingAgent.value = null
  showEditor.value = true
}

function openEdit(agent: AgentInfo) {
  editingAgent.value = agent
  showEditor.value = true
}

async function saveAgent(payload: Record<string, unknown>) {
  busy.value = true
  error.value = ''
  const body = { ...payload, scope: sessionId.value ? 'project' : 'global' }
  try {
    const target = editingAgent.value?.name
    const url = target
      ? `/api/v2/agents/${encodeURIComponent(target)}${query()}`
      : `/api/v2/agents${query()}`
    await request(url, {
      method: target ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    showEditor.value = false
    editingAgent.value = null
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

function openFilePicker() {
  showFilePicker.value = true
}

async function addInstructionFile(path: string) {
  busy.value = true
  error.value = ''
  try {
    await addInstructionEntry(path, sessionId.value)
    showFilePicker.value = false
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function removeInstructionFile(entry: string) {
  busy.value = true
  error.value = ''
  try {
    await removeInstructionEntry(entry, sessionId.value)
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function toggleAutoDiscover(event: Event) {
  const enabled = (event.target as HTMLInputElement).checked
  busy.value = true
  error.value = ''
  try {
    await setInstructionAutoDiscover(enabled, sessionId.value)
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

function isJsonField(entry: SettingEntry): boolean {
  const type = entry.type.toLowerCase()
  return type.includes('dict') || type.includes('list')
}

function setJsonValue(name: string, event: Event) {
  const text = (event.target as HTMLTextAreaElement).value
  try {
    configDraft.value[name] = JSON.parse(text)
  } catch {
    error.value = `${name} 不是合法 JSON`
  }
}

function addRule() {
  if (!newRule.value.action.trim()) return
  policyDraft.value.push({ ...newRule.value, action: newRule.value.action.trim() })
  newRule.value = { action: 'tool.use', resource: '*', effect: 'deny' }
}

function removeRule(index: number) {
  policyDraft.value.splice(index, 1)
}

function moveRule(index: number, step: number) {
  const next = index + step
  if (next < 0 || next >= policyDraft.value.length) return
  const [item] = policyDraft.value.splice(index, 1)
  policyDraft.value.splice(next, 0, item)
}

async function saveRules() {
  busy.value = true
  error.value = ''
  try {
    await request('/api/v2/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: { policies: policyDraft.value } }),
    })
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function saveConfig() {  busy.value = true
  error.value = ''
  try {
    await request('/api/v2/config', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: configDraft.value }),
    })
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function rollbackConfig() {
  busy.value = true
  try {
    await request('/api/v2/config/rollback', { method: 'POST' })
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function saveReference() {
  busy.value = true
  error.value = ''
  try {
    await request(`/api/v2/references${query()}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        ...referenceForm.value,
        scope: sessionId.value ? 'project' : 'global',
      }),
    })
    referenceForm.value = { name: '', path: '', repository: '', branch: '', description: '' }
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function removeReference(name: string) {
  busy.value = true
  try {
    const scope = sessionId.value ? 'project' : 'global'
    await request(
      `/api/v2/references/${encodeURIComponent(name)}${query()}${sessionId.value ? '&' : '?'}scope=${scope}`,
      { method: 'DELETE' },
    )
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function refreshReference(name: string) {
  busy.value = true
  try {
    await request(`/api/v2/references/${encodeURIComponent(name)}/refresh${query()}`, {
      method: 'POST',
    })
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

async function removeAgent(agent: AgentInfo) {  if (!confirm(`删除 Agent ${agent.name}？`)) return
  busy.value = true
  try {
    const scope = sessionId.value ? 'project' : 'global'
    await request(
      `/api/v2/agents/${encodeURIComponent(agent.name)}${query()}${sessionId.value ? '&' : '?'}scope=${scope}`,
      { method: 'DELETE' },
    )
    await loadAll()
  } catch (err) {
    error.value = String((err as Error).message || err)
  } finally {
    busy.value = false
  }
}

onMounted(loadAll)
</script>

<template>
  <div class="page-header">
    <div class="header-row">
      <div>
        <h2>Agent 配置</h2>
        <p>Agent、斜杠命令、策略与项目指令均由服务端提供；此页只读写 API。</p>
      </div>
      <div class="head-actions">
        <input
          v-model="sessionId"
          class="ctrl session-input"
          placeholder="会话 ID（可选，项目级）"
          @change="loadAll"
        />
        <button class="btn" :disabled="busy" @click="loadAll">刷新</button>
      </div>
    </div>
  </div>

  <div class="page-content agent-settings">
    <p v-if="error" class="form-error">{{ error }}</p>

    <section class="section">
      <div class="section-head">
        <div class="section-title">
          Agents <span class="badge type-badge">{{ scopeLabel }}</span>
        </div>
        <button class="btn btn-primary" type="button" :disabled="busy" @click="openCreate">
          + 新增 Agent
        </button>
      </div>

      <div v-if="agents.length" class="list-meta">{{ agents.length }} 个 Agent</div>
      <div v-if="agents.length" class="agent-list">
        <div v-for="agent in agents" :key="agent.name" class="card agent-card">
          <div class="agent-avatar">{{ agent.name.slice(0, 1).toUpperCase() }}</div>
          <div class="agent-info">
            <div class="agent-title">
              {{ agent.name }}
              <span class="badge type-badge">{{ agent.mode }}</span>
              <span class="badge type-badge">{{ agent.source }}</span>
              <span v-if="agent.model" class="badge type-badge">{{ agent.model }}</span>
              <span v-if="agent.hidden" class="badge type-badge">隐藏</span>
            </div>
            <div class="agent-desc">{{ agent.description || '无描述' }}</div>
            <div class="agent-sub">工具：{{ agent.tools.length ? agent.tools.join(', ') : '全部' }}</div>
          </div>
          <span class="badge" :class="agent.disabled ? 'badge-disabled' : 'badge-enabled'">
            {{ agent.disabled ? '已禁用' : '已启用' }}
          </span>
          <div class="agent-actions">
            <button class="btn btn-sm" type="button" @click="openEdit(agent)">编辑</button>
            <button class="btn btn-sm btn-danger" type="button" @click="removeAgent(agent)">删除</button>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">
        <div class="icon">🤖</div>
        <p>暂无 Agent</p>
        <p style="font-size: 13px; margin-top: 4px">点击「新增 Agent」创建。</p>
      </div>
    </section>

    <AgentEditModal
      :show="showEditor"
      :agent="editingAgent"
      :busy="busy"
      :scope-label="scopeLabel"
      @close="showEditor = false"
      @submit="saveAgent"
    />

    <section class="card">
      <h3>斜杠命令</h3>
      <ul class="plain-list">
        <li v-for="command in commands" :key="command.name">
          <code>/{{ command.name }}</code>
          <span>{{ command.description }}</span>
          <span class="badge type-badge">{{ command.source }}</span>
          <span v-if="command.agent" class="badge type-badge">agent: {{ command.agent }}</span>
          <span v-if="command.model" class="badge type-badge">{{ command.model }}</span>
        </li>
        <li v-if="!commands.length" class="muted">未发现命令（写入 .agentsuper/commands/*.md）</li>
      </ul>
    </section>

    <section class="card">
      <h3>策略规则</h3>
      <div v-for="(rule, index) in policyDraft" :key="index" class="rule-row">
        <input v-model="rule.action" class="ctrl" placeholder="action" />
        <input v-model="rule.resource" class="ctrl" placeholder="resource（通配 *）" />
        <select v-model="rule.effect" class="ctrl">
          <option value="deny">deny</option>
          <option value="allow">allow</option>
        </select>
        <button class="btn btn-sm" type="button" @click="moveRule(index, -1)">上移</button>
        <button class="btn btn-sm" type="button" @click="moveRule(index, 1)">下移</button>
        <button class="btn btn-sm btn-danger" type="button" @click="removeRule(index)">删除</button>
      </div>
      <div class="rule-row rule-row-new">
        <input v-model="newRule.action" class="ctrl" placeholder="action，如 tool.use / websearch.use" />
        <input v-model="newRule.resource" class="ctrl" placeholder="resource，如 web_search 或 *" />
        <select v-model="newRule.effect" class="ctrl">
          <option value="deny">deny</option>
          <option value="allow">allow</option>
        </select>
        <button class="btn btn-sm" type="button" @click="addRule">添加</button>
        <button class="btn btn-sm btn-primary" type="button" :disabled="busy" @click="saveRules">保存规则</button>
      </div>
      <p class="muted">规则按顺序匹配，最后命中生效；deny 不会被 allow 放宽。审批授权仅对当前回合有效，不提供「允许并记住」。</p>
    </section>

    <section class="card">
      <h3>引用（References） <span class="badge type-badge">{{ scopeLabel }}</span></h3>
      <ul class="plain-list">
        <li v-for="ref in references" :key="ref.name">
          <strong>{{ ref.name }}</strong>
          <span class="badge type-badge">{{ ref.kind }}</span>
          <span class="badge" :class="ref.status === 'error' ? 'badge-disabled' : 'badge-enabled'">{{ ref.status }}</span>
          <span class="muted-inline">{{ ref.description }}</span>
          <span class="spacer" />
          <button v-if="ref.kind === 'git'" class="btn btn-sm" type="button" @click="refreshReference(ref.name)">刷新</button>
          <button class="btn btn-sm btn-danger" type="button" @click="removeReference(ref.name)">删除</button>
        </li>
        <li v-if="!references.length" class="muted">未配置引用</li>
      </ul>
      <form class="form-grid" @submit.prevent="saveReference">
        <label class="field">别名<input v-model="referenceForm.name" class="ctrl" required /></label>
        <label class="field">本地路径<input v-model="referenceForm.path" class="ctrl" /></label>
        <label class="field">Git 仓库<input v-model="referenceForm.repository" class="ctrl" /></label>
        <label class="field">分支<input v-model="referenceForm.branch" class="ctrl" /></label>
        <label class="field">描述<input v-model="referenceForm.description" class="ctrl" /></label>
        <div class="form-footer">
          <button class="btn btn-primary" type="submit" :disabled="busy">添加引用</button>
        </div>
      </form>
    </section>

    <section class="card">
      <h3>Web 搜索</h3>
      <p v-if="websearch" class="muted">
        状态：{{ websearch.enabled ? '启用' : '禁用' }}；选择：{{ websearch.provider }}；
        可用：{{ websearch.available.join(', ') || '无' }}
      </p>
      <p v-if="websearch && Object.keys(websearch.cooldown).length" class="muted">
        冷却中：{{ Object.keys(websearch.cooldown).join(', ') }}
      </p>
    </section>

    <section class="card">
      <h3>运行配置</h3>
      <div v-for="(entries, group) in configSchema" :key="group" class="config-group">
        <h4>{{ group }}</h4>
        <div class="config-grid">
          <label v-for="entry in entries" :key="entry.name" class="config-row">
            <span>{{ entry.name }}</span>
            <input
              v-if="typeof configDraft[entry.name] === 'boolean'"
              v-model="configDraft[entry.name]"
              type="checkbox"
            />
            <input
              v-else-if="typeof configDraft[entry.name] === 'number'"
              v-model.number="configDraft[entry.name]"
              type="number"
              class="ctrl"
            />
            <textarea
              v-else-if="isJsonField(entry)"
              :value="JSON.stringify(configDraft[entry.name])"
              rows="2"
              class="ctrl"
              @change="setJsonValue(entry.name, $event)"
            />
            <input v-else v-model="configDraft[entry.name]" class="ctrl" />
          </label>
        </div>
      </div>
      <div class="form-footer">
        <button class="btn btn-primary" type="button" :disabled="busy" @click="saveConfig">保存配置</button>
        <button class="btn" type="button" :disabled="busy" @click="rollbackConfig">回滚上次修改</button>
      </div>
      <p class="muted">配置修改即时生效；密钥类字段不会回传明文。</p>
    </section>

    <section class="card">
      <div class="section-head">
        <h3>项目指令来源</h3>
        <div class="section-actions">
          <label class="auto-toggle" title="关闭后不再自动注入工作区及上级目录的 AGENTS.md">
            <input
              type="checkbox"
              :checked="instructionAutoDiscover"
              :disabled="busy"
              @change="toggleAutoDiscover"
            />
            自动加载 AGENTS.md
          </label>
          <button
            class="btn btn-sm btn-primary"
            type="button"
            :disabled="busy"
            title="选择要注入的文件"
            @click="openFilePicker"
          >+ 添加文件</button>
        </div>
      </div>

      <div v-if="instructionEntries.length" class="entry-list">
        <span v-for="entry in instructionEntries" :key="entry" class="entry-chip">
          <code>{{ entry }}</code>
          <button class="chip-remove" type="button" title="移除" @click="removeInstructionFile(entry)">×</button>
        </span>
      </div>
      <p v-else class="muted">尚未手动添加文件（关闭上方开关后 AGENTS.md 不再自动注入）。</p>
      <p class="muted-inline workspace-hint">工作区：{{ instructionWorkspace || '—' }}</p>

      <ul class="plain-list">
        <li v-for="source in instructions" :key="source.id">
          <span class="badge" :class="source.loaded ? 'badge-enabled' : 'badge-disabled'">{{ source.loaded ? '已加载' : '已跳过' }}</span>
          <code>{{ source.kind }}</code>
          <span>{{ source.location }}</span>
          <span class="muted-inline">{{ source.chars }} 字符 / {{ source.tokens }} tokens</span>
          <span v-if="source.skipped_reason" class="muted-inline">{{ source.skipped_reason }}</span>
        </li>
        <li v-if="!instructions.length" class="muted">当前工作区没有生效的指令来源</li>
      </ul>
    </section>

    <InstructionFilePicker
      :show="showFilePicker"
      :start-path="instructionWorkspace"
      :busy="busy"
      @close="showFilePicker = false"
      @select="addInstructionFile"
    />
  </div>
</template>

<style scoped>
.agent-settings {
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

.section { display: flex; flex-direction: column; gap: 10px; }
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
.list-meta { font-size: 13px; color: var(--text-secondary); font-weight: 500; }

.agent-list { display: flex; flex-direction: column; gap: 10px; animation: fadeSlideUp 0.4s var(--ease); }
.agent-card {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  border-radius: var(--radius-lg);
  transition: all var(--duration) var(--ease);
}
.agent-card:hover {
  box-shadow: var(--shadow-md);
  border-color: color-mix(in srgb, var(--primary) 25%, var(--border));
  transform: translateY(-1px);
}
.agent-avatar {
  font-size: 18px;
  font-weight: 800;
  color: var(--primary);
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
.agent-info { flex: 1; min-width: 0; }
.agent-title {
  font-weight: 700;
  font-size: 14px;
  color: var(--text);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.agent-desc { font-size: 12.5px; color: var(--text-secondary); margin-top: 3px; line-height: 1.5; }
.agent-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
.agent-actions { display: flex; gap: 8px; flex-shrink: 0; }

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

.card h3 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.01em;
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-head h3 { margin: 0; }
.section-actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.auto-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text-secondary);
  cursor: pointer;
  user-select: none;
}
.auto-toggle input { cursor: pointer; }
.workspace-hint {
  display: block;
  margin-top: 6px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11.5px;
  word-break: break-all;
}

.entry-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.entry-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 6px 4px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius-pill);
  background: var(--bg-subtle);
  font-size: 12px;
}
.entry-chip code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11.5px;
  color: var(--text-secondary);
}
.chip-remove {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  font-size: 14px;
  line-height: 1;
  transition: all var(--duration) var(--ease);
}
.chip-remove:hover { background: var(--danger-soft); color: var(--danger); }

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

.plain-list { list-style: none; margin: 0; padding: 0; }
.plain-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--border-subtle);
  font-size: 13px;
  flex-wrap: wrap;
}
.plain-list li:last-child { border-bottom: 0; }
.plain-list code {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--primary);
  background: var(--bg-subtle);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
}
.spacer { flex: 1; }
.muted { color: var(--text-secondary); font-size: 12px; margin: 8px 0 0; line-height: 1.6; }
.muted-inline { color: var(--text-secondary); font-size: 12px; }
.form-error { color: var(--danger); font-size: 12.5px; }

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
  gap: 10px;
  margin-top: 14px;
}
.field { display: flex; flex-direction: column; gap: 5px; font-size: 12px; font-weight: 500; color: var(--text-secondary); }
.form-footer { display: flex; align-items: center; gap: 10px; margin-top: 14px; flex-wrap: wrap; }

.config-group { margin-bottom: 14px; }
.config-group h4 {
  margin: 8px 0;
  font-size: 11px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 700;
}
.config-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px 16px; }
.config-row { display: flex; flex-direction: column; gap: 5px; font-size: 12px; font-weight: 500; color: var(--text-secondary); }
.config-row input[type='checkbox'] { align-self: flex-start; }

.rule-row {
  display: grid;
  grid-template-columns: 1fr 1fr 110px auto auto auto;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.rule-row-new { margin-top: 4px; padding-top: 12px; border-top: 1px dashed var(--border); }

@media (max-width: 768px) {
  .header-row { flex-direction: column; }
  .session-input { width: 100%; }
  .rule-row { grid-template-columns: 1fr; }
  .agent-actions { margin-left: auto; }
}
</style>
