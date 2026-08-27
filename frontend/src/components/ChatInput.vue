<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'

// 定义组件事件：发送消息、取消请求
const emit = defineEmits<{ send: [text: string]; cancel: [] }>()
// 定义组件属性：加载状态
const props = defineProps<{ loading: boolean }>()

// 输入框文本内容
const text = ref('')

// 消息长度上限（与后端 ChatRequest.max_length=50_000 对齐，双层约束）
const MAX_LENGTH = 50_000

// 剩余可输入字符数
const remaining = computed(() => MAX_LENGTH - text.value.length)
// 剩余不足 5000 字符时显示计数器，不足 1000 时红色告警
const showCounter = computed(() => remaining.value < 5000)
const counterDanger = computed(() => remaining.value < 1000)

// F3: textarea 自动增高（min 80px / max 200px）
function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  const clamped = Math.min(Math.max(el.scrollHeight, 80), 200)
  el.style.height = clamped + 'px'
}

// 发送消息
function handleSend() {
  if (props.loading) return
  // F5: 仅用 trim() 判空，发送原文（保留首尾空白/换行）
  const msg = text.value
  if (!msg.trim()) return
  emit('send', msg)
  text.value = ''
  // F3: 发送后重置高度
  nextTick(() => autoResize())
  // F6: 发送后焦点恢复
  nextTick(() => textareaRef.value?.focus())
}

// 取消当前请求
function handleCancel() {
  emit('cancel')
}

// 键盘事件处理：回车发送，Shift+回车换行
// F1 修复：IME 合成期间（isComposing / keyCode 229）的 Enter 直接忽略，
// 避免中文输入法选词/组句时误触发发送。
function onKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// 设置输入框文本（供父组件调用）
function setText(val: string) {
  text.value = val
  nextTick(() => autoResize())
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
        :maxlength="MAX_LENGTH"
        placeholder="请输入关于知识库的问题..."
        rows="3"
        @keydown="onKeydown"
        @input="autoResize"
      ></textarea>
      <span
        v-if="showCounter"
        class="char-counter"
        :class="{ 'char-counter--danger': counterDanger }"
      >{{ remaining }}</span>
      <button
        v-if="!loading"
        class="send-btn"
        :disabled="!text.trim()"
        @click="handleSend"
        title="发送"
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
        title="取消"
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

.char-counter {
  position: absolute;
  right: 50px;
  bottom: 10px;
  font-size: 11px;
  line-height: 1;
  color: var(--text-muted, #9ca3af);
  pointer-events: none;
  user-select: none;
}
.char-counter--danger {
  color: var(--danger, #dc2626);
  font-weight: 600;
}

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
