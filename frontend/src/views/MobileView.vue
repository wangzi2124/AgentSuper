<script setup lang="ts">
import { ref, computed, nextTick, watch, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { useMobileChatStore } from '../stores/mobileChat'
import WeatherPanel from '../mobile/WeatherPanel.vue'
import SettingsPanel from '../mobile/SettingsPanel.vue'
import GeneratedFilesPanel from '../mobile/GeneratedFilesPanel.vue'

const route = useRoute()
const chat = useMobileChatStore()
const inputText = ref('')
const chatContainer = ref<HTMLElement>()
const isNearBottom = ref(true)
const showSidebar = ref(false)
const showSettings = ref(false)
const showFiles = ref(false)
const editingTitle = ref('')
const editingId = ref<string | null>(null)

const messages = computed(() => chat.messages)

watch(() => messages.value.length, async () => {
  await nextTick()
  if (isNearBottom.value && chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
})

onMounted(() => {
  chat.loadConversations()
  const id = route.params.id as string
  if (id) {
    chat.loadConversation(id)
  }
})

async function sendMessage() {
  if (!inputText.value.trim() || chat.loading) return
  const text = inputText.value.trim()
  inputText.value = ''
  await chat.send(text)
  await nextTick()
  scrollToBottom()
}

function handleScroll() {
  if (!chatContainer.value) return
  const { scrollTop, scrollHeight, clientHeight } = chatContainer.value
  isNearBottom.value = scrollHeight - scrollTop - clientHeight < 100
}

function scrollToBottom() {
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

function autoResize(e: Event) {
  const textarea = e.target as HTMLTextAreaElement
  textarea.style.height = 'auto'
  textarea.style.height = Math.min(textarea.scrollHeight, 120) + 'px'
}

function formatMessage(content: string) {
  if (!content) return ''
  return content
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>')
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.*?)`/g, '<code>$1</code>')
}

function formatTime(timestamp: Date) {
  if (!timestamp) return ''
  const date = timestamp instanceof Date ? timestamp : new Date(timestamp)
  return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`
}

function startEdit(conv: any) {
  editingId.value = conv.id
  editingTitle.value = conv.title
}

async function saveEdit() {
  if (editingId.value && editingTitle.value.trim()) {
    await chat.renameConversation(editingId.value, editingTitle.value.trim())
  }
  editingId.value = null
  editingTitle.value = ''
}

function formatDate(date: Date | string) {
  const d = new Date(date)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) {
    return `${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  }
  return `${d.getMonth() + 1}/${d.getDate()}`
}
</script>

<template>
  <div class="m-app">
    <!-- Header -->
    <header class="m-header">
      <button class="icon-btn" @click="showSidebar = !showSidebar">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M3 12h18M3 6h18M3 18h18"/>
        </svg>
      </button>
      <h1 class="title">{{ chat.conversationTitle || 'AI 助手' }}</h1>
      <div class="header-actions">
        <WeatherPanel />
        <button class="icon-btn" @click="showSettings = !showSettings">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="3"/>
            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
          </svg>
        </button>
      </div>
    </header>

    <!-- Sidebar -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showSidebar" class="sidebar-overlay" @click="showSidebar = false">
          <div class="sidebar" @click.stop>
            <div class="sidebar-header">
              <h2>会话列表</h2>
              <button class="close-btn" @click="showSidebar = false">×</button>
            </div>
            
            <button class="new-chat-btn" @click="chat.newChat()">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 5v14M5 12h14"/>
              </svg>
              新建对话
            </button>
            
            <div class="conv-list">
              <div v-if="chat.conversations.length === 0" class="empty">
                暂无对话记录
              </div>
              <div v-for="conv in chat.conversations" :key="conv.id"
                :class="['conv-item', { active: chat.activeSessionId === conv.id }]"
                @click="chat.loadConversation(conv.id)">
                <div class="conv-info">
                  <template v-if="editingId === conv.id">
                    <input v-model="editingTitle" @blur="saveEdit" @keyup.enter="saveEdit" class="conv-edit" />
                  </template>
                  <template v-else>
                    <div class="conv-title">{{ conv.title }}</div>
                    <div class="conv-time">{{ formatDate(conv.updated_at) }}</div>
                  </template>
                </div>
                <button v-if="editingId !== conv.id" class="conv-menu" @click.stop="startEdit(conv)">✏️</button>
              </div>
            </div>
            
            <div class="sidebar-footer">
              <button class="menu-link" @click="showSidebar = false; showFiles = true">
                <span>📁</span> 生成的文件
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Settings Panel -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showSettings" class="sidebar-overlay" @click="showSettings = false">
          <div class="settings-drawer" @click.stop>
            <SettingsPanel @close="showSettings = false" />
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Generated Files Panel -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showFiles" class="sidebar-overlay" @click="showFiles = false">
          <div class="files-drawer" @click.stop>
            <div class="panel-header">
              <h3>生成的文件</h3>
              <button class="close-btn" @click="showFiles = false">×</button>
            </div>
            <GeneratedFilesPanel />
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- Chat Messages -->
    <div class="chat-container" ref="chatContainer" @scroll="handleScroll">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="empty-icon">🤖</div>
        <h2>你好！</h2>
        <p>我是AI助手，有什么可以帮助你的？</p>
        <div class="quick-actions">
          <button @click="inputText = '帮我总结一下知识库的内容'">总结知识库</button>
          <button @click="inputText = '帮我搜索最新的AI资讯'">搜索资讯</button>
          <button @click="inputText = '帮我写一篇关于人工智能的文章'">写文章</button>
        </div>
      </div>
      
      <div v-for="msg in messages" :key="msg.id" :class="['message', msg.role]">
        <div class="avatar">
          <span v-if="msg.role === 'user'">👤</span>
          <span v-else>🤖</span>
        </div>
        <div class="content">
          <div class="bubble" v-html="formatMessage(msg.content)"></div>
          <div class="meta">
            <span class="time">{{ formatTime(msg.timestamp) }}</span>
          </div>
        </div>
      </div>
      
      <div v-if="chat.loading" class="message assistant">
        <div class="avatar">🤖</div>
        <div class="content">
          <div class="bubble typing">
            <span></span><span></span><span></span>
          </div>
        </div>
      </div>
    </div>

    <!-- Scroll to Bottom -->
    <Transition name="fade">
      <button v-if="!isNearBottom && messages.length > 0" class="scroll-btn" @click="scrollToBottom">
        ↓
      </button>
    </Transition>

    <!-- Input Area -->
    <div class="input-area">
      <div class="input-wrapper">
        <textarea 
          v-model="inputText"
          placeholder="输入消息..."
          rows="1"
          :disabled="chat.loading"
          @keydown.enter.prevent="sendMessage"
          @input="autoResize"
        ></textarea>
        <button class="send-btn" @click="sendMessage" :disabled="!inputText.trim() || chat.loading">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z"/>
          </svg>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.m-app {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  background: var(--bg, #f8fafc);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  overflow: hidden;
}

.m-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--surface, #fff);
  border-bottom: 1px solid var(--border, #e2e8f0);
  padding-top: max(12px, env(safe-area-inset-top));
  z-index: 100;
}

.icon-btn {
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text, #1e293b);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.icon-btn:hover {
  background: var(--bg, #f1f5f9);
}

.title {
  flex: 1;
  font-size: 16px;
  font-weight: 600;
  text-align: center;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.header-actions {
  display: flex;
  gap: 4px;
}

/* Sidebar */
.sidebar-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 200;
}

.sidebar {
  position: absolute;
  top: 0;
  left: 0;
  width: 280px;
  height: 100%;
  background: var(--surface, #fff);
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}

.sidebar-header h2 {
  font-size: 16px;
  font-weight: 600;
}

.close-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: var(--bg, #f1f5f9);
  font-size: 18px;
  cursor: pointer;
}

.new-chat-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin: 12px 16px;
  padding: 10px;
  border: 1px dashed var(--border, #e2e8f0);
  border-radius: 8px;
  background: transparent;
  color: var(--text, #1e293b);
  font-size: 14px;
  cursor: pointer;
}

.new-chat-btn:hover {
  background: var(--bg, #f1f5f9);
}

.conv-list {
  flex: 1;
  overflow-y: auto;
  padding: 0 12px 12px;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid var(--border, #e2e8f0);
}

.menu-link {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text, #1e293b);
  font-size: 14px;
  cursor: pointer;
  text-align: left;
}

.menu-link:hover {
  background: var(--bg, #f1f5f9);
}

.empty {
  padding: 24px;
  text-align: center;
  color: var(--text-secondary, #64748b);
  font-size: 13px;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px;
  border-radius: 8px;
  cursor: pointer;
  margin-bottom: 4px;
}

.conv-item:hover {
  background: var(--bg, #f1f5f9);
}

.conv-item.active {
  background: rgba(59, 130, 246, 0.1);
}

.conv-info {
  flex: 1;
  min-width: 0;
}

.conv-title {
  font-size: 14px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-time {
  font-size: 11px;
  color: var(--text-secondary, #64748b);
  margin-top: 2px;
}

.conv-edit {
  width: 100%;
  padding: 4px 8px;
  border: 1px solid var(--primary, #3b82f6);
  border-radius: 4px;
  font-size: 14px;
}

.conv-menu {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: transparent;
  font-size: 14px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}

.conv-item:hover .conv-menu {
  opacity: 1;
}

/* Settings Drawer */
.settings-drawer {
  position: absolute;
  top: 0;
  right: 0;
  width: 300px;
  height: 100%;
  background: var(--surface, #fff);
  overflow-y: auto;
}

/* Files Drawer */
.files-drawer {
  position: absolute;
  top: 0;
  right: 0;
  width: 100%;
  max-width: 400px;
  height: 100%;
  background: var(--surface, #fff);
  overflow-y: auto;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--border, #e2e8f0);
  position: sticky;
  top: 0;
  background: var(--surface, #fff);
  z-index: 10;
}

.panel-header h3 {
  font-size: 16px;
  font-weight: 600;
}

.close-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: var(--bg, #f1f5f9);
  font-size: 18px;
  cursor: pointer;
}

/* Chat Container */
.chat-container {
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding: 16px;
  padding-bottom: 100px;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 60vh;
  text-align: center;
}

.empty-icon {
  font-size: 56px;
  margin-bottom: 12px;
}

.empty-state h2 {
  font-size: 20px;
  font-weight: 600;
  margin-bottom: 6px;
}

.empty-state p {
  font-size: 14px;
  color: var(--text-secondary, #64748b);
  margin-bottom: 20px;
}

.quick-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  justify-content: center;
}

.quick-actions button {
  padding: 8px 14px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 20px;
  background: var(--surface, #fff);
  font-size: 12px;
  color: var(--text, #1e293b);
  cursor: pointer;
}

.quick-actions button:hover {
  border-color: var(--primary, #3b82f6);
  color: var(--primary, #3b82f6);
}

/* Messages */
.message {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
  max-width: 85%;
}

.message.user {
  flex-direction: row-reverse;
  margin-left: auto;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: var(--bg, #f1f5f9);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}

.message.user .avatar {
  background: var(--primary, #3b82f6);
}

.content {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.bubble {
  padding: 10px 12px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
}

.message.user .bubble {
  background: var(--primary, #3b82f6);
  color: white;
  border-bottom-right-radius: 4px;
}

.message.assistant .bubble {
  background: var(--surface, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-bottom-left-radius: 4px;
}

.meta {
  font-size: 10px;
  color: var(--text-secondary, #64748b);
}

.message.user .meta {
  text-align: right;
}

/* Typing Indicator */
.bubble.typing {
  display: flex;
  gap: 4px;
  padding: 12px 14px;
}

.bubble.typing span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--text-secondary, #64748b);
  animation: bounce 1.4s infinite;
}

.bubble.typing span:nth-child(2) {
  animation-delay: 0.2s;
}

.bubble.typing span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes bounce {
  0%, 60%, 100% { transform: translateY(0); }
  30% { transform: translateY(-4px); }
}

/* Scroll Button */
.scroll-btn {
  position: fixed;
  bottom: 90px;
  right: 16px;
  width: 36px;
  height: 36px;
  border: none;
  border-radius: 50%;
  background: var(--surface, #fff);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
  font-size: 16px;
  cursor: pointer;
  z-index: 50;
}

/* Input Area */
.input-area {
  position: fixed;
  bottom: 0;
  left: 0;
  right: 0;
  background: var(--surface, #fff);
  border-top: 1px solid var(--border, #e2e8f0);
  padding: 10px 16px;
  padding-bottom: max(10px, env(safe-area-inset-bottom));
  z-index: 100;
}

.input-wrapper {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  background: var(--bg, #f1f5f9);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 22px;
  padding: 6px 10px;
}

.input-wrapper textarea {
  flex: 1;
  border: none;
  background: transparent;
  font-size: 15px;
  line-height: 1.4;
  resize: none;
  outline: none;
  font-family: inherit;
  max-height: 100px;
}

.send-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 50%;
  background: var(--primary, #3b82f6);
  color: white;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}

.send-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Transitions */
.fade-enter-active, .fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from, .fade-leave-to {
  opacity: 0;
}

/* Safe Area */
@supports (padding: max(0px)) {
  .m-header {
    padding-top: max(12px, env(safe-area-inset-top));
  }
  .input-area {
    padding-bottom: max(10px, env(safe-area-inset-bottom));
  }
}

/* Dark Mode */
@media (prefers-color-scheme: dark) {
  :root {
    --primary: #60a5fa;
    --bg: #0f172a;
    --surface: #1e293b;
    --text: #f1f5f9;
    --text-secondary: #94a3b8;
    --border: #334155;
  }
}
</style>
