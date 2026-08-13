<script setup lang="ts">
import { computed, nextTick, ref, watch, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useChatStore, SUPPORTED_MODELS } from '../stores/chat'
import { usePermissionStore } from '../stores/permission'
import ChatMessage from '../components/ChatMessage.vue'
import ChatInput from '../components/ChatInput.vue'
import StepTaskList from '../components/StepTaskList.vue'
import WeatherAlert from '../components/WeatherAlert.vue'
import DirPickerModal from '../components/DirPickerModal.vue'

// 路由实例
const route = useRoute()
// 路由器实例
const router = useRouter()
// 聊天状态管理
const chat = useChatStore()
const perm = usePermissionStore()
// 工作目录管理
const showWsPanel = ref(false)
const wsInput = ref('')
const wsError = ref('')
const wsBusy = ref(false)
const showDirPicker = ref(false)
// 主工作区 = 列表第一项，其余为额外工作区
const extraWorkspaces = computed(() => perm.workspaces.length > 1 ? perm.workspaces.slice(1) : [])
const mainWorkspace = computed(() => perm.workspaces[0] || '')
// 消息列表容器引用
const parentRef = ref<HTMLElement>()
// 聊天输入框引用
const chatInputRef = ref<any>()
// 是否滚动到底部
const isNearBottom = ref(true)
// 是否启用天气插件
const isWeatherEnabled = ref(false)

// 计算属性：当前会话的消息列表
const messages = computed(() => chat.messages)

// 侦听消息数量变化，自动滚动到底部
watch(() => messages.value.length, async () => {
  await nextTick()
  if (isNearBottom.value && parentRef.value) {
    parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
  }
})

// 侦听步骤变化（工具调用过程中），自动滚动到底部
watch(() => chat.currentSteps.length, async () => {
  await nextTick()
  if (isNearBottom.value && parentRef.value) {
    parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
  }
})

// 侦听最后一条消息的内容变化（流式输出），自动滚动到底部
watch(
  () => {
    const msgs = messages.value
    if (msgs.length === 0) return ''
    return msgs[msgs.length - 1].content
  },
  async () => {
    await nextTick()
    if (isNearBottom.value && parentRef.value) {
      parentRef.value.scrollTo({ top: parentRef.value.scrollHeight, behavior: 'smooth' })
    }
  }
)

// 检查天气插件是否启用
async function checkWeatherPlugin() {
  try {
    const { addAuthHeaders } = await import('../api/fetch')
    const response = await fetch('/api/plugins/weather-alert/status', { headers: await addAuthHeaders() })
    if (response.ok) {
      const data = await response.json()
      isWeatherEnabled.value = data.enabled
    }
  } catch (e) {
    console.error('Failed to check weather plugin status:', e)
  }
}

// 组件挂载时加载指定会话或新建会话
onMounted(() => {
  const id = route.params.id as string
  if (id) {
    chat.loadConversation(id)
  }
  checkWeatherPlugin()
  perm.loadWorkspaces()
})

// 切换工作目录面板
function toggleWsPanel() {
  showWsPanel.value = !showWsPanel.value
  wsError.value = ''
}

// 添加额外工作区
async function handleAddWorkspace() {
  const path = wsInput.value.trim()
  if (!path) {
    wsError.value = '请输入绝对路径，如 F:\\tetris'
    return
  }
  wsBusy.value = true
  wsError.value = ''
  try {
    await perm.addWorkspace(path)
    wsInput.value = ''
  } catch (e: any) {
    wsError.value = e?.message || '添加失败'
  } finally {
    wsBusy.value = false
  }
}

// 移除额外工作区
async function handleRemoveWorkspace(path: string) {
  try {
    await perm.removeWorkspace(path)
  } catch (e: any) {
    wsError.value = e?.message || '移除失败'
  }
}

// 目录选择器选中后回填输入框
function handleDirPick(path: string) {
  wsInput.value = path
  showDirPicker.value = false
  wsError.value = ''
}

