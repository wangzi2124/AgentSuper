<script setup lang="ts">
import { ref, watch } from 'vue'
import { Marked } from 'marked'
import hljs from 'highlight.js/lib/common'

const props = defineProps<{ text: string }>()

function escapeHtml(s: string): string {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

// ChatGPT 风格 Markdown 渲染：GFM + 单换行即换行；代码块带语法高亮 + 语言标签 + 复制按钮；丢弃原始 HTML（防 XSS）
const md = new Marked({
  gfm: true,
  breaks: true,
  async: false,
})

md.use({
  renderer: {
    html() {
      return ''
    },
    code({ text, lang }) {
      const langName = (lang || '').trim().split(/\s+/)[0] || 'text'
      const safeLang = langName.replace(/[^a-zA-Z0-9_+#-]/g, '')
      let body = escapeHtml(text)
      if (safeLang && hljs.getLanguage(safeLang)) {
        try { body = hljs.highlight(String(text), { language: safeLang }).value } catch { body = escapeHtml(text) }
      }
      return `<div class="md-code">
        <div class="md-code-head">
          <span class="md-code-lang">${escapeHtml(safeLang)}</span>
          <button type="button" class="md-copy" data-copy>复制</button>
        </div>
        <pre class="md-pre"><code class="hljs language-${safeLang}">${body}</code></pre>
      </div>`
    },
  },
})

const root = ref<HTMLElement>()
const html = ref('')

// 渲染（流式增量时重新解析，marked 对常见篇幅开销很小）
function render(src: string) {
  html.value = src ? (md.parse(src) as string) : ''
}

watch(() => props.text, (v) => render(v), { immediate: true })

// 复制按钮（事件委托）
function onCopy(e: Event) {
  const btn = (e.target as HTMLElement).closest('[data-copy]') as HTMLElement | null
  if (!btn || !root.value) return
  const container = btn.closest('.md-code')
  const code = container?.querySelector('.md-pre code')
  if (!code) return
  const text = code.textContent ?? ''
  const done = () => {
    const orig = btn.textContent
    btn.textContent = '已复制'
    window.setTimeout(() => { btn.textContent = orig }, 1200)
  }
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(done)
  } else {
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    try { document.execCommand('copy') } catch { /* noop */ }
    document.body.removeChild(ta)
    done()
  }
}
</script>

<template>
  <div
    ref="root"
    class="md"
    v-html="html"
    @click="onCopy"
  ></div>
</template>

<style scoped src="../styles/chat/markdownContent.css"></style>
