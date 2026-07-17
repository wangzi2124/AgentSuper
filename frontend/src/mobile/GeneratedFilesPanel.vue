<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useGeneratedStore } from '../stores/generated'
import { getGeneratedContent } from '../api/generated'

const store = useGeneratedStore()
const deleting = ref<Set<string>>(new Set())
const runningFile = ref<string | null>(null)
const runOutput = ref<string | null>(null)
const runError = ref<string | null>(null)
const showOutput = ref(false)

onMounted(() => {
  store.fetchAll()
})

async function handleDelete(filename: string) {
  if (!confirm(`删除 "${filename}"?`)) return
  deleting.value.add(filename)
  try {
    await store.remove(filename)
  } catch (err: any) {
    alert(err.message)
  } finally {
    deleting.value.delete(filename)
  }
}

async function handleDownload(filename: string) {
  try {
    const url = `/api/generated/download/${encodeURIComponent(filename)}`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`下载失败: ${res.statusText}`)
    const blob = await res.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(a.href)
  } catch (err: any) {
    alert(err.message || '下载失败')
  }
}

function isJsFile(filename: string): boolean {
  return filename.endsWith('.js')
}

function getFileIcon(filename: string): string {
  if (filename.endsWith('.docx')) return '📝'
  if (filename.endsWith('.pdf')) return '📕'
  if (filename.endsWith('.xlsx') || filename.endsWith('.xls')) return '📊'
  if (filename.endsWith('.pptx') || filename.endsWith('.ppt')) return '📽️'
  if (filename.endsWith('.js')) return '⚡'
  if (filename.endsWith('.html')) return '🌐'
  if (filename.endsWith('.txt')) return '📄'
  return '📄'
}

async function handleRun(filename: string) {
  runningFile.value = filename
  runOutput.value = null
  runError.value = null
  showOutput.value = true
  
  try {
    const code = await getGeneratedContent(filename)
    const logs: string[] = []
    const writtenFiles: Record<string, string> = {}
    const mockConsole = {
      log: (...args: unknown[]) => logs.push(args.map(a => String(a)).join(' ')),
      warn: (...args: unknown[]) => logs.push('[warn] ' + args.map(a => String(a)).join(' ')),
      error: (...args: unknown[]) => logs.push('[error] ' + args.map(a => String(a)).join(' ')),
    }
    const mockRequire = (mod: string) => {
      if (mod === 'fs') {
        return {
          writeFileSync: (path: string, data: string) => {
            writtenFiles[path] = data
            logs.push(`[fs] wrote ${data.length} bytes to ${path}`)
          },
          readFileSync: () => '',
          existsSync: () => true,
          mkdirSync: () => {},
        }
      }
      if (mod === 'path') {
        return {
          join: (...args: string[]) => args.join('/'),
          resolve: (...args: string[]) => args.join('/'),
          dirname: (p: string) => p.split('/').slice(0, -1).join('/'),
          basename: (p: string) => p.split('/').pop() || '',
        }
      }
      throw new Error(`require('${mod}') is not supported`)
    }
    const fn = new Function('console', 'require', code)
    const result = fn(mockConsole, mockRequire)
    if (result !== undefined) {
      logs.push('=> ' + String(result))
    }
    const output = logs.join('\n') || '(无输出)'
    if (Object.keys(writtenFiles).length > 0) {
      runOutput.value = output + '\n\n--- 生成的文件 ---\n' +
        Object.entries(writtenFiles).map(([p, d]) => `${p} (${d.length} bytes)`).join('\n')
    } else {
      runOutput.value = output
    }
  } catch (err: any) {
    runError.value = err.message || String(err)
  }
}

function closeOutput() {
  showOutput.value = false
  runningFile.value = null
  runOutput.value = null
  runError.value = null
}
</script>

