<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useMultiAgentStore } from '../stores/multiAgent'
import { useAuthStore } from '../stores/auth'
import { deleteConversation as apiDelete } from '../api/sessions'
import type { ConversationMeta } from '../api/sessions'
import { compactSession as apiV2Compact, forkSession as apiV2Fork } from '../api/v2'
import { useV2SessionStore } from '../v2/sessionStore'
import { estimateTokens } from '../api/models'
import { usePermissionStore } from '../stores/permission'
import DirPickerModal from './DirPickerModal.vue'

function fmtTokens(n?: number) {
  if (!n) return ''
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}k` : `${n}`
}
function fmtCost(n?: number) {
  if (!n) return ''
  return n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`
}
function fmtTime(s?: string) {
  if (!s) return ''
  try { return new Date(s).toLocaleString() } catch { return s }
}

const router = useRouter()
const agent = useMultiAgentStore()
const auth = useAuthStore()
const perm = usePermissionStore()
const v2 = useV2SessionStore()
const searchQuery = ref('')
const editingId = ref<string | null>(null)
const editingTitle = ref('')
const showDirPicker = ref(false)
const wsBusy = ref(false)
const wsError = ref('')
const collapsedGroups = ref<Record<string, boolean>>({})
// [ctx-strip xxx] xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
// [perf] xxxxxxxxx mouseenter xxxxxx messages xxx POST x?/api/models/estimate-tokensx?
// xxxxxxxxx?= N xxxxxxxxxxxxxxxxxx 250ms xxx + xxxx?xxxxxxxxx + xxxxxxxxx?
const hoveredId = ref<string | null>(null)
const ctxEst = ref<{ tokens: number; chars: number; method: string } | null>(null)
const CTX_EST_DEBOUNCE_MS = 250
let ctxHoverTimer: ReturnType<typeof setTimeout> | null = null
let ctxEstInFlight: Promise<{ tokens: number; chars: number; method: string }> | null = null
let ctxEstKey = ''

function ctxEstCacheKey(): string {
  const ms = agent.messages
  // xxxxxxx?+ xxxxxx + xxxxxxxxxxxxxxxxxxxxxxxx
  let sig = `${agent.conversationId}|${ms.length}|`
  for (const m of ms) sig += `${(m.content || '').length},`
  const last = ms.length ? (ms[ms.length - 1].content || '') : ''
  return sig + last.slice(-64)
}

async function loadCtxEst() {
  if (!agent.messages.length) { ctxEst.value = null; return }
  const key = ctxEstCacheKey()
  if (key === ctxEstKey && ctxEst.value) return
  // xxxxxxxxxxxxxxxxxxx?Promisexxxxxxxxxxxxxxxxxxxx
  if (key === ctxEstKey && ctxEstInFlight) {
    try { ctxEst.value = await ctxEstInFlight } catch { /* xxxxxx */ }
    return
  }
  ctxEstKey = key
  ctxEstInFlight = estimateTokens({
    messages: agent.messages.map(m => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content || '' })),
  })
  try {
    ctxEst.value = await ctxEstInFlight
  } catch { /* xxxxxx */ }
  finally { ctxEstInFlight = null }
}
function ctxLimitFor(c: ConversationMeta): number {
  const m = agent.models.find(mm => mm.id === (c.model?.id || agent.selectedModel))
  return m?.context_length || 0
}
function ctxPctFor(c: ConversationMeta): number {
  const lim = ctxLimitFor(c)
  if (!lim || !ctxEst.value) return 0
  return Math.min(100, Math.round((ctxEst.value.tokens / lim) * 100))
}
function showUsage(c: ConversationMeta) {
  hoveredId.value = c.id
  if (agent.conversationId !== c.id) return
  // xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  if (ctxHoverTimer) clearTimeout(ctxHoverTimer)
  ctxHoverTimer = setTimeout(() => { ctxHoverTimer = null; void loadCtxEst() }, CTX_EST_DEBOUNCE_MS)
}
function hideUsage() {
  hoveredId.value = null
  if (ctxHoverTimer) { clearTimeout(ctxHoverTimer); ctxHoverTimer = null }
}

onBeforeUnmount(() => { if (ctxHoverTimer) clearTimeout(ctxHoverTimer) })

onMounted(() => {
  // xxxxxxxxxxxxxxxxxxxxxxxxx?xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  if (auth.enabled && !auth.isLoggedIn) return
  agent.loadConversations()
  perm.loadWorkspaces()
})

