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
import WorkspaceQuickSwitcher from './WorkspaceQuickSwitcher.vue'
import { baseName, isUnder, matchesConversation, normPath, queryTokens, workspaceRootFor } from '../utils/workspaceNav'

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
const quickOpen = ref(false)
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
  const tokens = queryTokens(searchQuery.value)
  const active = agent.activeDirectory
  let list = agent.conversations
  if (active) list = list.filter(c => isUnder(c.directory || '', active))
  if (tokens.length) {
    list = list.filter(c => matchesConversation(c, tokens, workspaceRootFor(c.directory, perm.workspaces)))
  }

  const byRoot = new Map<string, ConversationMeta[]>()
  const temp: ConversationMeta[] = []
  for (const c of list) {
    const root = workspaceRootFor(c.directory, perm.workspaces)
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

interface MenuItem {
  label: string
  icon: string
  danger?: boolean
  run: () => void
}
const menu = ref<{ x: number; y: number; items: MenuItem[] } | null>(null)
const MENU_ITEM_HEIGHT = 32

function openMenu(event: MouseEvent, items: MenuItem[]) {
  event.preventDefault()
  event.stopPropagation()
  menu.value = {
    x: Math.max(8, Math.min(event.clientX, window.innerWidth - 196)),
    y: Math.max(8, Math.min(event.clientY, window.innerHeight - items.length * MENU_ITEM_HEIGHT - 16)),
    items,
  }
}

function runMenuItem(item: MenuItem) {
  menu.value = null
  item.run()
}

function onGlobalKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    quickOpen.value = !quickOpen.value
    return
  }
  if (event.key === 'Escape') {
    menu.value = null
    quickOpen.value = false
  }
}

function onGlobalPointerDown() {
  menu.value = null
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKeydown)
  window.addEventListener('pointerdown', onGlobalPointerDown)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKeydown)
  window.removeEventListener('pointerdown', onGlobalPointerDown)
})