<template>
  <div class="m-files">
    <div class="search-bar">
      <input v-model="store.searchQuery" type="text" placeholder="搜索文件..." />
    </div>
    
    <div v-if="store.loading" class="loading">加载中...</div>
    
    <div v-else-if="store.filteredFiles.length === 0" class="empty">
      <div class="empty-icon">📝</div>
      <p>{{ store.searchQuery ? '没有匹配的文件' : '暂无生成的文件' }}</p>
      <p class="empty-hint">{{ store.searchQuery ? '尝试其他搜索词' : '让AI助手帮你创建文件' }}</p>
    </div>
    
    <div v-else class="file-list">
      <div class="file-count">{{ store.filteredFiles.length }} 个文件</div>
      
      <div v-for="f in store.filteredFiles" :key="f.filename" class="file-card">
        <div class="file-info">
          <span class="file-icon">{{ getFileIcon(f.filename) }}</span>
          <div class="file-details">
            <div class="file-name">{{ f.filename }}</div>
            <div class="file-meta">{{ store.formatSize(f.size) }}</div>
          </div>
        </div>
        <div class="file-actions">
          <button class="action-btn download" @click="handleDownload(f.filename)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>
            </svg>
          </button>
          <button v-if="isJsFile(f.filename)" class="action-btn run" @click="handleRun(f.filename)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polygon points="5 3 19 12 5 21 5 3"/>
            </svg>
          </button>
          <button class="action-btn delete" @click="handleDelete(f.filename)">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
            </svg>
          </button>
        </div>
      </div>
    </div>
    
    <!-- Output Modal -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showOutput" class="output-overlay" @click.self="closeOutput">
          <div class="output-modal">
            <div class="output-header">
              <span class="output-title">{{ runningFile }}</span>
              <button class="close-btn" @click="closeOutput">×</button>
            </div>
            <div class="output-body">
              <pre v-if="runOutput">{{ runOutput }}</pre>
              <pre v-else-if="runError" class="error">{{ runError }}</pre>
              <div v-else class="running">运行中...</div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.m-files {
  padding: 16px;
  padding-bottom: 100px;
}

.search-bar {
  margin-bottom: 16px;
}

.search-bar input {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 10px;
  font-size: 14px;
  background: var(--surface, #fff);
}

.loading, .empty {
  padding: 40px 20px;
  text-align: center;
  color: var(--text-secondary, #64748b);
}

.empty-icon {
  font-size: 48px;
  margin-bottom: 12px;
}

.empty-hint {
  font-size: 13px;
  margin-top: 4px;
}

.file-count {
  font-size: 12px;
  color: var(--text-secondary, #64748b);
  margin-bottom: 8px;
}

.file-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 10px;
  margin-bottom: 8px;
}

.file-info {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  flex: 1;
}

.file-icon {
  font-size: 24px;
}

.file-details {
  min-width: 0;
}

.file-name {
  font-size: 13px;
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-meta {
  font-size: 11px;
  color: var(--text-secondary, #64748b);
  margin-top: 2px;
}

.file-actions {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
}

.action-btn {
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  text-decoration: none;
}

.action-btn.download {
  background: var(--primary, #3b82f6);
  color: white;
}

.action-btn.run {
  background: #10b981;
  color: white;
}

.action-btn.delete {
  background: var(--bg, #f1f5f9);
  color: var(--text-secondary, #64748b);
}

.action-btn:hover {
  opacity: 0.9;
}

/* Output Modal */
.output-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: flex-end;
  z-index: 1000;
}

.output-modal {
  width: 100%;
  max-height: 70vh;
  background: var(--surface, #fff);
  border-radius: 16px 16px 0 0;
  display: flex;
  flex-direction: column;
}

.output-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}

.output-title {
  font-size: 14px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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

.output-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.output-body pre {
  margin: 0;
  font-family: 'SF Mono', Menlo, monospace;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.output-body .error {
  color: #ef4444;
}

.output-body .running {
  text-align: center;
  color: var(--text-secondary, #64748b);
}

.fade-enter-active, .fade-leave-active {
  transition: opacity 0.2s;
}

.fade-enter-from, .fade-leave-to {
  opacity: 0;
}
</style>
