<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useMultiAgentStore } from '../stores/multiAgent'
import { useChatSettingsStore, TTS_LANGUAGES } from '../stores/chatSettings'
import { usePermissionStore } from '../stores/permission'
import DirPickerModal from './DirPickerModal.vue'

const emit = defineEmits<{ close: [] }>()

const agent = useMultiAgentStore()
const chatSettings = useChatSettingsStore()
const perm = usePermissionStore()

const wsInput = ref('')
const wsBusy = ref(false)
const wsError = ref('')
const showDirPicker = ref(false)

const messages = computed(() => agent.messages)
const autoRead = computed(() => chatSettings.autoRead)

async function addWhitelist(create = false) {
  const path = wsInput.value.trim()
  if (!path) {
    wsError.value = '请输入已存在的绝对路径'
    return
  }
  wsBusy.value = true
  wsError.value = ''
  try {
    await perm.addWorkspace(path, create)
    wsInput.value = ''
  } catch (e: any) {
    const msg = String(e?.message || '添加目录失败')
    if (!create && msg.includes('directory_not_found')) {
      if (confirm(`目录不存在：\n${path}\n\n是否创建该目录并加入白名单？`)) {
        await addWhitelist(true)
        return
      }
      wsError.value = '目录不存在'
    } else {
      wsError.value = msg.replace(/^Add workspace error:\s*/, '')
    }
  } finally {
    wsBusy.value = false
  }
}

async function removeWhitelist(path: string) {
  wsBusy.value = true
  wsError.value = ''
  try {
    await perm.removeWorkspace(path)
  } catch (e: any) {
    wsError.value = e?.message || '移除目录失败'
  } finally {
    wsBusy.value = false
  }
}

function handleDirPick(path: string) {
  wsInput.value = path
  showDirPicker.value = false
  wsError.value = ''
}

function handleClearConversation() {
  if (!confirm('确定清空当前对话？此操作不可撤销。')) return
  agent.deleteConversation()
  emit('close')
}

onMounted(() => {
  perm.loadWorkspaces()
})
</script>

<template>
  <div class="settings-panel">
    <!-- 工作目录白名单：用户手动授权、全局免审的额外目录 -->
    <div class="drawer-section">
      <div class="drawer-label">工作目录白名单</div>
      <div class="ws-row">
        <button class="ws-pick-btn" title="选择目录" @click="showDirPicker = true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        </button>
        <input v-model="wsInput" class="ws-input" placeholder="选择或输入绝对路径" @keyup.enter="addWhitelist()" />
        <button class="ws-add" :disabled="wsBusy" @click="addWhitelist()">
          {{ wsBusy ? '处理中...' : '添加' }}
        </button>
      </div>
      <p v-if="wsError" class="ws-error">{{ wsError }}</p>
      <ul v-if="perm.workspaces.length" class="ws-list">
        <li v-for="w in perm.workspaces" :key="w" class="ws-item">
          <span class="ws-dot"></span>
          <span class="ws-path" :title="w">{{ w }}</span>
          <button class="ws-remove" :disabled="wsBusy" title="移出白名单" @click="removeWhitelist(w)">×</button>
        </li>
      </ul>
      <p v-else class="ws-empty">尚未添加目录；当前仅默认临时目录与程序内部数据目录免审。</p>
      <p class="ws-empty">白名单内目录免人工审批；其余路径一律询问。改动下一回合生效。</p>
    </div>


    <!-- 选项 -->
    <div class="drawer-section">
      <div class="drawer-label">选项</div>
      <div class="toggle-row">
        <span class="toggle-row-label">知识库检索</span>
        <label class="toggle">
          <input type="checkbox" v-model="agent.useVectorDb" :disabled="agent.loading" />
          <span class="toggle-slider"></span>
        </label>
      </div>
      <div class="toggle-row">
        <span class="toggle-row-label">自动朗读回复</span>
        <label class="toggle">
          <input type="checkbox" :checked="autoRead" :disabled="agent.loading" @change="chatSettings.autoRead = ($event.target as HTMLInputElement).checked" />
          <span class="toggle-slider"></span>
        </label>
      </div>
      <div class="toggle-row">
        <span class="toggle-row-label">朗读语言</span>
        <select v-model="chatSettings.ttsLang" class="drawer-select tts-lang-select" :disabled="agent.loading">
          <option v-for="l in TTS_LANGUAGES" :key="l.value" :value="l.value">{{ l.label }}</option>
        </select>
      </div>
    </div>

    <!-- 危险操作 -->
    <div class="drawer-section">
      <button
        class="drawer-danger"
        :disabled="agent.loading || messages.length === 0"
        @click="handleClearConversation"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
        清空当前对话
      </button>
    </div>

    <DirPickerModal :show="showDirPicker" @close="showDirPicker = false" @select="handleDirPick" />
  </div>
</template>

<style scoped src="../styles/chat/settingsPanel.css"></style>
