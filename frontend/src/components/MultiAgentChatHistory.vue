<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useMultiAgentStore } from '../stores/multiAgent'
import { usePermissionStore } from '../stores/permission'
import { useAuthStore } from '../stores/auth'
import { deleteConversation as apiDelete } from '../api/sessions'
import type { ConversationMeta } from '../api/sessions'
import DirPickerModal from './DirPickerModal.vue'

const router = useRouter()
const agent = useMultiAgentStore()
const perm = usePermissionStore()
const auth = useAuthStore()
const searchQuery = ref('')
const editingId = ref<string | null>(null)
const editingTitle = ref('')
const showDirMenu = ref(false)
const showDirPicker = ref(false)

onMounted(() => {
  // 双保险：鉴权启用但未登录时不发会话/工作区请求（登录页不会挂载本组件，防止时序异常）
  if (auth.enabled && !auth.isLoggedIn) return
  agent.loadConversations()
  perm.loadWorkspaces()
})

// 按目录分组对话列表（对齐 opencode 按 project/directory 分组）
const directoryGroups = computed(() => {
  const filtered = agent.conversations.filter(c =>
    !searchQuery.value || c.title.toLowerCase().includes(searchQuery.value.toLowerCase())
  )
  const map = new Map<string, ConversationMeta[]>()
  for (const c of filtered) {
    const key = c.directory || ''
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(c)
  }
  return Array.from(map.entries()).map(([dir, items]) => ({
    directory: dir,
    label: dir || '默认目录',
    items: [...items].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1)),
  }))
})

// 新建对话（可选绑定工作目录，目录成为会话 cwd）
function handleNewChat(dir?: string) {
  showDirMenu.value = false
  agent.newChat()
  if (dir !== undefined) agent.setSessionDirectory(dir)
  router.push({ name: 'MultiAgent' })
}

function pickCustomDir() {
  showDirMenu.value = false
  showDirPicker.value = true
}

function handleDirPicked(path: string) {
  showDirPicker.value = false
  handleNewChat(path)
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
        <button class="new-chat-btn" @click="showDirMenu = !showDirMenu" title="新建对话（可选择工作目录）">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 5v14M5 12h14"/></svg>
          新建多智能体对话
        </button>
        <div v-if="showDirMenu" class="dir-menu" @click.stop>
          <div class="dir-menu-title">在哪个目录下创建对话？</div>
          <button class="dir-menu-item" @click="handleNewChat('')">📦 默认（backend/）</button>
          <template v-for="w in perm.workspaces" :key="w">
            <button class="dir-menu-item" :title="w" @click="handleNewChat(w)">📁 {{ w }}</button>
          </template>
          <button class="dir-menu-item pick" @click="pickCustomDir">⋯ 选择其他目录…</button>
        </div>
      </div>
    </div>
    <div class="search-box">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
      <input v-model="searchQuery" placeholder="搜索..." />
    </div>
    <div class="history-list">
      <div v-if="directoryGroups.length === 0" class="empty-hint">{{ searchQuery ? '无匹配结果' : '暂无历史对话' }}</div>
      <div v-for="group in directoryGroups" :key="group.directory" class="group">
        <div class="group-label" :title="group.directory">{{ group.label }}</div>
        <div v-for="c in group.items" :key="c.id" class="history-item" :class="{ active: agent.conversationId === c.id }" @click="selectConversation(c.id)">
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
        </div>
      </div>
    </div>
    <DirPickerModal :show="showDirPicker" @close="showDirPicker = false" @select="handleDirPicked" />
  </div>
</template>


<style scoped src="../styles/chat/multiAgentChatHistory.css"></style>
