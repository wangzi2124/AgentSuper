<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useGeneratedStore } from '../stores/generated'
import { getGeneratedContent, downloadGenerated } from '../api/generated'

// 生成文件状态管理
const store = useGeneratedStore()
// 正在删除的文件名集合
const deleting = ref<Set<string>>(new Set())
// 当前正在运行的文件名
const runningFile = ref<string | null>(null)
// 运行输出内容
const runOutput = ref<string | null>(null)
// 运行错误信息
const runError = ref<string | null>(null)

// 组件挂载时加载所有生成文件
onMounted(() => {
  store.fetchAll()
})

// 删除指定生成文件
async function handleDelete(filename: string) {
  if (!confirm(`Delete "${filename}"?`)) return
  deleting.value.add(filename)
  try {
    await store.remove(filename)
  } catch (err: any) {
    alert(err.message)
  } finally {
    deleting.value.delete(filename)
  }
}

// 判断是否为JavaScript文件
function isJsFile(filename: string): boolean {
  return filename.endsWith('.js')
}

// 下载文件（带鉴权头，通过 blob 触发浏览器下载）
async function handleDownload(filename: string) {
  try {
    await downloadGenerated(filename)
  } catch (err: any) {
    alert(err.message || '下载失败')
  }
}

// 在浏览器沙箱中运行JavaScript文件
async function handleRun(filename: string) {
  runningFile.value = filename
  runOutput.value = null
  runError.value = null
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
          readFileSync: (path: string) => {
            logs.push(`[fs] read ${path} (returned empty mock)`)
            return ''
          },
          existsSync: () => true,
          mkdirSync: () => {},
        }
      }
      if (mod === 'path') {
        return {
          join: (...args: string[]) => args.join('/'),
          resolve: (...args: string[]) => args.join('/'),
          dirname: (p: string) => p.split('/').slice(0, -1).join('/'),
          basename: (p: string) => p.split('/').pop() || p,
        }
      }
      throw new Error(`require('${mod}') is not supported in browser. Try running this file on the server side.`)
    }
    const fn = new Function('console', 'require', code)
    const result = fn(mockConsole, mockRequire)
    if (result !== undefined) {
      logs.push('=> ' + String(result))
    }
    const output = logs.join('\n') || '(no output)'
    if (Object.keys(writtenFiles).length > 0) {
      runOutput.value = output + '\n\n--- Files written ---\n' +
        Object.entries(writtenFiles).map(([p, d]) => `${p} (${d.length} bytes)`).join('\n')
    } else {
      runOutput.value = output
    }
  } catch (err: any) {
    runError.value = err.message || String(err)
  }
}

// 关闭输出弹窗
function closeOutput() {
  runningFile.value = null
  runOutput.value = null
  runError.value = null
}
</script>

