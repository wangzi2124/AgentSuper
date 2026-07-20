<script setup lang="ts">
import { ref } from 'vue'

// 定义组件事件：发送消息、取消请求
const emit = defineEmits<{ send: [text: string]; cancel: [] }>()
// 定义组件属性：加载状态
const props = defineProps<{ loading: boolean }>()

// 输入框文本内容
const text = ref('')

// 发送消息
function handleSend() {
  const msg = text.value.trim()
  if (!msg || props.loading) return
  emit('send', msg)
  text.value = ''
}

// 取消当前请求
function handleCancel() {
  emit('cancel')
}

// 键盘事件处理：回车发送，Shift+回车换行
function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// 设置输入框文本（供父组件调用）
function setText(val: string) {
  text.value = val
}

// 暴露方法供父组件调用
defineExpose({ setText, focus: () => textareaRef.value?.focus() })

// 文本域元素引用
const textareaRef = ref<HTMLTextAreaElement>()
</script>

<template>
  <div class="chat-input">
    <div class="textarea-wrapper">
      <textarea
        ref="textareaRef"
        v-model="text"
        :disabled="loading"
        placeholder="Ask a question about your knowledge base..."
        rows="3"
        @keydown="onKeydown"
      ></textarea>
      <button
        v-if="!loading"
        class="send-btn"
        :disabled="!text.trim()"
        @click="handleSend"
        title="Send"
      >
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M12 4L12 20" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"/>
            <path d="M6 10L12 4L18 10" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
      </button>
      <button
        v-else
        class="cancel-btn"
        @click="handleCancel"
        title="Cancel"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <rect x="6" y="6" width="12" height="12" rx="2"/>
        </svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.chat-input {
  padding: 16px 24px;
  background: var(--surface);
  border-top: 1px solid var(--border);
}
.textarea-wrapper {
  position: relative;
}
.textarea-wrapper textarea {
  width: 100%;
  padding: 10px 44px 10px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  resize: none;
  font-size: 14px;
  line-height: 1.5;
  outline: none;
  transition: border-color 0.15s;
  background: var(--bg);
  color: var(--text);
  box-sizing: border-box;
  min-height: 80px;
}
.textarea-wrapper textarea:focus { border-color: var(--primary); }
.textarea-wrapper textarea:disabled { opacity: 0.6; cursor: not-allowed; }

.send-btn,
.cancel-btn {
  position: absolute;
  right: 8px;
  bottom: 8px;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  cursor: pointer;
  transition: all 0.15s;
}
.send-btn {
  background: var(--primary);
  color: white;
}
.send-btn:hover:not(:disabled) {
  background: var(--primary-hover, #4338ca);
  transform: scale(1.05);
}
.send-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.cancel-btn {
  background: var(--danger);
  color: white;
}
.cancel-btn:hover {
  background: var(--danger-hover, #dc2626);
  transform: scale(1.05);
}
</style>
