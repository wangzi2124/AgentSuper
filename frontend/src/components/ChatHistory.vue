<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useChatStore } from '../stores/chat'
import { usePermissionStore } from '../stores/permission'
import { deleteConversation } from '../api/chat'
import type { ConversationMeta } from '../api/chat'
import DirPickerModal from './DirPickerModal.vue'

// 路由实例
const router = useRouter()
// 聊天状态管理
const chat = useChatStore()
// 工作目录管理（新建对话选目录用）
const perm = usePermissionStore()
// 搜索关键词
const searchQuery = ref('')
// 当前正在编辑的对话ID
const editingId = ref<string | null>(null)
// 编辑中的对话标题
const editingTitle = ref('')
// 新建对话目录选择
const showDirMenu = ref(false)
const showDirPicker = ref(false)

// 组件挂载时加载对话列表与工作目录
onMounted(() => {
  chat.loadConversations()
  perm.loadWorkspaces()
})

// 新建对话（opencode 式：可选绑定工作目录，目录成为会话 cwd）
function handleNewChat(dir?: string) {
  if (dir !== undefined) chat.setSessionDirectory(dir)
  showDirMenu.value = false
  chat.newChat()
  router.push({ name: 'Chat' })
}

function pickCustomDir() {
  showDirMenu.value = false
  showDirPicker.value = true
}

function handleDirPicked(path: string) {
  showDirPicker.value = false
  handleNewChat(path)
}

// 按目录分组对话列表（对齐 opencode 按 project/directory 分组）
const directoryGroups = computed(() => {
  const filtered = chat.conversations.filter(c =>
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

// 选择并加载指定对话
function selectConversation(id: string) {
  chat.loadConversation(id)
  router.push({ name: 'ChatConversation', params: { id } })
}

// 开始重命名对话
function startRename(c: ConversationMeta) {
  editingId.value = c.id
  editingTitle.value = c.title
}

// 保存重命名
function saveRename() {
  if (editingId.value && editingTitle.value.trim()) {
    chat.renameConversation(editingId.value, editingTitle.value.trim())
  }
  editingId.value = null
}

// 取消重命名
function cancelRename() {
  editingId.value = null
}

// 删除对话
function handleDelete(e: Event, id: string) {
  e.stopPropagation()
  if (chat.conversationId === id) {
    chat.newChat()
    router.push({ name: 'Chat' })
  }
  deleteConversation(id).then(() => chat.loadConversations())
}

// 获取会话的流式阶段
function getSessionPhase(id: string): 'idle' | 'queued' | 'running' {
  const session = chat.sessions[id]
  return session?.streamPhase || 'idle'
}

// 获取会话的排队位置
function getSessionQueuePosition(id: string): number | null {
  const session = chat.sessions[id]
  return session?.queuePosition ?? null
}
</script>

<template>
  <div class="chat-history">
    <div class="history-header">
      <div class="new-chat-wrap">
        <button class="new-chat-btn" @click="showDirMenu = !showDirMenu" title="新建对话（可选择工作目录）">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M12 5v14M5 12h14"/>
          </svg>
          新建对话
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
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="11" cy="11" r="8"/>
        <path d="M21 21l-4.35-4.35"/>
      </svg>
      <input v-model="searchQuery" placeholder="搜索对话..." />
    </div>
    <div class="history-list">
      <div v-if="directoryGroups.length === 0" class="empty-hint">
        {{ searchQuery ? '无匹配结果' : '暂无历史对话' }}
      </div>
      <div v-for="group in directoryGroups" :key="group.directory" class="group">
        <div class="group-label" :title="group.directory">
          {{ group.label }}
        </div>
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
              <span v-if="getSessionPhase(c.id) === 'queued'" class="session-badge queued">
                ⏳ #{{ getSessionQueuePosition(c.id) }}
              </span>
              <span v-else-if="getSessionPhase(c.id) === 'running'" class="session-badge running">
                ● streaming
              </span>
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
    <DirPickerModal :show="showDirPicker" @close="showDirPicker = false" @select="handleDirPicked" />
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
.session-badge {
  display: inline-block;
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 4px;
  margin-left: 4px;
  vertical-align: middle;
  white-space: nowrap;
  flex-shrink: 0;
}
.session-badge.queued {
  background: rgba(251, 191, 36, 0.15);
  color: #f59e0b;
}
.session-badge.running {
  background: rgba(34, 197, 94, 0.15);
  color: #22c55e;
  animation: pulse-badge 1.5s ease-in-out infinite;
}
@keyframes pulse-badge {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
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

/* 新建对话目录选择 */
.new-chat-wrap {
  position: relative;
  flex: 1;
}
.dir-menu {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  right: 0;
  z-index: 100;
  background: var(--surface, #ffffff);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.35);
  padding: 6px;
  max-height: 60vh;
  overflow-y: auto;
}
.dir-menu-title {
  font-size: 11px;
  color: var(--text-secondary);
  padding: 6px 8px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 4px;
}
.dir-menu-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 8px 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dir-menu-item:hover {
  background: rgba(255, 255, 255, 0.08);
}
.dir-menu-item.pick {
  border-top: 1px solid var(--border);
  margin-top: 4px;
  padding-top: 8px;
  color: var(--primary);
}

/* 目录分组 */
.group-label {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary);
  padding: 10px 12px 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  user-select: none;
}
.group + .group {
  margin-top: 4px;
}

</style>