function normPath(path?: string | null): string {
  return String(path || '').replace(/\\/g, '/').replace(/\/+$/, '').toLowerCase()
}
function baseName(path: string): string {
  const parts = String(path || '').split(/[\\/]+/).filter(Boolean)
  return parts[parts.length - 1] || path
}
function isUnder(candidate: string, root: string): boolean {
  const c = normPath(candidate)
  const r = normPath(root)
  return !!c && !!r && (c === r || c.startsWith(r + '/'))
}
// xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?nullx?
function workspaceRootFor(directory?: string): string | null {
  if (!directory) return null
  let best: string | null = null
  for (const w of perm.workspaces) {
    if (isUnder(directory, w) && (!best || normPath(w).length > normPath(best).length)) best = w
  }
  return best
}

interface WorkspaceGroup {
  key: string
  label: string
  title: string
  root: string | null
  items: ConversationMeta[]
  updated: number
}

// xxxxxx + xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?
const groups = computed<WorkspaceGroup[]>(() => {
  const q = searchQuery.value.trim().toLowerCase()
  const active = agent.activeDirectory
  let list = agent.conversations.filter(c => !q || c.title.toLowerCase().includes(q))
  if (active) list = list.filter(c => isUnder(c.directory || '', active))

  const byRoot = new Map<string, ConversationMeta[]>()
  const temp: ConversationMeta[] = []
  for (const c of list) {
    const root = workspaceRootFor(c.directory)
    if (!root) { temp.push(c); continue }
    const key = normPath(root)
    const items = byRoot.get(key)
    if (items) items.push(c)
    else byRoot.set(key, [c])
  }

  const roots = active
    ? [active]
    : [...perm.workspaces].sort((a, b) => baseName(a).localeCompare(baseName(b)))

  const result: WorkspaceGroup[] = roots.map(root => {
    const items = byRoot.get(normPath(root)) || []
    let updated = 0
    for (const c of items) {
      const ts = Date.parse(c.updated_at) || 0
      if (ts > updated) updated = ts
    }
    return { key: normPath(root), label: baseName(root), title: root, root, items, updated }
  })

  if (temp.length) {
    let updated = 0
    for (const c of temp) {
      const ts = Date.parse(c.updated_at) || 0
      if (ts > updated) updated = ts
    }
    result.push({
      key: '__temp__',
      label: '临时会话',
      title: '未绑定工作目录的会话',
      root: null,
      items: temp,
      updated,
    })
  }

  for (const g of result) g.items.sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))
  return result.sort((a, b) => b.updated - a.updated)
})

function toggleGroup(key: string) {
  collapsedGroups.value = { ...collapsedGroups.value, [key]: !collapsedGroups.value[key] }
}

function isWorkspaceRoot(root: string | null): boolean {
  return !!root && perm.workspaces.some(w => normPath(w) === normPath(root))
}

// xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
function goChat() {
  if (router.currentRoute.value.name !== 'MultiAgent') router.push({ name: 'MultiAgent' })
}

function selectAll() {
  agent.setActiveDirectory(null)
  goChat()
}

function selectWorkspace(root: string | null) {
  if (!root) return
  agent.setActiveDirectory(root)
  goChat()
}

function newSessionIn(event: Event, root: string | null) {
  event.stopPropagation()
  if (!root) return
  agent.newChat(root)
  goChat()
}

async function handleDirPick(path: string) {
  showDirPicker.value = false
  wsBusy.value = true
  wsError.value = ''
  try {
    await perm.addWorkspace(path)
    selectWorkspace(path)
  } catch (e: any) {
    wsError.value = String(e?.message || '添加目录失败').replace(/^Add workspace error:\s*/, '')
  } finally {
    wsBusy.value = false
  }
}

async function removeWorkspace(event: Event, root: string | null) {
  event.stopPropagation()
  if (!root) return
  wsBusy.value = true
  wsError.value = ''
  try {
    await perm.removeWorkspace(root)
    if (agent.activeDirectory && isUnder(agent.activeDirectory, root)) agent.setActiveDirectory(null)
  } catch (e: any) {
    wsError.value = String(e?.message || '添加目录失败')
  } finally {
    wsBusy.value = false
  }
}