// 侦听路由参数变化，加载对应会话或新建会话
watch(() => route.params.id, (newId) => {
  if (newId) {
    chat.loadConversation(newId as string)
  } else {
    chat.newChat()
  }
})

// 滚动事件处理：检测是否滚动到底部附近
function onScroll() {
  const el = parentRef.value
  if (!el) return
  const threshold = 100
  isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

// 发送消息
function handleSend(text: string) {
  chat.send(text).then(() => {
    if (chat.conversationId && route.name !== 'ChatConversation') {
      router.push({ name: 'ChatConversation', params: { id: chat.conversationId } })
    }
  })
}

// 取消当前请求
function handleCancel() {
  chat.cancel()
}

// 复制消息文本（暂未实现）
function handleCopy(_text: string) {
}

// 撤销指定索引的消息，恢复到输入框
function handleUndo(index: number) {
  if (chat.loading) chat.cancel()
  const msgText = messages.value[index]?.content
  chat.undoMessage(index)
  if (msgText) {
    chatInputRef.value?.setText(msgText)
    nextTick(() => chatInputRef.value?.focus())
  }
}

// 步骤面板"撤销上一步"：停止当前 Agent 任务并回退到最近一条用户消息
function handleStepUndo() {
  if (chat.loading) chat.cancel()
  const msgs = messages.value
  let lastUserIdx = -1
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'user') {
      lastUserIdx = i
      break
    }
  }
  if (lastUserIdx >= 0) chat.undoMessage(lastUserIdx)
}

// 删除指定消息
function handleMessageDelete(messageId: string) {
  chat.deleteMessage(messageId)
}

// 重试指定消息
function handleMessageRetry(messageId: string) {
  chat.manualRetry(messageId)
}
</script>

