<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import type { FileContent } from '../types'

// 定义组件事件：发送消息（含可选附件）、取消请求
const emit = defineEmits<{ send: [text: string, files: FileContent[]]; cancel: [] }>()
// 定义组件属性：加载状态
const props = defineProps<{ loading: boolean }>()

// [F8] 待发送附件：data 为 base64 编码内容（对齐后端 FileContent），
// 图片额外保留 data URL 预览用
interface PendingFile extends FileContent {
  id: string
  preview?: string
  size?: number
}

// 输入框文本内容
const text = ref('')
// [F8] 待发送附件列表
const files = ref<PendingFile[]>([])

// [F9] 已发送历史导航：sentHistory 最新在前（索引 0 = 最近一条）
// historyIndex -1 = 当前输入，0..len-1 = 回溯历史；draft 保存离开时的草稿
const sentHistory = ref<string[]>([])
const historyIndex = ref(-1)
const draft = ref('')

// 消息长度上限（与后端 ChatRequest.max_length=50_000 对齐，双层约束）
const MAX_LENGTH = 50_000

// 剩余可输入字符数
const remaining = computed(() => MAX_LENGTH - text.value.length)
// 剩余不足 5000 字符时显示计数器，不足 1000 时红色告警
const showCounter = computed(() => remaining.value < 5000)
const counterDanger = computed(() => remaining.value < 1000)

// F10: 输入框占位文案（loading 时为动态处理文案），修复模板引用未定义 computed
const placeholder = computed(() =>
  props.loading ? '正在处理，请稍候…' : '输入消息，Enter 发送，Shift+Enter 换行'
)

// F3: textarea 自动增高（min 80px / max 200px）
function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  const clamped = Math.min(Math.max(el.scrollHeight, 80), 200)
  el.style.height = clamped + 'px'
}

// [F8] 读取 File → base64（对齐后端 FileContent.data），图片额外生成 data URL 预览
function readFile(file: File): Promise<PendingFile> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = String(reader.result || '').split(',')[1] || ''
      const pending: PendingFile = {
        id: genId(), filename: file.name, mime_type: file.type || 'application/octet-stream',
        data: base64, size: file.size,
      }
      if (file.type.startsWith('image/')) {
        pending.preview = reader.result as string
      }
      resolve(pending)
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

