<script setup lang="ts">
import { ref, watch } from 'vue'

interface AgentSource {
  name: string
  description: string
  mode: string
  tier: string
  model: string
  tools: string[]
  permission: Record<string, string>
  steps: number
  color: string
  hidden: boolean
  disabled: boolean
}

const props = defineProps<{
  show: boolean
  agent?: AgentSource | null
  busy?: boolean
  scopeLabel?: string
}>()

const emit = defineEmits<{
  close: []
  submit: [
    payload: {
      name: string
      description: string
      mode: string
      tier: string
      model: string
      tools: string[]
      permission: Record<string, string>
      steps: number
      color: string
      hidden: boolean
      disabled: boolean
    },
  ]
}>()

function defaults() {
  return {
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

const form = ref(defaults())

watch(
  () => props.show,
  (visible) => {
    if (!visible) return
    const agent = props.agent
    if (agent) {
      form.value = {
        name: agent.name,
        description: agent.description || '',
        mode: agent.mode || 'subagent',
        tier: agent.tier || 'medium',
        model: agent.model || '',
        tools: (agent.tools || []).join(', '),
        permission: Object.entries(agent.permission || {})
          .map(([key, value]) => `${key}: ${value}`)
          .join('\n'),
        steps: agent.steps || 6,
        color: agent.color || '',
        hidden: !!agent.hidden,
        disabled: !!agent.disabled,
      }
    } else {
      form.value = defaults()
    }
  },
)

function parsePermission(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split('\n')) {
    const idx = line.indexOf(':')
    if (idx <= 0) continue
    const key = line.slice(0, idx).trim()
    const value = line.slice(idx + 1).trim()
    if (key && value) out[key] = value
  }
  return out
}

function submit() {
  if (!form.value.name.trim()) return
  emit('submit', {
    name: form.value.name.trim(),
    description: form.value.description.trim(),
    mode: form.value.mode,
    tier: form.value.tier,
    model: form.value.model.trim(),
    tools: form.value.tools
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean),
    permission: parsePermission(form.value.permission),
    steps: Number(form.value.steps) || 6,
    color: form.value.color.trim(),
    hidden: form.value.hidden,
    disabled: form.value.disabled,
  })
}
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="agent-modal-overlay" @click.self="emit('close')">
      <div class="agent-modal" role="dialog" aria-modal="true">
        <div class="agent-modal-header">
          <div>
            <span class="agent-modal-title">{{ agent ? '编辑 Agent' : '新增 Agent' }}</span>
            <span v-if="scopeLabel" class="agent-modal-scope">{{ scopeLabel }}</span>
          </div>
          <button class="agent-modal-close" type="button" title="关闭" @click="emit('close')">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6L6 18M6 6l12 12" /></svg>
          </button>
        </div>

        <form class="agent-modal-body" @submit.prevent="submit">
          <label>ID<input v-model="form.name" class="ctrl" :disabled="!!agent" required /></label>
          <label>描述<input v-model="form.description" class="ctrl" /></label>
          <label>模式
            <select v-model="form.mode" class="ctrl">
              <option value="primary">primary</option>
              <option value="subagent">subagent</option>
              <option value="all">all</option>
            </select>
          </label>
          <label>模型<input v-model="form.model" class="ctrl" placeholder="provider/model（可空）" /></label>
          <label>工具（逗号分隔，空=全部）<input v-model="form.tools" class="ctrl" /></label>
          <label>权限（每行 action: allow|deny）<textarea v-model="form.permission" rows="3" class="ctrl" /></label>
          <label>最大轮次<input v-model.number="form.steps" type="number" min="1" class="ctrl" /></label>
          <label>颜色<input v-model="form.color" class="ctrl" placeholder="#RRGGBB" /></label>
          <div class="checks">
            <label><input v-model="form.hidden" type="checkbox" /> 隐藏</label>
            <label><input v-model="form.disabled" type="checkbox" /> 禁用</label>
          </div>

          <div class="agent-modal-footer">
            <button class="btn" type="button" @click="emit('close')">取消</button>
            <button class="btn btn-primary" type="submit" :disabled="busy">{{ agent ? '保存修改' : '创建 Agent' }}</button>
          </div>
        </form>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.agent-modal-overlay {
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
.agent-modal {
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
.agent-modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 18px 20px;
  border-bottom: 1px solid var(--border);
}
.agent-modal-title { font-size: 15px; font-weight: 700; letter-spacing: -0.01em; }
.agent-modal-scope { margin-left: 8px; font-size: 12px; color: var(--text-secondary); }
.agent-modal-close {
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
.agent-modal-close:hover { background: var(--bg-subtle); color: var(--text); }
.agent-modal-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 20px;
  overflow-y: auto;
}
.agent-modal-body label {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
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
.checks { display: flex; gap: 16px; font-size: 12px; }
.checks label { flex-direction: row; align-items: center; gap: 6px; }
.agent-modal-footer {
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