function selectConversation(input: ConversationMeta | string) {
  const c = typeof input === 'string' ? agent.conversations.find(x => x.id === input) : input
  // xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx?
  if (c && agent.activeDirectory && !isUnder(c.directory || '', agent.activeDirectory)) {
    agent.setActiveDirectory(null)
  }
  const id = typeof input === 'string' ? input : input.id
  agent.loadConversation(id, false)
  router.push({ name: 'MultiAgentConversation', params: { id } })
}
function startRename(c: ConversationMeta) { editingId.value = c.id; editingTitle.value = c.title }
function saveRename() { if (editingId.value && editingTitle.value.trim()) { agent.renameConversation(editingId.value, editingTitle.value.trim()) }; editingId.value = null }
function cancelRename() { editingId.value = null }
function handleDelete(e: Event, id: string) { e.stopPropagation(); if (agent.conversationId === id) { agent.newChat(); router.push({ name: 'MultiAgent' }) }; apiDelete(id).then(() => agent.loadConversations()) }

async function handleFork(e: Event, id: string) {
  e.stopPropagation()
  try {
    const info = await apiV2Fork(id)
    v2.setInfo(info.id, info)
    void agent.loadConversations()
    await router.push({ name: 'MultiAgentConversation', params: { id: info.id } })
  } catch (err) {
    agent.setNotice(String(err instanceof Error ? err.message : err))
  }
}
async function handleCompact(e: Event, id: string) {
  e.stopPropagation()
  const runtime = v2.getRuntime(id)
  if (runtime?.status.type === 'busy' || runtime?.status.type === 'retry') {
    agent.setNotice('会话运行中，请先停止后再压缩')
    return
  }
  try {
    await apiV2Compact(id)
    // 压缩完成事件会触发 sessionRuntime hydration 同步时间线。
  } catch (err) {
    agent.setNotice(String(err instanceof Error ? err.message : err))
  }
}
const statusOf = ref<Record<string, string>>({})
async function handleStatus(e: Event, c: ConversationMeta) {
  e.stopPropagation()
  const st = await agent.fetchSessionStatus(c.id)
  statusOf.value = { ...statusOf.value, [c.id]: st?.status || 'unknown' }
}
</script>

