<script setup lang="ts">
import { ref, watch } from 'vue'
import { Marked } from 'marked'
import hljs from 'highlight.js/lib/common'
import { splitUnclosedFence } from '../utils/markdownFence'

const props = defineProps<{ text: string; streaming?: boolean }>()

function escapeHtml(s: string): string {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
}

function codeBlockHtml(text: string, lang: string, highlight: boolean): string {
  const langName = (lang || '').trim().split(/\s+/)[0] || 'text'
  const safeLang = langName.replace(/[^a-zA-Z0-9_+#-]/g, '')
  let body = escapeHtml(text)
  if (highlight && safeLang && hljs.getLanguage(safeLang)) {
    try { body = hljs.highlight(String(text), { language: safeLang }).value } catch { body = escapeHtml(text) }
  }
  return `<div class="md-code">
        <div class="md-code-head">
          <span class="md-code-lang">${escapeHtml(safeLang)}</span>
          <button type="button" class="md-copy" data-copy>复制</button>
        </div>
        <pre class="md-pre"><code class="hljs language-${safeLang}">${body}</code></pre>
      </div>`
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
      return codeBlockHtml(text, lang || '', true)
    },
  },
})

const root = ref<HTMLElement>()
const html = ref('')

// 渲染（流式期间已按 ~10fps 合并；未闭合代码块先纯文本，闭合后再高亮）
function render(src: string) {
  if (!src) {
    html.value = ''
    return
  }
  const split = props.streaming ? splitUnclosedFence(src) : null
  if (!split) {
    html.value = md.parse(src) as string
    return
  }
  const head = split.safe ? (md.parse(split.safe) as string) : ''
  const newline = split.tail.indexOf('\n')
  const fenceLine = newline >= 0 ? split.tail.slice(0, newline) : split.tail
  const body = newline >= 0 ? split.tail.slice(newline + 1) : ''
  const lang = fenceLine.replace(/^[ \t]{0,3}[`~]+/, '').trim()
  html.value = head + codeBlockHtml(body, lang, false)
}

watch([() => props.text, () => props.streaming], ([text]) => render(text), { immediate: true })

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

<style src="../styles/chat/markdownContent.css"></style>
