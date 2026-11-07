<script setup lang="ts">
import { computed } from 'vue'
import { useMultiAgentStore } from '../stores/multiAgent'
import { useChatSettingsStore, TTS_LANGUAGES } from '../stores/chatSettings'

const emit = defineEmits<{ close: [] }>()

const agent = useMultiAgentStore()
const chatSettings = useChatSettingsStore()

const messages = computed(() => agent.messages)
const autoRead = computed(() => chatSettings.autoRead)

function handleClearConversation() {
  if (!confirm('确定清空当前对话？此操作不可撤销。')) return
  agent.deleteConversation()
  emit('close')
}
</script>

<template>
  <div class="settings-panel">
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
  </div>
</template>

<style scoped src="../styles/chat/settingsPanel.css"></style>
