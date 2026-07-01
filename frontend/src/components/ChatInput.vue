<script setup lang="ts">
import { ref } from 'vue'

const emit = defineEmits<{ send: [text: string] }>()
const props = defineProps<{ loading: boolean }>()

const text = ref('')

function handleSend() {
  const msg = text.value.trim()
  if (!msg || props.loading) return
  emit('send', msg)
  text.value = ''
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}
</script>

<template>
  <div class="chat-input">
    <textarea
      v-model="text"
      :disabled="loading"
      placeholder="Ask a question about your knowledge base..."
      rows="2"
      @keydown="onKeydown"
    ></textarea>
    <button
      class="btn btn-primary send-btn"
      :disabled="loading || !text.trim()"
      @click="handleSend"
    >
      <span v-if="loading" class="spinner"></span>
      <span v-else>Send</span>
    </button>
  </div>
</template>

<style scoped>
.chat-input {
  display: flex;
  gap: 12px;
  padding: 16px 24px;
  background: var(--surface);
  border-top: 1px solid var(--border);
}
.chat-input textarea {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  resize: none;
  font-size: 14px;
  line-height: 1.5;
  outline: none;
  transition: border-color 0.15s;
}
.chat-input textarea:focus { border-color: var(--primary); }
.send-btn { align-self: flex-end; height: 40px; }
</style>
