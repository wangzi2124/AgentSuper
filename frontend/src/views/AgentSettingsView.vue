<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { fetchWithTimeout } from '../api/fetch'

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

const form = ref({
  name: '',
  description: '',
  mode: 'subagent',
  tier: 'medium',
  model: '',
  tools: '',
  permission: '',
  steps: 6,
  color: '',
  hidden: false,
  disabled: false,
})
const editing = ref('')

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
      request<{ sources: InstructionSource[]; enabled: boolean }>(
        `/api/instructions${query()}`,
      ),
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

function resetForm() {
  editing.value = ''
  form.value = {
    name: '',
    description: '',
    mode: 'subagent',
    tier: 'medium',
    model: '',
    tools: '',
    permission: '',
    steps: 6,
    color: '',
    hidden: false,
    disabled: false,
  }
}

function editAgent(agent: AgentInfo) {
  editing.value = agent.name
  form.value = {
    name: agent.name,
    description: agent.description,
    mode: agent.mode,
    tier: agent.tier,
    model: agent.model,
    tools: agent.tools.join(', '),
    permission: Object.entries(agent.permission)
      .map(([key, value]) => `${key}: ${value}`)
      .join('\n'),
    steps: agent.steps,
    color: agent.color,
    hidden: agent.hidden,
    disabled: agent.disabled,
  }
}

function parsePermission(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const [key, value] = line.split(':')
    if (key && value && value.trim()) out[key.trim()] = value.trim()
  }
  return out
}

