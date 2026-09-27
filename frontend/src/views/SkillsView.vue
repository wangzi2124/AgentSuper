<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useSkillStore } from '../stores/skills'
import type { Skill } from '../types'
import DirPickerModal from '../components/DirPickerModal.vue'

const skillStore = useSkillStore()

const showDirPicker = ref(false)
const busy = ref(false)
const error = ref('')
const notice = ref('')
const search = ref('')

// ── 新建 / 编辑弹窗 ──
const editor = ref(false)
const editing = ref<string | null>(null)   // null = 新建
const form = ref({ name: '', description: '', content: '', enabled: true, disable_model_invocation: false })
const saving = ref(false)
const confirmDelete = ref<string | null>(null)

const filtered = computed(() => {
  const q = search.value.toLowerCase().trim()
  if (!q) return skillStore.skills
  return skillStore.skills.filter(
    (s) => s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
  )
})

const enabledCount = computed(() => skillStore.skills.filter((s) => s.enabled).length)
/** 可挂载为 load_skill_* 工具的数量（仅手动 的技能不算） */
const toolCount = computed(
  () => skillStore.skills.filter((s) => s.enabled && !s.disable_model_invocation).length,
)
const managedCount = computed(() => skillStore.skills.filter((s) => s.managed).length)

/** 技能挂载为 load_skill_<name> 工具（后端 app/agent/tools.py:create_skill_tools 同款命名） */
function toolNameOf(name: string): string {
  return 'load_skill_' + name.replace(/-/g, '_').replace(/ /g, '_')
}

function isManaged(s: Skill): boolean {
  return s.managed !== false
}

async function reload() {
  busy.value = true
  error.value = ''
  try {
    await skillStore.refresh()
  } catch (err: any) {
    error.value = err?.message || '加载技能失败'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  if (!skillStore.initialized) void reload()
})

// ── 外部技能源 ──

async function handleSelectDir(path: string) {
  showDirPicker.value = false
  busy.value = true
  error.value = ''
  try {
    await skillStore.addDirectory(path)
    notice.value = '已追加外部技能源（受管库中的技能不受影响）'
  } catch (err: any) {
    error.value = err?.message || '添加技能目录失败'
  } finally {
    busy.value = false
  }
}

async function handleRemoveDir(path: string) {
  busy.value = true
  error.value = ''
  try {
    await skillStore.removeDirectory(path)
    notice.value = '已移除该外部技能源'
  } catch (err: any) {
    error.value = err?.message || '移除技能目录失败'
  } finally {
    busy.value = false
  }
}

// ── 启停 ──

async function handleToggle(skill: Skill) {
  error.value = ''
  try {
    await skillStore.toggle(skill.name, !skill.enabled)
  } catch (err: any) {
    error.value = err?.message || '切换技能失败'
  }
}

// ── 新建 / 编辑 / 删除 ──

function openCreate() {
  editing.value = null
  form.value = { name: '', description: '', content: '', enabled: true, disable_model_invocation: false }
  editor.value = true
}

async function openEdit(skill: Skill) {
  error.value = ''
  editor.value = true
  editing.value = skill.name
  form.value = {
    name: skill.name,
    description: skill.description || '',
    content: '',
    enabled: skill.enabled,
    disable_model_invocation: skill.disable_model_invocation,
  }
  try {
    const d = await skillStore.detail(skill.name)
    form.value.content = d.content || ''
  } catch (err: any) {
    error.value = err?.message || '读取技能内容失败'
    editor.value = false
  }
}

async function submitForm() {
  const f = form.value
  if (!f.name.trim()) {
    error.value = '技能名不能为空'
    return
  }
  saving.value = true
  error.value = ''
  try {
    if (editing.value) {
      await skillStore.update(editing.value, {
        description: f.description,
        content: f.content,
        enabled: f.enabled,
        disable_model_invocation: f.disable_model_invocation,
      })
      notice.value = `已保存「${editing.value}」`
    } else {
      await skillStore.create({
        name: f.name.trim(),
        description: f.description,
        content: f.content,
        enabled: f.enabled,
        disable_model_invocation: f.disable_model_invocation,
      })
      notice.value = `已创建「${f.name.trim()}」并挂载为 ${toolNameOf(f.name.trim())}`
    }
    editor.value = false
  } catch (err: any) {
    error.value = err?.message || (editing.value ? '保存技能失败' : '创建技能失败')
  } finally {
    saving.value = false
  }
}

async function handleDelete() {
  const name = confirmDelete.value
  if (!name) return
  saving.value = true
  error.value = ''
  try {
    await skillStore.remove(name)
    notice.value = `已删除「${name}」`
  } catch (err: any) {
    error.value = err?.message || '删除技能失败'
  } finally {
    saving.value = false
    confirmDelete.value = null
  }
}
</script>

