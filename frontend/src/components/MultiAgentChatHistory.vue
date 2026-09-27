<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useMultiAgentStore } from '../stores/multiAgent'
import { usePermissionStore } from '../stores/permission'
import { useAuthStore } from '../stores/auth'
import { deleteConversation as apiDelete } from '../api/sessions'
import type { ConversationMeta } from '../api/sessions'
import { estimateTokens } from '../api/models'

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
const perm = usePermissionStore()
const auth = useAuthStore()
const searchQuery = ref('')
const editingId = ref<string | null>(null)
const editingTitle = ref('')
// [ctx-strip 迁移] 会话用量浮层：鼠标悬浮历史项即显示（当前会话额外显示上下文占用）
// [perf] 原实现在每次 mouseenter 都把整个 messages 数组 POST 给 /api/models/estimate-tokens，
// 悬停一列历史 = N 个大请求。改为：悬停停留 250ms 才发 + 按会话/消息指纹缓存 + 同请求去重。
const hoveredId = ref<string | null>(null)
const ctxEst = ref<{ tokens: number; chars: number; method: string } | null>(null)
const CTX_EST_DEBOUNCE_MS = 250
let ctxHoverTimer: ReturnType<typeof setTimeout> | null = null
let ctxEstInFlight: Promise<{ tokens: number; chars: number; method: string }> | null = null
let ctxEstKey = ''

function ctxEstCacheKey(): string {
  const ms = agent.messages
  // 指纹：条数 + 每条长度 + 末条内容尾部，变化即视为新上下文
  let sig = `${agent.conversationId}|${ms.length}|`
  for (const m of ms) sig += `${(m.content || '').length},`
  const last = ms.length ? (ms[ms.length - 1].content || '') : ''
  return sig + last.slice(-64)
}

async function loadCtxEst() {
  if (!agent.messages.length) { ctxEst.value = null; return }
  const key = ctxEstCacheKey()
  if (key === ctxEstKey && ctxEst.value) return
  // 同一指纹的并发请求共享一个 Promise（悬停快速划过时只发一次）
  if (key === ctxEstKey && ctxEstInFlight) {
    try { ctxEst.value = await ctxEstInFlight } catch { /* 静默失败 */ }
    return
  }
  ctxEstKey = key
  ctxEstInFlight = estimateTokens({
    messages: agent.messages.map(m => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content || '' })),
  })
  try {
    ctxEst.value = await ctxEstInFlight
  } catch { /* 静默失败 */ }
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
  // 悬停停留一小会儿再请求，避免划过式悬停产生请求风暴
  if (ctxHoverTimer) clearTimeout(ctxHoverTimer)
  ctxHoverTimer = setTimeout(() => { ctxHoverTimer = null; void loadCtxEst() }, CTX_EST_DEBOUNCE_MS)
}
function hideUsage() {
  hoveredId.value = null
  if (ctxHoverTimer) { clearTimeout(ctxHoverTimer); ctxHoverTimer = null }
}

onBeforeUnmount(() => { if (ctxHoverTimer) clearTimeout(ctxHoverTimer) })

onMounted(() => {
  // 双保险：鉴权启用但未登录时不发会话/工作区请求（登录页不会挂载本组件，防止时序异常）
  if (auth.enabled && !auth.isLoggedIn) return
  agent.loadConversations()
  perm.loadWorkspaces()
})

// 对话列表（按更新时间倒序；不再按目录分组显示路径地址）
const sortedConversations = computed(() => {
  const filtered = agent.conversations.filter(c =>
    !searchQuery.value || c.title.toLowerCase().includes(searchQuery.value.toLowerCase())
  )
  return [...filtered].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))
})

// 新建对话（可选绑定工作目录，目录成为会话 cwd）
function handleNewChat(dir?: string) {
  agent.newChat(dir)
  router.push({ name: 'MultiAgent' })
}

function selectConversation(id: string) { agent.loadConversation(id); router.push({ name: 'MultiAgentConversation', params: { id } }) }
function startRename(c: ConversationMeta) { editingId.value = c.id; editingTitle.value = c.title }
function saveRename() { if (editingId.value && editingTitle.value.trim()) { agent.renameConversation(editingId.value, editingTitle.value.trim()) }; editingId.value = null }
function cancelRename() { editingId.value = null }
function handleDelete(e: Event, id: string) { e.stopPropagation(); if (agent.conversationId === id) { agent.newChat(); router.push({ name: 'MultiAgent' }) }; apiDelete(id).then(() => agent.loadConversations()) }
</script>

<template>
  <div class="chat-history">
    <div class="history-header">
      <div class="new-chat-wrap">
        <button class="new-chat-btn" @click="handleNewChat()" title="新建对话">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
          新建多智能体对话
        </button>
      </div>
    </div>
    <div class="search-box">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
      <input v-model="searchQuery" placeholder="搜索..." />
    </div>
    <div class="history-list">
      <div v-if="sortedConversations.length === 0" class="empty-hint">{{ searchQuery ? '无匹配结果' : '暂无历史对话' }}</div>
      <div v-for="c in sortedConversations" :key="c.id" class="history-item" :class="{ active: agent.conversationId === c.id }" @click="selectConversation(c.id)" @mouseenter="showUsage(c)" @mouseleave="hideUsage()">
        <div class="item-content">
          <template v-if="editingId === c.id">
            <input v-model="editingTitle" class="rename-input" @keyup.enter="saveRename" @keyup.escape="cancelRename" @blur="saveRename" autofocus />
          </template>
          <template v-else>
            <div class="item-title-row">
              <span class="item-title" @dblclick.stop="startRename(c)">{{ c.title }}</span>
              <span v-if="agent.sessions[c.id]?.streamPhase === 'queued'" class="stream-badge queued">
                ⏳ 排队中 #{{ agent.sessions[c.id]?.queuePosition }}
              </span>
              <span v-else-if="agent.sessions[c.id]?.streamPhase === 'running'" class="stream-badge running">
                ● 运行中
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
</template>


<style scoped src="../styles/chat/multiAgentChatHistory.css"></style>
