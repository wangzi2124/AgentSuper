<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { deleteConversation } from '../api/chat'
import type { ConversationMeta } from '../api/chat'

const router = useRouter()
const chat = useChatStore()
const searchQuery = ref('')
const editingId = ref<string | null>(null)
const editingTitle = ref('')

onMounted(() => {
  chat.loadConversations()
})

const groupedConversations = computed(() => {
  const now = new Date()
  const today = now.toDateString()
  const yesterday = new Date(now.getTime() - 86400000).toDateString()
  const weekAgo = new Date(now.getTime() - 7 * 86400000)

  const filtered = chat.conversations.filter(c =>
    !searchQuery.value || c.title.toLowerCase().includes(searchQuery.value.toLowerCase())
  )

  const groups: { label: string; items: ConversationMeta[] }[] = []
  const todayItems: ConversationMeta[] = []
  const yesterdayItems: ConversationMeta[] = []
  const weekItems: ConversationMeta[] = []
  const olderItems: ConversationMeta[] = []

  for (const c of filtered) {
    const d = new Date(c.updated_at)
    if (d.toDateString() === today) {
      todayItems.push(c)
    } else if (d.toDateString() === yesterday) {
      yesterdayItems.push(c)
    } else if (d > weekAgo) {
      weekItems.push(c)
    } else {
      olderItems.push(c)
    }
  }

  if (todayItems.length) groups.push({ label: '今天', items: todayItems })
  if (yesterdayItems.length) groups.push({ label: '昨天', items: yesterdayItems })
  if (weekItems.length) groups.push({ label: '最近7天', items: weekItems })
  if (olderItems.length) groups.push({ label: '更早', items: olderItems })

  return groups
})

function handleNewChat() {
  chat.newChat()
  router.push({ name: 'Chat' })
}

function selectConversation(id: string) {
  chat.loadConversation(id)
  router.push({ name: 'ChatConversation', params: { id } })
}

function startRename(c: ConversationMeta) {
  editingId.value = c.id
  editingTitle.value = c.title
}

function saveRename() {
  if (editingId.value && editingTitle.value.trim()) {
    chat.renameConversation(editingId.value, editingTitle.value.trim())
  }
  editingId.value = null
}

function cancelRename() {
  editingId.value = null
}

function handleDelete(e: Event, id: string) {
  e.stopPropagation()
  if (chat.conversationId === id) {
    chat.newChat()
    router.push({ name: 'Chat' })
  }
  deleteConversation(id).then(() => chat.loadConversations())
}
</script>

<template>
  <div class="chat-history">
    <div class="history-header">
      <button class="new-chat-btn" @click="handleNewChat" title="新建对话">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M12 5v14M5 12h14"/>
        </svg>
        新建对话
      </button>
    </div>
    <div class="search-box">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="11" cy="11" r="8"/>
        <path d="M21 21l-4.35-4.35"/>
      </svg>
      <input v-model="searchQuery" placeholder="搜索对话..." />
    </div>
    <div class="history-list">
      <div v-if="groupedConversations.length === 0" class="empty-hint">
        {{ searchQuery ? '无匹配结果' : '暂无历史对话' }}
      </div>
      <div v-for="group in groupedConversations" :key="group.label" class="group">
        <div class="group-label">{{ group.label }}</div>
        <div
          v-for="c in group.items"
          :key="c.id"
          class="history-item"
          :class="{ active: chat.conversationId === c.id }"
          @click="selectConversation(c.id)"
        >
          <div class="item-content">
            <template v-if="editingId === c.id">
              <input
                v-model="editingTitle"
                class="rename-input"
                @keyup.enter="saveRename"
                @keyup.escape="cancelRename"
                @blur="saveRename"
                autofocus
              />
            </template>
            <template v-else>
              <span class="item-title" @dblclick.stop="startRename(c)">{{ c.title }}</span>
            </template>
          </div>
          <div class="item-actions">
            <button class="action-btn" @click.stop="startRename(c)" title="重命名">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
              </svg>
            </button>
            <button class="action-btn delete" @click="handleDelete($event, c.id)" title="删除">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-history {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.history-header {
  padding: 8px;
}
.new-chat-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 8px 12px;
  border: 1px dashed var(--border);
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
  transition: all 0.15s;
}
.new-chat-btn:hover {
  border-color: var(--primary);
  color: var(--primary);
  background: rgba(var(--primary-rgb, 59, 130, 246), 0.05);
}
.search-box {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 8px 8px;
  padding: 6px 10px;
  border-radius: 8px;
  background: var(--bg);
  color: var(--text-secondary);
}
.search-box input {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  outline: none;
}
.search-box input::placeholder {
  color: var(--text-secondary);
  opacity: 0.6;
}
.history-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 8px 8px;
}
.empty-hint {
  text-align: center;
  padding: 24px 0;
  color: var(--text-secondary);
  font-size: 13px;
  opacity: 0.6;
}
.group {
  margin-bottom: 8px;
}
.group-label {
  padding: 6px 8px 4px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  opacity: 0.7;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.history-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.1s;
}
.history-item:hover {
  background: var(--bg);
}
.history-item.active {
  background: var(--bg);
}
.item-content {
  flex: 1;
  min-width: 0;
}
.item-title {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: var(--text);
}
.item-actions {
  display: flex;
  gap: 2px;
  opacity: 0;
  transition: opacity 0.15s;
}
.history-item:hover .item-actions {
  opacity: 1;
}
.action-btn {
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.1s;
}
.action-btn:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text);
}
.action-btn.delete:hover {
  color: #ef4444;
}
.rename-input {
  width: 100%;
  border: 1px solid var(--primary);
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 13px;
  background: var(--bg);
  color: var(--text);
  outline: none;
}
</style>
