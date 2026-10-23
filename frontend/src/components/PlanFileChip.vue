<script setup lang="ts">
import { ref } from 'vue'
import { showToast } from '../utils/toast'

const props = defineProps<{ path: string }>()

const copied = ref(false)
const pathEl = ref<HTMLElement | null>(null)

// 浏览器无法直接打开后端本地文件，故「点击复制 + 显式提示」。
// clipboard 在非安全上下文不可用 → 选中路径文本 + toast，让用户手动复制。
async function copy(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.path)
    copied.value = true
    showToast('已复制计划文件路径')
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    const el = pathEl.value
    if (el) {
      const range = document.createRange()
      range.selectNodeContents(el)
      const sel = window.getSelection()
      sel?.removeAllRanges()
      sel?.addRange(range)
    }
    showToast('已选中路径，请按 Ctrl+C 复制')
  }
}
</script>

<template>
  <div
    class="plan-file"
    role="button"
    tabindex="0"
    title="点击复制计划文件路径"
    @click="copy"
    @keyup.enter="copy"
  >
    <span class="pf-icon">📋</span>
    <span class="pf-label">已生成计划文件</span>
    <code ref="pathEl" class="pf-path">{{ path }}</code>
    <span class="pf-copy">{{ copied ? '已复制' : '点击复制路径' }}</span>
  </div>
</template>

<style scoped>
.plan-file {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--primary);
  border-radius: 12px;
  background: var(--surface-elevated);
  font-size: 13px;
  cursor: pointer;
}
.plan-file:hover { border-color: var(--primary); }
.pf-label {
  color: var(--text-secondary);
  font-weight: 600;
}
.pf-path {
  flex: 1 1 240px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--text-primary);
  user-select: all;
}
.pf-copy {
  padding: 3px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg);
  color: var(--primary);
  font-size: 12px;
}
.plan-file:hover .pf-copy { border-color: var(--primary); }
</style>
