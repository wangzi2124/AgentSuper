<script setup lang="ts">
import type { Message } from '../types'

defineProps<{ message: Message }>()
</script>

<template>
  <div class="chat-message" :class="message.role">
    <div class="avatar">
      {{ message.role === 'user' ? '👤' : '🤖' }}
    </div>
    <div class="bubble">
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
      <div class="time">{{ message.timestamp.toLocaleTimeString() }}</div>
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
.time {
  font-size: 11px; color: var(--text-secondary); margin-top: 6px; opacity: 0.7;
}
.user .time { color: rgba(255,255,255,0.6); }
</style>