async function saveAgent() {
  busy.value = true
  error.value = ''
  const payload = {
    ...form.value,
    tools: form.value.tools.split(',').map((item) => item.trim()).filter(Boolean),
    permission: parsePermission(form.value.permission),
    scope: sessionId.value ? 'project' : 'global',
  }
  try {
    const url = editing.value
      ? `/api/v2/agents/${encodeURIComponent(editing.value)}${query()}`
      : `/api/v2/agents${query()}`
    await request(url, {
      method: editing.value ? 'PATCH' : 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    resetForm()
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
  <div class="agent-settings">
    <header class="page-head">
      <div>
        <h1>Agent 配置</h1>
        <p>Agent、斜杠命令、策略与项目指令均由服务端提供；此页只读写 API。</p>
      </div>
      <div class="head-actions">
        <input
          v-model="sessionId"
          class="text-input"
          placeholder="会话 ID（可选，项目级）"
          @change="loadAll"
        />
        <button class="btn" :disabled="busy" @click="loadAll">刷新</button>
      </div>
    </header>

    <p v-if="error" class="error">{{ error }}</p>

    <section class="card">
      <h2>Agents <small>{{ scopeLabel }}</small></h2>
      <div class="agent-grid">
        <form class="agent-form" @submit.prevent="saveAgent">
          <label>ID<input v-model="form.name" class="text-input" :disabled="!!editing" required /></label>
          <label>描述<input v-model="form.description" class="text-input" /></label>
          <label>模式
            <select v-model="form.mode" class="text-input">
              <option value="primary">primary</option>
              <option value="subagent">subagent</option>
              <option value="all">all</option>
            </select>
          </label>
          <label>模型<input v-model="form.model" class="text-input" placeholder="provider/model（可空）" /></label>
          <label>工具（逗号分隔，空=全部）<input v-model="form.tools" class="text-input" /></label>
          <label>权限（每行 action: allow|deny）<textarea v-model="form.permission" rows="3" class="text-input" /></label>
          <label>最大轮次<input v-model.number="form.steps" type="number" min="1" class="text-input" /></label>
          <label>颜色<input v-model="form.color" class="text-input" placeholder="#RRGGBB" /></label>
          <div class="checks">
            <label><input v-model="form.hidden" type="checkbox" /> 隐藏</label>
            <label><input v-model="form.disabled" type="checkbox" /> 禁用</label>
          </div>
          <div class="row">
            <button class="btn primary" type="submit" :disabled="busy">{{ editing ? '保存修改' : '新建 Agent' }}</button>
            <button v-if="editing" class="btn" type="button" @click="resetForm">取消</button>
          </div>
        </form>
        <ul class="agent-list">
          <li v-for="agent in agents" :key="agent.name">
            <div class="agent-line">
              <strong>{{ agent.name }}</strong>
              <span class="tag">{{ agent.mode }}</span>
              <span class="tag">{{ agent.source }}</span>
              <span v-if="agent.model" class="tag">{{ agent.model }}</span>
              <span v-if="agent.disabled" class="tag danger">禁用</span>
              <span v-if="agent.hidden" class="tag">隐藏</span>
              <span class="spacer" />
              <button class="link" type="button" @click="editAgent(agent)">编辑</button>
              <button class="link danger" type="button" @click="removeAgent(agent)">删除</button>
            </div>
            <p class="muted">{{ agent.description || '无描述' }}</p>
            <p class="muted">工具：{{ agent.tools.length ? agent.tools.join(', ') : '全部' }}</p>
          </li>
        </ul>
      </div>
    </section>

    <section class="card">
      <h2>斜杠命令</h2>
      <ul class="plain-list">
        <li v-for="command in commands" :key="command.name">
          <code>/{{ command.name }}</code>
          <span>{{ command.description }}</span>
          <span class="tag">{{ command.source }}</span>
          <span v-if="command.agent" class="tag">agent: {{ command.agent }}</span>
          <span v-if="command.model" class="tag">{{ command.model }}</span>
        </li>
        <li v-if="!commands.length" class="muted">未发现命令（写入 .agentsuper/commands/*.md）</li>
      </ul>
    </section>

    <section class="card">
      <h2>策略规则</h2>
      <div v-for="(rule, index) in policyDraft" :key="index" class="rule-row">
        <input v-model="rule.action" class="text-input" placeholder="action" />
        <input v-model="rule.resource" class="text-input" placeholder="resource（通配 *）" />
        <select v-model="rule.effect" class="text-input">
          <option value="deny">deny</option>
          <option value="allow">allow</option>
        </select>
        <button class="link" type="button" @click="moveRule(index, -1)">上移</button>
        <button class="link" type="button" @click="moveRule(index, 1)">下移</button>
        <button class="link danger" type="button" @click="removeRule(index)">删除</button>
      </div>
      <div class="rule-row">
        <input v-model="newRule.action" class="text-input" placeholder="action，如 tool.use / websearch.use" />
        <input v-model="newRule.resource" class="text-input" placeholder="resource，如 web_search 或 *" />
        <select v-model="newRule.effect" class="text-input">
          <option value="deny">deny</option>
          <option value="allow">allow</option>
        </select>
        <button class="btn" type="button" @click="addRule">添加</button>
        <button class="btn primary" type="button" :disabled="busy" @click="saveRules">保存规则</button>
      </div>
      <p class="muted">规则按顺序匹配，最后命中生效；deny 不会被 allow 放宽。审批授权仅对当前回合有效，不提供「允许并记住」。</p>
    </section>

    <section class="card">
      <h2>引用（References） <small>{{ scopeLabel }}</small></h2>
      <ul class="plain-list">
        <li v-for="ref in references" :key="ref.name">
          <strong>{{ ref.name }}</strong>
          <span class="tag">{{ ref.kind }}</span>
          <span class="tag" :class="ref.status === 'error' ? 'danger' : ''">{{ ref.status }}</span>
          <span>{{ ref.description }}</span>
          <span class="spacer" />
          <button v-if="ref.kind === 'git'" class="link" type="button" @click="refreshReference(ref.name)">刷新</button>
          <button class="link danger" type="button" @click="removeReference(ref.name)">删除</button>
        </li>
        <li v-if="!references.length" class="muted">未配置引用</li>
      </ul>
      <form class="agent-form" @submit.prevent="saveReference">
        <label>别名<input v-model="referenceForm.name" class="text-input" required /></label>
        <label>本地路径<input v-model="referenceForm.path" class="text-input" /></label>
        <label>Git 仓库<input v-model="referenceForm.repository" class="text-input" /></label>
        <label>分支<input v-model="referenceForm.branch" class="text-input" /></label>
        <label>描述<input v-model="referenceForm.description" class="text-input" /></label>
        <button class="btn primary" type="submit" :disabled="busy">添加引用</button>
      </form>
    </section>

    <section class="card">
      <h2>Web 搜索</h2>
      <p v-if="websearch" class="muted">
        状态：{{ websearch.enabled ? '启用' : '禁用' }}；选择：{{ websearch.provider }}；
        可用：{{ websearch.available.join(', ') || '无' }}
      </p>
      <p v-if="websearch && Object.keys(websearch.cooldown).length" class="muted">
        冷却中：{{ Object.keys(websearch.cooldown).join(', ') }}
      </p>
    </section>

    <section class="card">
      <h2>运行配置</h2>
      <div v-for="(entries, group) in configSchema" :key="group" class="config-group">
        <h3>{{ group }}</h3>
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
              class="text-input"
            />
            <textarea
              v-else-if="isJsonField(entry)"
              :value="JSON.stringify(configDraft[entry.name])"
              rows="2"
              class="text-input"
              @change="setJsonValue(entry.name, $event)"
            />
            <input v-else v-model="configDraft[entry.name]" class="text-input" />
          </label>
        </div>
      </div>
      <div class="row">
        <button class="btn primary" type="button" :disabled="busy" @click="saveConfig">保存配置</button>
        <button class="btn" type="button" :disabled="busy" @click="rollbackConfig">回滚上次修改</button>
      </div>
      <p class="muted">配置修改即时生效；密钥类字段不会回传明文。</p>
    </section>

    <section class="card">
      <h2>项目指令来源</h2>
      <ul class="plain-list">
        <li v-for="source in instructions" :key="source.id">
          <span class="tag" :class="source.loaded ? '' : 'danger'">{{ source.loaded ? '已加载' : '已跳过' }}</span>
          <code>{{ source.kind }}</code>
          <span>{{ source.location }}</span>
          <span class="muted">{{ source.chars }} 字符 / {{ source.tokens }} tokens</span>
          <span v-if="source.skipped_reason" class="muted">{{ source.skipped_reason }}</span>
        </li>
        <li v-if="!instructions.length" class="muted">当前工作区没有生效的指令来源</li>
      </ul>
    </section>
  </div>
</template>

<style scoped>
.agent-settings { padding: 24px; max-width: 1100px; margin: 0 auto; color: var(--text); }
.page-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.page-head h1 { margin: 0 0 4px; font-size: 20px; }
.page-head p { margin: 0; color: var(--text-secondary); font-size: 13px; }
.head-actions { display: flex; gap: 8px; }
.card { margin-top: 18px; padding: 16px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
.card h2 { margin: 0 0 12px; font-size: 15px; }
.card h2 small { color: var(--text-secondary); font-weight: 400; margin-left: 6px; }
.agent-grid { display: grid; grid-template-columns: minmax(260px, 340px) 1fr; gap: 20px; }
.agent-form { display: flex; flex-direction: column; gap: 8px; }
.agent-form label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-secondary); }
.text-input { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg); color: var(--text); font-size: 13px; }
.checks { display: flex; gap: 16px; font-size: 12px; }
.row { display: flex; gap: 8px; }
.btn { padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px; background: var(--surface); color: var(--text); cursor: pointer; }
.btn.primary { background: var(--primary); color: #fff; border-color: var(--primary); }
.agent-list, .plain-list { list-style: none; margin: 0; padding: 0; }
.agent-list li { padding: 10px 0; border-bottom: 1px solid var(--border); }
.agent-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.spacer { flex: 1; }
.tag { font-size: 11px; padding: 1px 6px; border: 1px solid var(--border); border-radius: 4px; color: var(--text-secondary); }
.tag.danger { color: var(--danger, #d33); border-color: currentColor; }
.muted { color: var(--text-secondary); font-size: 12px; margin: 4px 0 0; }
.link { border: 0; background: none; color: var(--primary); cursor: pointer; font-size: 12px; }
.link.danger { color: var(--danger, #d33); }
.plain-list li { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--border); font-size: 13px; flex-wrap: wrap; }
.config-group { margin-bottom: 12px; }
.config-group h3 { margin: 8px 0; font-size: 12px; color: var(--text-secondary); text-transform: uppercase; }
.config-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 8px 16px; }
.config-row { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-secondary); }
.config-row input[type='checkbox'] { align-self: flex-start; }
.rule-row { display: grid; grid-template-columns: 1fr 1fr 100px auto auto auto; gap: 6px; align-items: center; margin-bottom: 6px; }
.error { color: var(--danger, #d33); }
</style>