// [预览] base64 → Uint8Array
function base64ToBytes(base64: string): Uint8Array {
  const bin = atob(base64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes
}

// [预览] base64 → UTF-8 文本
function decodeText(base64: string): string {
  try {
    return new TextDecoder('utf-8').decode(base64ToBytes(base64))
  } catch {
    return ''
  }
}

const TEXT_EXTS = ['txt', 'md', 'markdown', 'json', 'csv', 'log', 'js', 'ts', 'py', 'xml', 'html', 'yml', 'yaml', 'ini', 'conf', 'sql', 'sh', 'css', 'toml']

function isTextLike(f: PendingFile): boolean {
  if (f.mime_type.startsWith('text/')) return true
  const ext = (f.filename.split('.').pop() || '').toLowerCase()
  return TEXT_EXTS.includes(ext)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// [预览] 附件预览浮层
const previewFile = ref<PendingFile | null>(null)
const previewText = computed(() => {
  const f = previewFile.value
  if (!f || !isTextLike(f)) return ''
  const txt = decodeText(f.data)
  return txt.length > 20000 ? `${txt.slice(0, 20000)}\n\n…（内容过长，仅预览前 20000 字符）` : txt
})
function openPreview(f: PendingFile) { previewFile.value = f }
function closePreview() { previewFile.value = null }
function downloadPending(f: PendingFile) {
  try {
    const blob = new Blob([base64ToBytes(f.data) as BlobPart], { type: f.mime_type })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = f.filename
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch {
    // 下载失败静默
  }
}

function genId(): string {
  try { return crypto.randomUUID() }
  catch { return 'xxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16) }) }
}

// [F8] 加入文件（去重同名同大小 + 上限保护）
async function addFiles(list: FileList | File[] | null) {
  if (!list || props.loading) return
  const incoming = Array.from(list)
  for (const f of incoming) {
    // 简单跳空目录占位（File 无 .size===0 但 type 为空且 name 为空视为目录）
    if (!f.name) continue
    // 去重：同名文件不重复添加
    if (files.value.some(p => p.filename === f.name)) continue
    // 后端/链路尽力而为，这里对单个文件大小做硬上限保护（50MB）
    if (f.size > 50 * 1024 * 1024) continue
    try {
      const pending = await readFile(f)
      files.value = [...files.value, pending]
    } catch {
      // 读取失败静默跳过
    }
  }
}

// [F8] 移除附件
function removeFile(id: string) {
  files.value = files.value.filter(f => f.id !== id)
}

// 发送消息
function handleSend() {
  if (props.loading) return
  // F5: 仅用 trim() 判空，发送原文（保留首尾空白/换行）
  const msg = text.value
  // [F8] 文本与附件皆空则不发送
  if (!msg.trim() && files.value.length === 0) return
  emit('send', msg, files.value.map(({ id: _id, preview: _pv, size: _sz, ...fc }) => fc))
  // [F9] 记录已发送历史（最新在前），重置导航指针
  if (msg.trim()) {
    sentHistory.value = [msg, ...sentHistory.value.filter(m => m !== msg)].slice(0, 50)
  }
  historyIndex.value = -1
  draft.value = ''
  text.value = ''
  files.value = []
  // F3: 发送后重置高度
  nextTick(() => autoResize())
  // F6: 发送后焦点恢复
  nextTick(() => textareaRef.value?.focus())
}

// 取消当前请求
function handleCancel() {
  emit('cancel')
}

// [F8] 拖拽上传
function onDrop(e: DragEvent) {
  dragging.value = false
  if (props.loading) return
  addFiles(e.dataTransfer?.files || null)
}
function onDragOver(e: DragEvent) {
  if (props.loading) return
  e.preventDefault()
  dragging.value = true
}
function onDragLeave() { dragging.value = false }

// [F8] 粘贴上传（图片/文件）
function onPaste(e: ClipboardEvent) {
  if (props.loading) return
  const items = e.clipboardData?.items
  if (!items || items.length === 0) return
  const dropped: File[] = []
  for (const item of Array.from(items)) {
    const f = item.getAsFile?.()
    if (f) dropped.push(f)
  }
  if (dropped.length) {
    e.preventDefault()
    addFiles(dropped)
  }
}

// [F8] 文件选择器
const fileInputRef = ref<HTMLInputElement>()
function triggerFilePicker() {
  if (props.loading) return
  fileInputRef.value?.click()
}
function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  addFiles(input.files || null)
  input.value = ''
}

const dragging = ref(false)
const canSend = computed(() => !!(text.value.trim() || files.value.length))

// [F9] 历史导航提示：是否处于历史回看状态，及当前位置「N / 总数」
const historyActive = computed(() => historyIndex.value !== -1)
const historyPosition = computed(() =>
  historyActive.value ? `${historyIndex.value + 1} / ${sentHistory.value.length}` : ''
)
// 退出历史回看，回到草稿
function exitHistory() {
  historyIndex.value = -1
  text.value = draft.value
  nextTick(() => autoResize())
  nextTick(() => textareaRef.value?.focus())
}

// [F9] 历史消息导航：↑ 回溯上一条、↓ 前进（最新在前，索引 0 = 最近一条）
function navigateHistory(step: number) {
  const len = sentHistory.value.length
  if (!len) return
  // 从当前输入开始时，先保存草稿
  if (historyIndex.value === -1) draft.value = text.value
  let next = historyIndex.value + step
  next = Math.min(Math.max(next, -1), len - 1)
  historyIndex.value = next
  text.value = next === -1 ? draft.value : (sentHistory.value[next] || '')
  nextTick(() => autoResize())
  nextTick(() => {
    const el = textareaRef.value
    if (el) el.setSelectionRange(el.value.length, el.value.length)
  })
}

// 键盘事件处理：回车发送，Shift+回车换行；↑/↓ 历史导航
// F1 修复：IME 合成期间（isComposing / keyCode 229）的 Enter 直接忽略，
// 避免中文输入法选词/组句时误触发发送。
function onKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
    return
  }
  const el = textareaRef.value
  // F9：仅当光标位于首行（↑）或末行末尾（↓）时才拦截，避免影响多行文本内光标移动
  if (e.key === 'ArrowUp' && el && el.selectionStart === 0 && el.selectionEnd === 0) {
    e.preventDefault()
    navigateHistory(-1)
  } else if (e.key === 'ArrowDown' && el && el.selectionStart === el.value.length) {
    e.preventDefault()
    navigateHistory(1)
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
  <div class="chat-input" :class="{ dragging }" @dragover.prevent="onDragOver" @dragleave="onDragLeave" @drop.prevent="onDrop">
    <!-- [F9] 历史导航提示条：有历史时提示 ↑/↓ 浏览；回看中显示位置可退出 -->
    <div v-if="sentHistory.length" class="history-hint" :class="{ 'history-hint--active': historyActive }">
      <template v-if="historyActive">
        <span class="history-hint__pos">历史 {{ historyPosition }}</span>
        <button class="history-hint__exit" @click="exitHistory">退出历史</button>
      </template>
      <template v-else>
        <span>↑ / ↓ 可浏览历史消息</span>
      </template>
    </div>
    <!-- [F8] 附件预览列表（点击查看大图/内容） -->
    <div v-if="files.length" class="file-list">
      <div v-for="f in files" :key="f.id" class="file-chip" @click="openPreview(f)">
        <img v-if="f.preview" :src="f.preview" class="file-thumb" alt="" />
        <span v-else class="file-icon">📄</span>
        <span class="file-name" :title="f.filename">{{ f.filename }}</span>
        <button class="file-remove" title="移除附件" @click.stop="removeFile(f.id)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
    </div>

    <!-- [预览] 附件预览浮层：图片大图 / 文本内容 / 不支持提示 + 下载 -->
    <div v-if="previewFile" class="preview-overlay" @click.self="closePreview">
      <div class="preview-panel" @click.stop>
        <div class="preview-head">
          <span class="preview-title" :title="previewFile.filename">{{ previewFile.filename }}</span>
          <button class="preview-close" title="关闭" @click="closePreview">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="preview-body">
          <img v-if="previewFile.preview" :src="previewFile.preview" class="preview-img" alt="" />
          <pre v-else-if="previewText" class="preview-text">{{ previewText }}</pre>
          <div v-else class="preview-nosupport">
            <div class="preview-nosupport-icon">📄</div>
            <p>该格式暂不支持直接预览</p>
            <p class="preview-nosupport-sub">可直接发送给模型分析，或点击下方下载查看</p>
          </div>
        </div>
        <div class="preview-foot">
          <span class="preview-meta">{{ previewFile.mime_type }}{{ previewFile.size ? ` · ${formatSize(previewFile.size)}` : '' }}</span>
          <button class="preview-dl" @click="downloadPending(previewFile)">下载</button>
        </div>
      </div>
    </div>
    <div class="textarea-wrapper">
      <textarea
        ref="textareaRef"
        v-model="text"
        :maxlength="MAX_LENGTH"
        :placeholder="placeholder"
        rows="3"
        @keydown="onKeydown"
        @input="autoResize"
        @paste="onPaste"
      ></textarea>
      <span
        v-if="showCounter"
        class="char-counter"
        :class="{ 'char-counter--danger': counterDanger }"
      >{{ remaining }}</span>
      <button class="attach-btn" :disabled="loading" title="添加附件" @click="triggerFilePicker">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
      </button>
      <input
        ref="fileInputRef"
        type="file"
        multiple
        style="display: none"
        @change="onFileInput"
      />
      <button
        v-if="!loading"
        class="send-btn"
        :disabled="!canSend"
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
/* [F9] 历史导航提示条 */
.history-hint {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
  padding: 4px 10px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--text-muted, #9ca3af);
  background: color-mix(in srgb, var(--primary, #4f46e5) 6%, var(--bg, #fff));
  border: 1px solid color-mix(in srgb, var(--primary, #4f46e5) 20%, var(--border));
  border-radius: var(--radius);
}
.history-hint--active {
  color: var(--primary, #4f46e5);
  font-weight: 500;
}
.history-hint__pos {
  flex: 1;
}
.history-hint__exit {
  border: none;
  background: transparent;
  color: var(--primary, #4f46e5);
  font-size: 12px;
  cursor: pointer;
  padding: 0;
}
.history-hint__exit:hover {
  text-decoration: underline;
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

/* [F8] 附件相关样式 */
.chat-input { position: relative; }
.chat-input.dragging .textarea-wrapper textarea {
  border-color: var(--primary, #4f46e5);
  background: color-mix(in srgb, var(--primary, #4f46e5) 6%, var(--bg, #fff));
}
.file-list {
  display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px;
}
.file-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 6px 4px 4px;
  border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg); max-width: 220px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.file-chip:hover { border-color: var(--primary, #4f46e5); box-shadow: 0 0 0 1px var(--primary, #4f46e5); }
.file-thumb {
  width: 32px; height: 32px; object-fit: cover;
  border-radius: 6px; flex-shrink: 0;
}
.file-icon { font-size: 20px; line-height: 1; flex-shrink: 0; }
.file-name {
  font-size: 12px; color: var(--text);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 130px;
}
.file-remove {
  display: flex; align-items: center; justify-content: center;
  width: 18px; height: 18px; border: none; border-radius: 50%;
  background: transparent; color: var(--text-muted, #9ca3af);
  cursor: pointer; flex-shrink: 0; padding: 0;
}
.file-remove:hover { background: var(--danger, #dc2626); color: #fff; }
.attach-btn {
  position: absolute; left: 8px; bottom: 8px;
  width: 32px; height: 32px; border-radius: 8px;
  display: flex; align-items: center; justify-content: center;
  border: none; background: transparent; color: var(--text-secondary);
  cursor: pointer; transition: all 0.15s;
}
.attach-btn:hover:not(:disabled) { color: var(--primary); background: color-mix(in srgb, var(--primary, #4f46e5) 10%, transparent); }
.attach-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.textarea-wrapper textarea { padding-left: 44px; }

/* [预览] 附件预览浮层 */
.preview-overlay {
  position: fixed; inset: 0; z-index: 1000;
  display: flex; align-items: center; justify-content: center;
  padding: 24px;
  background: rgba(15, 23, 42, 0.55);
  backdrop-filter: blur(2px);
  -webkit-backdrop-filter: blur(2px);
}
.preview-panel {
  display: flex; flex-direction: column;
  width: 100%; max-width: 560px;
  max-height: 80vh;
  background: var(--surface, #fff);
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.3);
}
.preview-head {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 12px 16px;
  border-bottom: 1px solid var(--border, #eef1f6);
}
.preview-title {
  font-size: 14px; font-weight: 600; color: var(--text, #1e293b);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.preview-close {
  display: flex; align-items: center; justify-content: center;
  width: 28px; height: 28px; border: none; border-radius: 50%;
  background: var(--bg, #f8fafc); color: var(--text-secondary, #64748b);
  cursor: pointer; flex-shrink: 0;
}
.preview-close:hover { background: var(--danger, #dc2626); color: #fff; }
.preview-body {
  flex: 1; min-height: 0; overflow: auto;
  display: flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, var(--bg, #f8fafc) 60%, #fff);
  padding: 12px;
}
.preview-img {
  max-width: 100%; max-height: 56vh;
  object-fit: contain; border-radius: 8px;
}
.preview-text {
  width: 100%; margin: 0; padding: 12px;
  font-family: 'Cascadia Code', Consolas, Menlo, monospace;
  font-size: 12px; line-height: 1.6;
  color: var(--text, #1e293b);
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: 8px;
  white-space: pre-wrap; word-break: break-all;
  align-self: stretch;
}
.preview-nosupport { text-align: center; padding: 24px 16px; color: var(--text-secondary, #64748b); }
.preview-nosupport-icon { font-size: 40px; margin-bottom: 8px; }
.preview-nosupport p { margin: 4px 0; font-size: 13px; }
.preview-nosupport-sub { font-size: 12px; opacity: 0.8; }
.preview-foot {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 16px;
  border-top: 1px solid var(--border, #eef1f6);
}
.preview-meta {
  font-size: 12px; color: var(--text-muted, #9ca3af);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.preview-dl {
  height: 28px; padding: 0 16px; border: none; border-radius: 8px;
  background: var(--primary, #4f46e5); color: #fff;
  font-size: 12px; font-weight: 600; cursor: pointer; flex-shrink: 0;
}
.preview-dl:hover { background: var(--primary-hover, #4338ca); }

/* 移动端适配（与 MultiAgentView 768px 断点对齐） */
@media (max-width: 768px) {
  .chat-input { padding: 10px 12px; padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px)); }
  .history-hint { margin-bottom: 6px; font-size: 11px; padding: 3px 8px; }
  .textarea-wrapper textarea { font-size: 16px; min-height: 64px; }
  .char-counter { right: 46px; bottom: 8px; }
  .send-btn, .cancel-btn, .attach-btn { bottom: 6px; }
  .file-chip { max-width: 100%; }
  .preview-overlay { padding: 12px; }
  .preview-panel { max-width: 100%; max-height: 86vh; }
  .preview-img { max-height: 60vh; }
}
</style>