<template>
  <div class="chat-history">
    <div class="history-header">
      <span class="history-title">工作目录</span>
      <button class="ws-add-btn" :disabled="wsBusy" title="添加工作目录" @click="showDirPicker = true">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 3.2v9.6M3.2 8h9.6"/></svg>
      </button>
    </div>
    <div class="search-box">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
      <input v-model="searchQuery" placeholder="搜索会话..." />
    </div>
    <div class="history-list">
      <div class="workspace-row all-row" :class="{ active: !agent.activeDirectory }" title="显示全部会话" @click="selectAll">
        <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><circle cx="8" cy="8" r="6"/><path d="M2 8h12M8 2c1.7 1.8 2.6 3.8 2.6 6S9.7 12.2 8 14C6.3 12.2 5.4 10.2 5.4 8S6.3 3.8 8 2Z"/></svg>
        <span class="workspace-name">全部会话</span>
        <span class="workspace-count">{{ agent.conversations.length }}</span>
      </div>

      <p v-if="wsError" class="ws-error">{{ wsError }}</p>
      <div v-if="groups.length === 0" class="empty-hint">{{ searchQuery ? '无匹配结果' : '暂无工作目录或会话' }}</div>

      <div v-for="g in groups" :key="g.key" class="group">
        <div class="group-head" :class="{ active: agent.activeDirectory === g.root }">
          <button class="group-toggle" :title="collapsedGroups[g.key] ? '展开' : '折叠'" @click="toggleGroup(g.key)">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" :class="{ folded: collapsedGroups[g.key] }"><path d="m4 6.5 4 4 4-4"/></svg>
          </button>
          <button class="group-name" :title="g.title" @click="selectWorkspace(g.root)">
            <svg v-if="g.root" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"><path d="M2.5 3.4v9.2h11V5H8.5L6.9 3.4H2.5Z"/></svg>
            <svg v-else width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3"><path d="M2.5 4.5h11v8h-11v-8Zm0 3h11"/></svg>
            <span class="group-label">{{ g.label }}</span>
            <span class="workspace-count">{{ g.items.length }}</span>
          </button>
          <div class="group-actions">
            <button v-if="g.root" class="ws-row-btn" title="在此目录新建会话" @click="newSessionIn($event, g.root)">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 3.2v9.6M3.2 8h9.6"/></svg>
            </button>
            <button v-if="isWorkspaceRoot(g.root)" class="ws-row-btn remove" title="移出工作目录" @click="removeWorkspace($event, g.root)">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M4.6 4.6l6.8 6.8M11.4 4.6l-6.8 6.8"/></svg>
            </button>
          </div>
        </div>

        <div v-show="!collapsedGroups[g.key]" class="group-items">
          <div v-if="g.items.length === 0" class="group-empty">{{ searchQuery ? '无匹配结果' : '暂无会话' }}</div>
          <div v-for="c in g.items" :key="c.id" class="history-item" :class="{ active: agent.conversationId === c.id }" @click="selectConversation(c)" @mouseenter="showUsage(c)" @mouseleave="hideUsage()">
            <div class="item-content">
              <template v-if="editingId === c.id">
                <input v-model="editingTitle" class="rename-input" @keyup.enter="saveRename" @keyup.escape="cancelRename" @blur="saveRename" autofocus />
              </template>
              <template v-else>
                <div class="item-title-row">
                  <span class="item-title" @dblclick.stop="startRename(c)">{{ c.title }}</span>
                  <span v-if="agent.sessions[c.id]?.streamPhase === 'queued'" class="stream-badge queued">
                    排队中 #{{ agent.sessions[c.id]?.queuePosition }}
                  </span>
                  <span v-else-if="agent.sessions[c.id]?.streamPhase === 'running'" class="stream-badge running">
                    运行中
                  </span>
                </div>
                <div class="item-meta" v-if="c.tokens_input || c.tokens_output || c.cost">
                  <span v-if="c.model" class="meta-model">{{ c.model.id }}</span>
                  <span v-if="c.tokens_input || c.tokens_output">{{ fmtTokens(c.tokens_input) }}→{{ fmtTokens(c.tokens_output) }}</span>
                  <span v-if="c.cost" class="meta-cost">{{ fmtCost(c.cost) }}</span>
                </div>
              </template>
            </div>
            <div class="item-actions">
              <button class="action-btn" @click.stop="handleFork($event, c.id)" title="分叉会话">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M6 9v6a3 3 0 0 0 3 3h6"/></svg>
              </button>
              <button class="action-btn" @click.stop="handleCompact($event, c.id)" title="压缩上下文">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 8h16M4 16h10"/><path d="M18 13l3 3-3 3"/></svg>
              </button>
              <button class="action-btn" @click.stop="handleStatus($event, c)" :title="statusOf[c.id] ? `状态：${statusOf[c.id]}` : '查询状态'">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
              </button>
              <button class="action-btn" @click.stop="startRename(c)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
              </button>
              <button class="action-btn delete" @click="handleDelete($event, c.id)">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
            </div>
            <div v-if="hoveredId === c.id" class="usage-pop">
              <div class="up-row"><span>模型</span><b>{{ c.model?.id || agent.selectedModel || '—' }}</b></div>
              <div class="up-row"><span>输入</span><b>{{ fmtTokens(c.tokens_input) || '0' }}</b></div>
              <div class="up-row"><span>输出</span><b>{{ fmtTokens(c.tokens_output) || '0' }}</b></div>
              <div v-if="c.tokens_reasoning" class="up-row"><span>推理</span><b>{{ fmtTokens(c.tokens_reasoning) }}</b></div>
              <div v-if="c.tokens_cache_read || c.tokens_cache_write" class="up-row"><span>缓存</span><b>{{ fmtTokens(c.tokens_cache_read) || 0 }} / {{ fmtTokens(c.tokens_cache_write) || 0 }}</b></div>
              <div class="up-row"><span>费用</span><b class="up-cost">{{ fmtCost(c.cost) || '—' }}</b></div>
              <div class="up-row"><span>更新</span><b>{{ fmtTime(c.updated_at) }}</b></div>
              <template v-if="agent.conversationId === c.id && ctxLimitFor(c)">
                <div class="up-row up-ctx"><span>上下文</span><b>{{ ctxEst ? ctxEst.tokens.toLocaleString() : '···' }} / {{ ctxLimitFor(c).toLocaleString() }}</b></div>
                <div class="up-bar"><span :style="{ width: ctxPctFor(c) + '%' }"></span></div>
              </template>
            </div>
          </div>
        </div>
      </div>
    </div>

    <DirPickerModal :show="showDirPicker" @close="showDirPicker = false" @select="handleDirPick" />
  </div>
</template>


<style scoped src="../styles/chat/multiAgentChatHistory.css"></style>