<template>
  <div class="page-header">
    <h2>技能（Skills）</h2>
    <p>
      技能是带说明的 Markdown 说明书。启用的技能挂载为
      <code>load_skill_*</code> 工具，模型按需加载其完整内容。
    </p>
  </div>

  <div class="page-content skills-wrap">
    <!-- 统计 + 操作 -->
    <div class="card stat-card">
      <div class="stat">
        <div class="stat-num">{{ skillStore.skills.length }}</div>
        <div class="stat-label">技能总数</div>
      </div>
      <div class="stat">
        <div class="stat-num">{{ enabledCount }}</div>
        <div class="stat-label">已启用</div>
      </div>
      <div class="stat">
        <div class="stat-num">{{ toolCount }}</div>
        <div class="stat-label">可被模型调用</div>
      </div>
      <div class="stat">
        <div class="stat-num">{{ managedCount }}</div>
        <div class="stat-label">受管（可编辑）</div>
      </div>
      <div class="stat-actions">
        <input v-model="search" class="ctrl search" placeholder="搜索技能…" />
        <button class="btn" :disabled="busy || skillStore.loading" @click="reload">
          {{ busy || skillStore.loading ? '加载中…' : '重新扫描' }}
        </button>
        <button class="btn btn-primary" @click="openCreate">+ 新建技能</button>
      </div>
    </div>

    <!-- 技能源 -->
    <div class="card src-card">
      <div class="src-head">
        <div class="src-title">技能源</div>
        <div class="src-hint">
          受管库由系统自动创建，<b>新建的技能保存在这里</b>；外部源用于引入已有的技能仓库，两者可并存，同名以受管库优先。
        </div>
      </div>
      <div class="src-row">
        <span class="tag tag-managed">受管库</span>
        <span class="src-path" :title="skillStore.managedDir">{{ skillStore.managedDir || '加载中…' }}</span>
      </div>
      <div v-for="d in skillStore.extraDirs" :key="d" class="src-row">
        <span class="tag tag-ext">外部源</span>
        <span class="src-path" :title="d">{{ d }}</span>
        <button class="btn btn-sm btn-danger" :disabled="busy" @click="handleRemoveDir(d)">移除</button>
      </div>
      <div class="src-actions">
        <button class="btn" :disabled="busy" @click="showDirPicker = true">+ 追加外部技能文件夹</button>
      </div>
    </div>

    <p v-if="error" class="msg msg-error">{{ error }}</p>
    <p v-if="notice && !error" class="msg msg-ok">{{ notice }}</p>

    <!-- 技能列表 -->
    <div v-if="skillStore.loading && !skillStore.skills.length" class="loading-wrap">
      <span class="spinner"></span>
    </div>

    <div v-else-if="!skillStore.skills.length" class="empty-state">
      <div class="icon">⚡</div>
      <p>还没有技能</p>
      <p class="empty-sub">点「+ 新建技能」写一个 Markdown 说明书，或「追加外部技能文件夹」引入已有技能仓库。</p>
      <button class="btn btn-primary" @click="openCreate">+ 新建技能</button>
    </div>

    <div v-else-if="!filtered.length" class="empty-state">
      <p>没有匹配「{{ search }}」的技能</p>
    </div>

    <div v-else class="skill-list">
      <div class="list-meta">{{ filtered.length }} / {{ skillStore.skills.length }} 个技能</div>
      <div v-for="s in filtered" :key="s.name" class="card skill-card">
        <div class="skill-icon">{{ s.disable_model_invocation ? '🔒' : '⚡' }}</div>
        <div class="skill-info">
          <div class="skill-name">
            <span class="skill-title">{{ s.name }}</span>
            <span class="badge mono">{{ toolNameOf(s.name) }}</span>
            <span v-if="s.disable_model_invocation" class="badge warn" title="仅由用户显式触发，不暴露为模型工具">仅手动</span>
            <span v-if="!isManaged(s)" class="badge" title="来自外部技能源，只读">只读</span>
          </div>
          <div class="skill-desc">{{ s.description || '（无描述）' }}</div>
          <div class="skill-path" :title="s.path">{{ s.path }}</div>
        </div>
        <span class="badge" :class="s.enabled ? 'ok' : 'off'">
          {{ s.enabled ? '已启用' : '已禁用' }}
        </span>
        <div class="skill-actions">
          <button
            class="btn btn-sm"
            :class="s.enabled ? 'btn-danger' : 'btn-primary'"
            :disabled="s.disable_model_invocation"
            @click="handleToggle(s)"
          >{{ s.enabled ? '禁用' : '启用' }}</button>
          <template v-if="isManaged(s)">
            <button class="btn btn-sm" @click="openEdit(s)">编辑</button>
            <button class="btn btn-sm btn-danger" @click="confirmDelete = s.name">删除</button>
          </template>
        </div>
      </div>
    </div>

    <DirPickerModal :show="showDirPicker" @close="showDirPicker = false" @select="handleSelectDir" />

    <!-- 新建 / 编辑弹窗 -->
    <div v-if="editor" class="modal-mask" @click.self="editor = false">
      <div class="modal">
        <div class="modal-head">
          <h3>{{ editing ? `编辑技能：${editing}` : '新建技能' }}</h3>
          <button class="modal-x" @click="editor = false">×</button>
        </div>
        <div class="modal-body">
          <label class="field">
            <span class="field-label">技能名 <em>*</em></span>
            <input
              v-model="form.name"
              class="ctrl"
              :disabled="!!editing"
              placeholder="my-skill（字母/数字/连字符，将生成 load_skill_my_skill 工具）"
            />
            <span v-if="!editing && form.name" class="field-hint">
              工具名：<code>{{ toolNameOf(form.name) }}</code>
            </span>
          </label>
          <label class="field">
            <span class="field-label">描述</span>
            <input v-model="form.description" class="ctrl" placeholder="一句话说明这个技能做什么（会进工具 schema，≤200 字符）" />
          </label>
          <label class="field">
            <span class="field-label">正文（Markdown）</span>
            <textarea
              v-model="form.content"
              class="ctrl mono-area"
              rows="14"
              placeholder="# 技能标题&#10;&#10;模型加载本技能后读到的完整说明书。&#10;写清步骤、约束与产出格式。"
            ></textarea>
            <span class="field-hint">模型调用工具时读到的就是这段正文，写清步骤与产出格式。</span>
          </label>
          <div class="field checks">
            <label class="chk">
              <input v-model="form.enabled" type="checkbox" />
              <span>启用（挂载为 load_skill_* 工具）</span>
            </label>
            <label class="chk">
              <input v-model="form.disable_model_invocation" type="checkbox" />
              <span>仅手动触发（<code>disable-model-invocation</code>，不暴露给模型）</span>
            </label>
          </div>
          <p v-if="error" class="msg msg-error">{{ error }}</p>
        </div>
        <div class="modal-foot">
          <button class="btn" :disabled="saving" @click="editor = false">取消</button>
          <button class="btn btn-primary" :disabled="saving" @click="submitForm">
            {{ saving ? '保存中…' : editing ? '保存' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 删除确认 -->
    <div v-if="confirmDelete" class="modal-mask" @click.self="confirmDelete = null">
      <div class="modal modal-sm">
        <div class="modal-head"><h3>删除技能</h3></div>
        <div class="modal-body">
          <p>确定删除「<b>{{ confirmDelete }}</b>」？将从受管库中移除其 <code>SKILL.md</code>，此操作不可撤销。</p>
        </div>
        <div class="modal-foot">
          <button class="btn" :disabled="saving" @click="confirmDelete = null">取消</button>
          <button class="btn btn-danger" :disabled="saving" @click="handleDelete">
            {{ saving ? '删除中…' : '删除' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.skills-wrap { display: flex; flex-direction: column; gap: 16px; }
.loading-wrap { display: flex; justify-content: center; padding: 48px; }

.ctrl {
  padding: 9px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  outline: none;
  transition: all var(--duration) var(--ease);
  width: 100%;
  font-family: inherit;
}
.ctrl:focus { border-color: var(--primary); box-shadow: 0 0 0 3px var(--primary-glow); }
.ctrl:disabled { opacity: 0.55; cursor: not-allowed; }
.search { min-width: 160px; }
.mono-area { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12.5px; line-height: 1.6; resize: vertical; }

.msg { font-size: 12.5px; margin: 0; padding: 8px 12px; border-radius: var(--radius); }
.msg-error { color: var(--danger); background: color-mix(in srgb, var(--danger) 10%, transparent); }
.msg-ok { color: var(--success, #16a34a); background: color-mix(in srgb, var(--success, #16a34a) 10%, transparent); }

/* ── 统计条 ── */
.stat-card { display: flex; align-items: center; gap: 28px; flex-wrap: wrap; }
.stat { text-align: center; min-width: 62px; }
.stat-num { font-size: 22px; font-weight: 800; color: var(--primary); line-height: 1.1; font-variant-numeric: tabular-nums; }
.stat-label { font-size: 11px; color: var(--text-secondary); margin-top: 2px; }
.stat-actions { margin-left: auto; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }

/* ── 技能源 ── */
.src-card { display: flex; flex-direction: column; gap: 8px; }
.src-head { margin-bottom: 2px; }
.src-title { font-weight: 700; font-size: 13px; color: var(--text); }
.src-hint { font-size: 12px; color: var(--text-secondary); margin-top: 3px; line-height: 1.55; }
.src-row { display: flex; align-items: center; gap: 8px; }
.src-path {
  flex: 1; min-width: 0; font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12px;
  color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.src-actions { margin-top: 2px; }
.tag {
  font-size: 10px; font-weight: 700; padding: 3px 9px; border-radius: var(--radius-pill);
  flex-shrink: 0; letter-spacing: 0.03em; white-space: nowrap;
}
.tag-managed { background: color-mix(in srgb, var(--primary) 15%, transparent); color: var(--primary); }
.tag-ext { background: color-mix(in srgb, #eab308 18%, transparent); color: #a16207; }

/* ── 技能列表 ── */
.skill-list { display: flex; flex-direction: column; gap: 10px; animation: fadeSlideUp 0.4s var(--ease); }
.list-meta { font-size: 12.5px; color: var(--text-secondary); font-weight: 500; }
.skill-card { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; transition: all var(--duration) var(--ease); }
.skill-card:hover {
  box-shadow: var(--shadow-md);
  border-color: color-mix(in srgb, var(--primary) 25%, var(--border));
  transform: translateY(-1px);
}
.skill-icon {
  font-size: 20px; width: 44px; height: 44px; border-radius: var(--radius);
  display: flex; align-items: center; justify-content: center; flex-shrink: 0;
  background: var(--bg-subtle); border: 1px solid var(--border-subtle);
}
.skill-info { flex: 1; min-width: 0; }
.skill-name { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.skill-title { font-weight: 700; font-size: 14px; color: var(--text); }
.skill-desc { font-size: 12.5px; color: var(--text-secondary); margin-top: 3px; line-height: 1.5; }
.skill-path {
  font-size: 11px; color: var(--text-muted); margin-top: 4px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.skill-actions { display: flex; gap: 6px; flex-shrink: 0; }
.btn-sm { padding: 6px 11px; font-size: 12px; }

.badge {
  font-size: 10.5px; font-weight: 700; padding: 2px 8px;
  border-radius: var(--radius-pill); white-space: nowrap; letter-spacing: 0.02em;
}
.badge.mono { font-family: 'JetBrains Mono', Consolas, monospace; background: var(--bg-subtle); color: var(--text-secondary); }
.badge.warn { background: color-mix(in srgb, #eab308 20%, transparent); color: #a16207; }
.badge.ok { background: color-mix(in srgb, #16a34a 15%, transparent); color: #16a34a; }
.badge.off { background: var(--bg-subtle); color: var(--text-muted); }

/* ── 弹窗 ── */
.modal-mask {
  position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5);
  display: flex; align-items: center; justify-content: center; z-index: 1000; padding: 20px;
  backdrop-filter: blur(3px);
}
.modal {
  background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-lg);
  width: 100%; max-width: 680px; max-height: 88vh; display: flex; flex-direction: column;
  box-shadow: var(--shadow-lg); animation: fadeSlideUp 0.22s var(--ease);
}
.modal-sm { max-width: 420px; }
.modal-head {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px; border-bottom: 1px solid var(--border-subtle);
}
.modal-head h3 { margin: 0; font-size: 15px; font-weight: 700; color: var(--text); }
.modal-x { background: none; border: none; font-size: 22px; line-height: 1; color: var(--text-muted); cursor: pointer; padding: 0 4px; }
.modal-x:hover { color: var(--text); }
.modal-body { padding: 18px 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }
.modal-body p { margin: 0; font-size: 13px; line-height: 1.6; color: var(--text-secondary); }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; padding: 14px 20px; border-top: 1px solid var(--border-subtle); }

.field { display: flex; flex-direction: column; gap: 6px; }
.field-label { font-size: 12px; font-weight: 600; color: var(--text-secondary); }
.field-label em { color: var(--danger); font-style: normal; }
.field-hint { font-size: 11.5px; color: var(--text-muted); }
.field-hint code { font-family: 'JetBrains Mono', Consolas, monospace; color: var(--primary); }
.checks { flex-direction: row; gap: 18px; flex-wrap: wrap; }
.chk { display: flex; align-items: center; gap: 7px; font-size: 12.5px; color: var(--text-secondary); cursor: pointer; }
.chk code { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11px; }

/* ── 空态 ── */
.empty-state { text-align: center; padding: 44px 20px; color: var(--text-secondary); }
.empty-state .icon { font-size: 40px; margin-bottom: 10px; }
.empty-state p { margin: 4px 0; }
.empty-sub { font-size: 12.5px; color: var(--text-muted); max-width: 460px; margin: 6px auto 14px !important; line-height: 1.6; }

@media (max-width: 600px) {
  .stat-card { gap: 16px; }
  .stat { min-width: 52px; }
  .stat-actions { margin-left: 0; width: 100%; }
  .search { flex: 1; }
  .skill-card { gap: 10px; }
  .skill-actions { margin-left: auto; }
  .modal { max-height: 92vh; }
}
</style>