async function copyPath(path: string) {
  try {
    if (!navigator.clipboard) throw new Error('unsupported')
    await navigator.clipboard.writeText(path)
    agent.setNotice('已复制目录路径')
  } catch {
    agent.setNotice('复制失败，请手动复制路径')
  }
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

function createSessionIn(root: string | null) {
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

async function removeWorkspaceRoot(root: string | null) {
  if (!root) return
  wsBusy.value = true
  wsError.value = ''
  try {
    await perm.removeWorkspace(root)
    if (agent.activeDirectory && isUnder(agent.activeDirectory, root)) agent.setActiveDirectory(null)
  } catch (e: any) {
    wsError.value = String(e?.message || '移除目录失败')
  } finally {
    wsBusy.value = false
  }
}

function groupMenu(g: WorkspaceGroup): MenuItem[] {
  const root = g.root
  if (!root) return []
  const items: MenuItem[] = [
    { label: '在此目录新建会话', icon: 'M12 5v14M5 12h14', run: () => createSessionIn(root) },
    { label: '复制目录路径', icon: 'M9 9h10v10H9zM5 15H3V5h10v2', run: () => void copyPath(root) },
  ]
  if (isWorkspaceRoot(root)) {
    items.push({
      label: '移出工作目录',
      icon: 'M4.6 4.6l6.8 6.8M11.4 4.6l-6.8 6.8',
      danger: true,
      run: () => void removeWorkspaceRoot(root),
    })
  }
  return items
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

async function deleteConversation(c: ConversationMeta) {
  if (!window.confirm(`删除会话「${c.title}」？此操作不可撤销。`)) return
  if (agent.conversationId === c.id) {
    agent.newChat()
    router.push({ name: 'MultiAgent' })
  }
  try {
    await apiDelete(c.id)
    await agent.loadConversations()
  } catch (err) {
    agent.setNotice(String(err instanceof Error ? err.message : err))
  }
}

async function forkConversation(id: string) {
  try {
    const info = await apiV2Fork(id)
    v2.setInfo(info.id, info)
    void agent.loadConversations()
    await router.push({ name: 'MultiAgentConversation', params: { id: info.id } })
  } catch (err) {
    agent.setNotice(String(err instanceof Error ? err.message : err))
  }
}

async function compactConversation(id: string) {
  const runtime = v2.getRuntime(id)
  if (runtime?.status.type === 'busy' || runtime?.status.type === 'retry') {
    agent.setNotice('会话运行中，请先停止后再压缩')
    return
  }
  try {
    await apiV2Compact(id)
  } catch (err) {
    agent.setNotice(String(err instanceof Error ? err.message : err))
  }
}

const statusOf = ref<Record<string, string>>({})
async function queryStatus(c: ConversationMeta) {
  try {
    const st = await agent.fetchSessionStatus(c.id)
    statusOf.value = { ...statusOf.value, [c.id]: st?.status || 'unknown' }
  } catch (err) {
    agent.setNotice(String(err instanceof Error ? err.message : err))
  }
}

function conversationMenu(c: ConversationMeta): MenuItem[] {
  return [
    { label: '分叉会话', icon: 'M6 3v12a3 3 0 0 0 3 3h9M18 18l-3-3m3 3-3 3M6 9h9a3 3 0 0 1 3 3v3', run: () => void forkConversation(c.id) },
    { label: '压缩上下文', icon: 'M4 8h16M4 16h10M18 13l3 3-3 3', run: () => void compactConversation(c.id) },
    { label: '查询状态', icon: 'M22 12h-4l-3 9L9 3l-3 9H2', run: () => void queryStatus(c) },
    { label: '重命名', icon: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z', run: () => startRename(c) },
    { label: '删除会话', icon: 'M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2', danger: true, run: () => void deleteConversation(c) },
  ]
}
</script>

<template>
  <div class="chat-history">
    <div class="history-header">
      <span class="history-title">工作目录</span>
      <div class="header-actions">
        <button class="ws-add-btn" title="快速切换 (Ctrl/⌘+K)" @click="quickOpen = true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 6h16M4 12h10M4 18h7"/><circle cx="17" cy="15" r="4"/></svg>
        </button>
        <button class="ws-add-btn" :disabled="wsBusy" title="添加工作目录" @click="showDirPicker = true">
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M8 3.2v9.6M3.2 8h9.6"/></svg>
        </button>
      </div>
    </div>
    <div class="search-box">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
      <input v-model="searchQuery" placeholder="搜索目录或会话..." aria-label="搜索目录或会话" />
      <kbd class="search-kbd">⌘K</kbd>
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
        <div class="group-head" :class="{ active: agent.activeDirectory === g.root }" @contextmenu="openMenu($event, groupMenu(g))">
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
            <button v-if="g.root" class="ws-row-btn" title="更多操作" aria-label="目录更多操作" @click.stop="openMenu($event, groupMenu(g))">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><circle cx="3" cy="8" r="1.4"/><circle cx="8" cy="8" r="1.4"/><circle cx="13" cy="8" r="1.4"/></svg>
            </button>
          </div>
        </div>

        <div v-show="!collapsedGroups[g.key]" class="group-items">
          <div v-if="g.items.length === 0" class="group-empty">{{ searchQuery ? '无匹配结果' : '暂无会话' }}</div>
          <div v-for="c in g.items" :key="c.id" class="history-item" :class="{ active: agent.conversationId === c.id }" @click="selectConversation(c)" @contextmenu="openMenu($event, conversationMenu(c))" @mouseenter="showUsage(c)" @mouseleave="hideUsage()">
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
              <button class="action-btn" :title="statusOf[c.id] ? `状态：${statusOf[c.id]} · 更多操作` : '更多操作'" aria-label="会话更多操作" @click.stop="openMenu($event, conversationMenu(c))">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor"><circle cx="3" cy="8" r="1.4"/><circle cx="8" cy="8" r="1.4"/><circle cx="13" cy="8" r="1.4"/></svg>
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
    <WorkspaceQuickSwitcher
      :show="quickOpen"
      :workspaces="perm.workspaces"
      :conversations="agent.conversations"
      @close="quickOpen = false"
      @select-directory="selectWorkspace"
      @select-conversation="selectConversation"
      @new-session="createSessionIn"
    />
    <Teleport to="body">
      <div v-if="menu" class="ctx-menu" :style="{ left: `${menu.x}px`, top: `${menu.y}px` }" @pointerdown.stop @click.stop>
        <button v-for="item in menu.items" :key="item.label" class="ctx-item" :class="{ danger: item.danger }" @click="runMenuItem(item)">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path :d="item.icon" /></svg>
          <span>{{ item.label }}</span>
        </button>
      </div>
    </Teleport>
  </div>
</template>


<style scoped src="../styles/chat/multiAgentChatHistory.css"></style>
