<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useMultiAgentStore } from '../stores/multiAgent'
import { useChatSettingsStore, TTS_LANGUAGES } from '../stores/chatSettings'
import { updateSession } from '../api/sessions'
import DirPickerModal from './DirPickerModal.vue'
import WeatherAlert from './WeatherAlert.vue'

const emit = defineEmits<{ close: [] }>()

const agent = useMultiAgentStore()
const chatSettings = useChatSettingsStore()

const wsInput = ref(agent.sessionDirectory || '')
const wsBusy = ref(false)
const wsError = ref('')
const showDirPicker = ref(false)
const isWeatherEnabled = ref(false)
const showWeather = ref(false)

const messages = computed(() => agent.messages)
const autoRead = computed(() => chatSettings.autoRead)

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

async function applyDirectory() {
  const path = wsInput.value.trim()
  if (!path) {
    wsError.value = '请输入已存在的绝对路径'
    return
  }
  wsBusy.value = true
  wsError.value = ''
  try {
    if (agent.conversationId) {
      const info = await updateSession(agent.conversationId, { directory: path })
      agent.setSessionDirectory(info.directory || path)
    } else {
      agent.setSessionDirectory(path)
    }
    wsInput.value = agent.sessionDirectory
  } catch (e: any) {
    wsError.value = e?.message || '设置工作目录失败'
  } finally {
    wsBusy.value = false
  }
}

async function resetDirectory() {
  wsBusy.value = true
  wsError.value = ''
  try {
    if (agent.conversationId) {
      const info = await updateSession(agent.conversationId, { directory: '' })
      agent.setSessionDirectory(info.directory || '')
    } else {
      agent.setSessionDirectory('')
    }
    wsInput.value = agent.sessionDirectory
  } catch (e: any) {
    wsError.value = e?.message || '恢复默认目录失败'
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
  wsInput.value = agent.sessionDirectory || ''
  checkWeatherPlugin()
})
</script>

<template>
  <div class="settings-panel">
    <!-- 当前会话工作目录：留空恢复系统临时目录 -->
    <div class="drawer-section">
      <div class="drawer-label">会话工作目录</div>
      <div class="ws-row">
        <button class="ws-pick-btn" title="选择目录" @click="showDirPicker = true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
        </button>
        <input v-model="wsInput" class="ws-input" placeholder="选择目录或留空使用系统临时目录" @keyup.enter="applyDirectory" />
        <button class="ws-add" :disabled="wsBusy" @click="applyDirectory">
          {{ wsBusy ? '应用中...' : '应用' }}
        </button>
        <button class="ws-add" :disabled="wsBusy" @click="resetDirectory">恢复默认</button>
      </div>
      <p v-if="wsError" class="ws-error">{{ wsError }}</p>
      <p v-if="agent.sessionDirectory" class="drawer-session-dir" :title="agent.sessionDirectory">当前：{{ agent.sessionDirectory }}</p>
      <p v-else class="ws-empty">默认使用系统临时目录下的独立会话目录；仅当前会话生效。</p>
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

    <!-- 辅助工具 -->
    <div v-if="isWeatherEnabled" class="drawer-section">
      <div class="drawer-label">辅助工具</div>
      <button class="drawer-row-btn" @click="showWeather = true">
        <span class="drawer-row-icon">🌤️</span>
        <span class="drawer-row-text">天气预警</span>
        <span class="drawer-row-chevron">›</span>
      </button>
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
    <WeatherAlert v-if="isWeatherEnabled" :show="showWeather" @update:show="showWeather = $event" />
  </div>
</template>

<style scoped src="../styles/chat/settingsPanel.css"></style>