<template>
  <div class="chat-view">
    <div class="chat-header">
      <div>
        <h2>知识库对话</h2>
        <p>基于 RAG + AI Agent 的智能问答</p>
        <p v-if="chat.sessionDirectory" class="session-dir" :title="chat.sessionDirectory">
          📁 {{ chat.sessionDirectory }}
        </p>
      </div>
      <div class="header-controls">
        <span v-if="chat.streamPhase === 'queued'" class="stream-badge queued">
          ⏳ 排队中 #{{ chat.queuePosition }}
        </span>
        <span v-else-if="chat.streamPhase === 'running'" class="stream-badge running">
          ● 流式传输中
        </span>
        <WeatherAlert v-if="isWeatherEnabled" />
        <div class="ws-manager">
          <button class="ws-btn" @click="toggleWsPanel" title="管理可写工作目录">
            📁 工作目录 ({{ extraWorkspaces.length }})
          </button>
          <div v-if="showWsPanel" class="ws-panel">
            <div class="ws-row">
              <button class="ws-pick-btn" title="选择目录" @click="showDirPicker = true">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
              </button>
              <input
                v-model="wsInput"
                class="ws-input"
                placeholder="F:\tetris"
                @keyup.enter="handleAddWorkspace"
              />
              <button class="ws-add" :disabled="wsBusy" @click="handleAddWorkspace">
                {{ wsBusy ? '添加中...' : '添加' }}
              </button>
            </div>
            <p v-if="wsError" class="ws-error">{{ wsError }}</p>
            <div class="ws-list">
              <div v-if="mainWorkspace" class="ws-item fixed" :title="mainWorkspace">
                <span class="ws-dot"></span>
                <span class="ws-path">{{ mainWorkspace }}</span>
                <span class="ws-tag">主</span>
              </div>
              <div v-for="w in extraWorkspaces" :key="w" class="ws-item">
                <span class="ws-dot"></span>
                <span class="ws-path">{{ w }}</span>
                <button class="ws-remove" title="移除" @click="handleRemoveWorkspace(w)">×</button>
              </div>
              <p v-if="extraWorkspaces.length === 0" class="ws-empty">
                无额外工作区。添加后 Agent 可写该路径（无需重启）。
              </p>
            </div>
          </div>
        </div>
        <label class="toggle">
          <input type="checkbox" v-model="chat.useVectorDb" :disabled="chat.loading" />
          <span class="toggle-slider"></span>
          <span class="toggle-label">向量库检索</span>
        </label>
        <div class="model-selector">
          <label for="model-select">模型：</label>
          <select id="model-select" v-model="chat.selectedModel" :disabled="chat.loading">
            <option v-for="m in SUPPORTED_MODELS" :key="m.value" :value="m.value">
              {{ m.label }}
            </option>
          </select>
        </div>
      </div>
    </div>

    <div class="chat-body">
      <div v-if="messages.length === 0" class="empty-state">
        <div class="icon">💬</div>
        <p>输入问题开始对话</p>
        <p class="hint">AI Agent 将检索相关文档，并基于知识库内容进行回答。</p>
      </div>

      <div v-else ref="parentRef" class="message-list" @scroll="onScroll">
        <div v-for="(msg, idx) in messages" :key="msg.id" class="message-wrapper">
          <ChatMessage
            :message="msg"
            :index="idx"
            @copy="handleCopy"
            @undo="handleUndo"
            @delete="handleMessageDelete"
            @retry="handleMessageRetry"
          />
        </div>
        <StepTaskList
          v-if="chat.loading"
          :steps="chat.currentSteps"
          :is-running="chat.loading"
          :on-undo="handleStepUndo"
          @undo="handleStepUndo"
        />
      </div>
    </div>

    <div class="chat-footer">
      <div v-if="chat.retryCountdown > 0" class="auto-retry-banner">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"></polyline>
          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
        </svg>
        <span>自动重试中... {{ chat.retryCountdown }}s</span>
        <button class="cancel-retry-btn" @click="chat.cancelAutoRetry()">取消</button>
      </div>
      <button v-if="messages.length > 0" class="btn btn-danger" @click="chat.deleteConversation()" style="margin: 0 24px 8px;" :disabled="chat.loading">
        Clear conversation
      </button>
      <ChatInput ref="chatInputRef" :loading="chat.loading" @send="handleSend" @cancel="handleCancel" />
    </div>

    <DirPickerModal :show="showDirPicker" @close="showDirPicker = false" @select="handleDirPick" />
  </div>
</template>

