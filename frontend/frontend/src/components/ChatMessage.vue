<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Message } from '../types'

const emit = defineEmits<{ copy: [text: string]; undo: [index: number] }>()
const props = defineProps<{ message: Message; index: number }>()

const copied = ref(false)

const thoughtDuration = computed(() => {
  if (!props.message.steps) return null
  const genStep = props.message.steps.find(s => s.step_id === 'generate' || s.name === '生成回答')
  return genStep?.duration_ms ?? null
})

async function handleCopy() {
  try {
    await navigator.clipboard.writeText(props.message.content)
    emit('copy', props.message.content)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {}
}

function handleUndo() {
  emit('undo', props.index)
}
</script>

<template>
  <div class="chat-message" :class="message.role">
    <div class="avatar">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>
    <div class="bubble">
      <div v-if="message.role === 'assistant' && thoughtDuration != null" class="thought-duration">
        Thought: {{ thoughtDuration }}ms
      </div>
      <div v-if="message.content" class="content">{{ message.content }}</div>
      <div v-if="message.files && message.files.length > 0" class="attachments">
        <div v-for="(f, i) in message.files" :key="i" class="attachment-badge">
          {{ f.filename }}
        </div>
      </div>
      <div v-if="message.sources && message.sources.length > 0" class="sources">
        <div class="sources-title">Sources:</div>
        <div v-for="(s, i) in message.sources" :key="i" class="source-item">
          <span class="source-score">{{ (s.score * 100).toFixed(0) }}%</span>
          <span class="source-text">{{ s.content.slice(0, 120) }}...</span>
        </div>
      </div>
      <div class="message-footer">
        <span class="time">{{ message.timestamp.toLocaleTimeString() }}</span>
        <div v-if="message.role === 'user'" class="message-actions">
          <div class="btn-wrapper">
            <button class="icon-btn" @click="handleCopy" title="复制">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
              </svg>
            </button>
            <span v-if="copied" class="copy-toast">复制成功</span>
          </div>
          <button class="icon-btn" @click="handleUndo" title="撤销到此处">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 7v6h6"></path>
              <path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"></path>
            </svg>
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-message {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.chat-message.user { flex-direction: row-reverse; }
.avatar {
  width: 36px; height: 36px;
  border-radius: 50%;
  background: var(--bg);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  flex-shrink: 0;
}
.bubble {
  max-width: 70%;
  padding: 12px 16px;
  border-radius: 16px;
  background: var(--surface);
  border: 1px solid var(--border);
  line-height: 1.6;
  font-size: 14px;
}
.user .bubble {
  background: var(--primary);
  color: white;
  border-color: var(--primary);
}
.content { white-space: pre-wrap; word-break: break-word; }
.attachments {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin: 6px 0 0;
}
.attachment-badge {
  font-size: 11px;
  padding: 2px 8px;
  background: rgba(255,255,255,0.15);
  border-radius: 10px;
  white-space: nowrap;
}
.user .attachment-badge { color: rgba(255,255,255,0.85); }
.sources {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}
.user .sources { border-top-color: rgba(255,255,255,0.2); }
.sources-title {
  font-size: 12px; font-weight: 600; margin-bottom: 6px; color: var(--text-secondary);
}
.user .sources-title { color: rgba(255,255,255,0.8); }
.source-item {
  display: flex; gap: 8px; align-items: flex-start;
  padding: 4px 0; font-size: 12px; color: var(--text-secondary);
}
.user .source-item { color: rgba(255,255,255,0.8); }
.source-score { font-weight: 600; color: var(--success); flex-shrink: 0; }
.source-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.message-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
  padding-top: 6px;
  border-top: 1px solid var(--border);
  font-size: 11px;
}
.user .message-footer { border-top-color: rgba(255,255,255,0.2); }
.time {
  color: var(--text-secondary);
  opacity: 0.7;
}
.user .time { color: rgba(255,255,255,0.6); }
.message-actions {
  display: flex;
  gap: 6px;
  align-items: center;
}
.btn-wrapper {
  position: relative;
  display: inline-flex;
}
.copy-toast {
  position: absolute;
  left: 50%;
  bottom: calc(100% + 4px);
  transform: translateX(-50%);
  white-space: nowrap;
  font-size: 11px;
  background: var(--text);
  color: var(--bg);
  padding: 2px 8px;
  border-radius: 4px;
  pointer-events: none;
  animation: fadeInOut 1.5s ease-in-out;
}
@keyframes fadeInOut {
  0% { opacity: 0; transform: translateX(-50%) translateY(4px); }
  15% { opacity: 1; transform: translateX(-50%) translateY(0); }
  85% { opacity: 1; transform: translateX(-50%) translateY(0); }
  100% { opacity: 0; transform: translateX(-50%) translateY(-4px); }
}
.icon-btn {
  width: 28px;
  height: 28px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: inherit;
  opacity: 0.5;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: all 0.15s;
}
.icon-btn:hover {
  opacity: 1;
  background: rgba(255,255,255,0.1);
}
.user .icon-btn:hover { background: rgba(255,255,255,0.2); }
</style>
