<script setup lang="ts">
import { ref, watch } from 'vue'
import { useMobileChatStore, SUPPORTED_MODELS } from '../stores/mobileChat'

const chat = useMobileChatStore()
const localModel = ref(chat.selectedModel)
const localUseVector = ref(chat.useVectorDb)

watch(localModel, (v) => { chat.selectedModel = v })
watch(localUseVector, (v) => { chat.useVectorDb = v })

const emit = defineEmits<{
  (e: 'close'): void
}>()
</script>

<template>
  <div class="settings-panel">
    <div class="panel-header">
      <h3>设置</h3>
      <button class="close-btn" @click="emit('close')">×</button>
    </div>
    
    <div class="settings-content">
      <div class="setting-item">
        <label>AI 模型</label>
        <select v-model="localModel">
          <option v-for="m in SUPPORTED_MODELS" :key="m.value" :value="m.value">
            {{ m.label }}
          </option>
        </select>
      </div>
      
      <div class="setting-item">
        <label>知识库搜索</label>
        <div class="toggle-wrapper">
          <input type="checkbox" id="vector-toggle" v-model="localUseVector" class="toggle-input" />
          <label for="vector-toggle" class="toggle-label">
            <span class="toggle-slider"></span>
          </label>
          <span class="toggle-text">{{ localUseVector ? '开启' : '关闭' }}</span>
        </div>
      </div>
      
      <div class="setting-item">
        <button class="danger-btn" @click="chat.deleteConversation(); emit('close')">
          删除当前对话
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-panel {
  padding: 0;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px;
  border-bottom: 1px solid var(--border, #e2e8f0);
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

.settings-content {
  padding: 16px;
}

.setting-item {
  margin-bottom: 20px;
}

.setting-item:last-child {
  margin-bottom: 0;
}

.setting-item > label {
  display: block;
  font-size: 13px;
  font-weight: 500;
  color: var(--text-secondary, #64748b);
  margin-bottom: 8px;
}

.setting-item select {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  font-size: 14px;
  background: var(--surface, #fff);
}

.toggle-wrapper {
  display: flex;
  align-items: center;
  gap: 12px;
}

.toggle-input {
  display: none;
}

.toggle-label {
  position: relative;
  width: 44px;
  height: 24px;
  cursor: pointer;
}

.toggle-slider {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: var(--border, #e2e8f0);
  border-radius: 12px;
  transition: background 0.2s;
}

.toggle-slider::before {
  content: '';
  position: absolute;
  width: 20px;
  height: 20px;
  left: 2px;
  top: 2px;
  background: white;
  border-radius: 50%;
  transition: transform 0.2s;
}

.toggle-input:checked + .toggle-label .toggle-slider {
  background: var(--primary, #3b82f6);
}

.toggle-input:checked + .toggle-label .toggle-slider::before {
  transform: translateX(20px);
}

.toggle-text {
  font-size: 14px;
  color: var(--text, #1e293b);
}

.danger-btn {
  width: 100%;
  padding: 12px;
  border: 1px solid #ef4444;
  border-radius: 8px;
  background: transparent;
  color: #ef4444;
  font-size: 14px;
  cursor: pointer;
}

.danger-btn:hover {
  background: #fef2f2;
}
</style>