<style scoped>
.chat-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}
.chat-header h2 {
  margin: 0 0 2px;
  font-size: 20px;
}
.chat-header p {
  margin: 0;
  font-size: 13px;
  color: var(--text-secondary);
}
.chat-header p.session-dir {
  margin-top: 4px;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
  color: var(--primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 420px;
}
.header-controls {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
}
.stream-badge {
  font-size: 12px;
  padding: 3px 8px;
  border-radius: 6px;
  white-space: nowrap;
}
.stream-badge.queued {
  background: rgba(251, 191, 36, 0.12);
  color: #f59e0b;
}
.stream-badge.running {
  background: rgba(34, 197, 94, 0.12);
  color: #22c55e;
  animation: pulse-stream 1.5s ease-in-out infinite;
}
@keyframes pulse-stream {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.5; }
}
.chat-body {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
.empty-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  color: var(--text-secondary);
}
.empty-state .icon { font-size: 48px; }
.empty-state .hint { font-size: 13px; margin-top: 4px; }
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  position: relative;
  scroll-behavior: smooth;
}
.message-list::-webkit-scrollbar {
  width: 6px;
}
.message-list::-webkit-scrollbar-track {
  background: transparent;
}
.message-list::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 3px;
}
.message-list::-webkit-scrollbar-thumb:hover {
  background: var(--text-secondary);
}
.loading-indicator {
  display: flex;
  gap: 4px;
  padding: 16px 0;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-secondary);
  animation: bounce 1.4s infinite ease-in-out;
}
.dot:nth-child(2) { animation-delay: 0.2s; }
.dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes bounce {
  0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
  40% { opacity: 1; transform: scale(1); }
}
.chat-footer { flex-shrink: 0; }
.toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
  color: var(--text-secondary);
  user-select: none;
}
.toggle input { display: none; }
.toggle-slider {
  width: 36px; height: 20px;
  background: var(--border);
  border-radius: 10px;
  position: relative;
  transition: background 0.2s;
}
.toggle-slider::after {
  content: '';
  position: absolute;
  width: 16px; height: 16px;
  border-radius: 50%;
  background: #fff;
  top: 2px; left: 2px;
  transition: transform 0.2s;
}
.toggle input:checked + .toggle-slider { background: var(--primary, #4f46e5); }
.toggle input:checked + .toggle-slider::after { transform: translateX(16px); }
.toggle input:disabled + .toggle-slider { opacity: 0.5; }
.model-selector {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--text-secondary);
}
.model-selector select {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
  outline: none;
  cursor: pointer;
  min-width: 200px;
}
.model-selector select:focus { border-color: var(--primary); }
.ws-manager {
  position: relative;
  flex-shrink: 0;
}
.ws-btn {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}
.ws-btn:hover { border-color: var(--primary); }
.ws-panel {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 6px;
  width: 360px;
  max-width: 70vw;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.15);
  padding: 12px;
  z-index: 50;
}
.ws-row {
  display: flex;
  gap: 8px;
}
.ws-pick-btn {
  width: 32px;
  height: 32px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--surface);
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all 0.15s;
}
.ws-pick-btn:hover { border-color: var(--primary); color: var(--primary); }
.ws-input {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 13px;
  background: var(--surface);
  color: var(--text);
  outline: none;
}
.ws-input:focus { border-color: var(--primary); }
.ws-add {
  padding: 6px 12px;
  border: none;
  border-radius: var(--radius);
  background: var(--primary, #4f46e5);
  color: #fff;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}
.ws-add:disabled { opacity: 0.5; cursor: not-allowed; }
.ws-error { color: #ef4444; font-size: 12px; margin: 6px 0 0; }
.ws-list {
  margin-top: 10px;
  max-height: 200px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ws-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text);
}
.ws-item.fixed { opacity: 0.7; }
.ws-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #22c55e;
  flex-shrink: 0;
}
.ws-path {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'Cascadia Code', Consolas, monospace;
}
.ws-tag {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: rgba(79, 70, 229, 0.1);
  color: var(--primary, #4f46e5);
  flex-shrink: 0;
}
.ws-remove {
  border: none;
  background: transparent;
  color: var(--text-secondary);
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  padding: 0 2px;
  flex-shrink: 0;
}
.ws-remove:hover { color: #ef4444; }
.ws-empty { font-size: 12px; color: var(--text-secondary); margin: 0; }
.btn-danger {
  background: #ef4444;
  color: #fff;
  border: none;
  border-radius: var(--radius);
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
}
.btn-danger:hover { background: #dc2626; }
.btn-danger:disabled { opacity: 0.5; cursor: not-allowed; }
.auto-retry-banner {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 16px;
  margin: 0 24px 8px;
  background: rgba(239, 68, 68, 0.06);
  border: 1px solid rgba(239, 68, 68, 0.2);
  border-radius: var(--radius);
  font-size: 13px;
  color: #ef4444;
}
.auto-retry-banner svg {
  flex-shrink: 0;
  animation: spin 1s linear infinite;
}
@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
.cancel-retry-btn {
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid rgba(239, 68, 68, 0.3);
  background: transparent;
  color: #ef4444;
  font-size: 12px;
  cursor: pointer;
  transition: all 0.15s;
}
.cancel-retry-btn:hover {
  background: rgba(239, 68, 68, 0.1);
}
</style>