<template>
  <div class="page-header">
    <h2>生成文件</h2>
    <p>AI Agent 生成的文件</p>
  </div>
  <div class="page-content">
    <div class="search-bar">
      <input
        v-model="store.searchQuery"
        type="text"
        placeholder="按文件名搜索..."
        class="search-input"
      />
    </div>

    <div v-if="store.loading" class="loading-wrap">
      <span class="spinner"></span>
    </div>

    <div v-else-if="store.filteredFiles.length === 0" class="empty-state">
      <div class="icon">📝</div>
      <p>{{ store.searchQuery ? '没有匹配的文件' : '还没有生成文件' }}</p>
      <p style="font-size:13px;margin-top:4px;">
        {{ store.searchQuery ? '换个搜索词试试。' : '请让 AI Agent 创建文件。' }}
      </p>
    </div>

    <div v-else class="file-list">
      <div class="file-count">{{ store.filteredFiles.length }} 个文件</div>
      <div class="file-row" v-for="f in store.filteredFiles" :key="f.filename">
        <div class="file-info">
          <div class="file-thumb" :class="isJsFile(f.filename) ? 'js' : 'doc'">{{ isJsFile(f.filename) ? 'JS' : 'DOC' }}</div>
          <div class="file-details">
            <div class="file-name">{{ f.filename }}</div>
            <div class="file-meta">{{ store.formatSize(f.size) }}</div>
          </div>
        </div>
        <div class="file-actions">
          <button class="btn btn-sm" @click="handleDownload(f.filename)">下载</button>
          <button
            v-if="isJsFile(f.filename)"
            class="btn btn-run btn-sm"
            :disabled="runningFile === f.filename"
            @click="handleRun(f.filename)"
          >
            {{ runningFile === f.filename ? '运行中...' : '运行' }}
          </button>
          <button
            class="btn btn-sm btn-danger"
            :disabled="deleting.has(f.filename)"
            @click="handleDelete(f.filename)"
          >
            {{ deleting.has(f.filename) ? '删除中...' : '删除' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="runningFile" class="output-overlay" @click.self="closeOutput">
      <div class="output-modal">
        <div class="output-header">
          <span class="output-title">输出：{{ runningFile }}</span>
          <button class="btn btn-sm" @click="closeOutput">关闭</button>
        </div>
        <pre v-if="runOutput" class="output-body">{{ runOutput }}</pre>
        <pre v-else-if="runError" class="output-body error">{{ runError }}</pre>
        <div v-else class="output-body running">运行中...</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.search-bar { margin-bottom: 20px; }

.search-input {
  width: 100%;
  max-width: 420px;
  padding: 11px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  font-size: 14px;
  background: var(--surface);
  color: var(--text);
  outline: none;
  transition: all var(--duration) var(--ease);
}
.search-input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px var(--primary-glow);
}

.loading-wrap { display: flex; justify-content: center; padding: 48px; }

.file-list { display: flex; flex-direction: column; gap: 10px; animation: fadeSlideUp 0.4s var(--ease); }

.file-count {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
  font-weight: 500;
}

.file-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  flex-wrap: wrap;
  gap: 10px;
  transition: all var(--duration) var(--ease);
}
.file-row:hover {
  box-shadow: var(--shadow-md);
  border-color: color-mix(in srgb, var(--primary) 25%, var(--border));
  transform: translateY(-1px);
}

.file-info { display: flex; align-items: center; gap: 14px; min-width: 0; }

.file-thumb {
  width: 44px;
  height: 44px;
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 0.04em;
  flex-shrink: 0;
}
.file-thumb.js {
  color: var(--warning);
  background: var(--warning-soft);
  border: 1px solid color-mix(in srgb, var(--warning) 25%, transparent);
}
.file-thumb.doc {
  color: var(--accent);
  background: linear-gradient(135deg, color-mix(in srgb, var(--accent) 12%, transparent), color-mix(in srgb, var(--primary) 8%, transparent));
  border: 1px solid color-mix(in srgb, var(--accent) 25%, transparent);
}

.file-details { min-width: 0; }
.file-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 360px;
}
@media (max-width: 600px) {
  .file-name { max-width: 200px; }
  .file-row { padding: 12px; }
}

.file-meta { font-size: 12px; color: var(--text-muted); margin-top: 3px; }

.file-actions { display: flex; gap: 8px; flex-shrink: 0; }
.btn-sm { padding: 7px 14px; font-size: 12px; }
.btn-run {
  background: linear-gradient(135deg, var(--primary), color-mix(in srgb, var(--primary) 80%, var(--accent)));
  color: white;
  border-color: transparent;
  box-shadow: 0 2px 8px var(--primary-glow);
}
.btn-run:hover { box-shadow: 0 4px 14px var(--primary-glow); }
.btn-run:disabled { opacity: 0.6; cursor: not-allowed; box-shadow: none; }

.output-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  animation: fadeIn 0.2s var(--ease);
}
@keyframes fadeIn { from { opacity: 0; } to { opacity: 1; } }

.output-modal {
  background: var(--surface-elevated);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  width: 90%;
  max-width: 800px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-xl);
  animation: scaleIn 0.2s var(--ease);
}

.output-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
}

.output-title { font-weight: 700; font-size: 14px; color: var(--text); }

.output-body {
  padding: 18px;
  margin: 0;
  overflow: auto;
  font-family: 'JetBrains Mono', 'Cascadia Code', Consolas, monospace;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: calc(80vh - 60px);
  color: var(--text);
}

.output-body.error { color: var(--danger); }
.output-body.running { color: var(--text-muted); }
</style>