<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import type { FileChange, TurnDiff } from '../types'

const props = defineProps<{
  messageId: string
  files: FileChange[]
  restored?: boolean
  loadDiff?: (messageId: string, step?: number) => Promise<TurnDiff>
}>()
const emit = defineEmits<{ restore: [] }>()

// diff 状态完全自管（组件 ref），禁止写回 message。
// 组件实例内缓存：同一轮次重复展开不重复请求；引入虚拟滚动时再提升到 Store。
const open = ref(false)
const loading = ref(false)
const error = ref('')
const step = ref<number | null>(null)
const data = ref<TurnDiff | null>(null)

const showStepPicker = computed(() => (data.value?.steps ?? 0) > 1)
const stepLabels = computed(() =>
  Array.from({ length: data.value?.steps ?? 0 }, (_, i) => i),
)

async function toggle(): Promise<void> {
  open.value = !open.value
  if (open.value && !data.value) await fetchDiff()
}

async function fetchDiff(s?: number): Promise<void> {
  if (!props.loadDiff) return
  const target = s ?? null
  loading.value = true
  error.value = ''
  try {
    data.value = await props.loadDiff(props.messageId, s)
    step.value = target
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

// 撤回后 diff 立刻失效：清空已拉取内容并收起。
watch(
  () => props.restored,
  (v) => {
    if (!v) return
    data.value = null
    open.value = false
  },
)

function splitDiff(diff: string): string[] {
  return diff ? diff.split('\n') : []
}

function diffLineClass(line: string): string {
  if (line.startsWith('@@')) return 'hunk'
  // --- / +++ 是文件头（路径行），不是增删内容
  if (line.startsWith('---') || line.startsWith('+++')) return 'meta'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  if (
    line.startsWith('diff ') ||
    line.startsWith('index ') ||
    line.startsWith('new file') ||
    line.startsWith('deleted file') ||
    line.startsWith('similarity ') ||
    line.startsWith('rename ')
  ) return 'meta'
  return 'ctx'
}

function statusLabel(status: string): string {
  if (status === 'added') return '新增'
  if (status === 'deleted') return '删除'
  return '修改'
}
</script>

<template>
  <div class="files-changed">
    <div class="fc-title">
      <span>文件改动 · {{ files.length }}</span>
      <div class="fc-actions">
        <button class="fc-toggle" @click="toggle">{{ open ? '收起 diff' : '查看 diff' }}</button>
        <button
          class="fc-restore"
          :disabled="restored"
          :class="{ restored }"
          @click="emit('restore')"
        >{{ restored ? '已撤回' : '撤回本轮改动' }}</button>
      </div>
    </div>

    <div v-for="fc in files" :key="fc.file" class="fc-row">
      <span class="fc-badge" :class="fc.status">{{ statusLabel(fc.status) }}</span>
      <span class="fc-file" :title="fc.file">{{ fc.file }}</span>
      <span v-if="fc.external" class="fc-lines binary">外部路径</span>
      <span v-else-if="fc.binary" class="fc-lines binary">二进制</span>
      <span v-else class="fc-lines">
        <span class="add">+{{ fc.additions ?? 0 }}</span>
        <span class="del">-{{ fc.deletions ?? 0 }}</span>
      </span>
    </div>

    <!-- 展开的 unified diff：内部文件来自 git diff before_tree→after_tree，
         外部文件由归档 blob 与当前内容 difflib 对比生成 -->
    <div v-if="open" class="fc-diff">
      <!-- 本轮有多个写步骤时可切到单步；「整轮」= 首尾净 diff -->
      <div v-if="showStepPicker" class="fc-steps">
        <button class="fc-step" :class="{ active: step === null }" @click="fetchDiff(undefined)">整轮</button>
        <button
          v-for="s in stepLabels"
          :key="s"
          class="fc-step"
          :class="{ active: step === s }"
          @click="fetchDiff(s)"
        >第 {{ s + 1 }} 步</button>
      </div>
      <div v-if="loading" class="fc-diff-hint">加载 diff…</div>
      <div v-else-if="error" class="fc-diff-hint">{{ error }}</div>
      <div v-else-if="data?.reason" class="fc-diff-hint">{{ data.reason }}</div>
      <div v-else-if="!data?.files.length" class="fc-diff-hint">无可展示的 diff</div>
      <div v-for="d in data?.files ?? []" :key="d.file" class="fc-diff-file">
        <div class="fc-diff-path">{{ d.file }}</div>
        <pre class="fc-diff-body"><code><span
          v-for="(line, i) in splitDiff(d.diff)"
          :key="i"
          class="fc-dl"
          :class="diffLineClass(line)"
        >{{ line }}{{ '\n' }}</span></code></pre>
      </div>
      <div v-if="data?.truncated" class="fc-diff-hint">diff 过长已截断</div>
    </div>
  </div>
</template>

<style scoped>
.files-changed {
  margin-top: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface-elevated);
  font-size: 13px;
}
.fc-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: var(--text-secondary);
  margin-bottom: 8px;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.fc-actions { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
.fc-toggle {
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: var(--radius-pill);
  padding: 2px 10px;
  cursor: pointer;
}
.fc-toggle:hover { background: var(--primary-glow); }
.fc-restore {
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: var(--radius-pill);
  padding: 2px 10px;
  cursor: pointer;
  flex-shrink: 0;
}
.fc-restore:hover:not(:disabled) { background: var(--primary-glow); }
.fc-restore:disabled { opacity: 0.6; cursor: default; }
.fc-restore.restored { color: var(--text-secondary); }
.fc-row { display: flex; align-items: center; gap: 8px; padding: 3px 0; }
.fc-badge {
  font-size: 11px;
  padding: 1px 7px;
  border-radius: var(--radius-pill);
  font-weight: 600;
  flex-shrink: 0;
}
.fc-badge.added { background: var(--success-soft); color: var(--success); }
.fc-badge.modified { background: var(--primary-glow); color: var(--primary); }
.fc-badge.deleted { background: var(--danger-soft); color: var(--danger); }
.fc-file {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono);
  font-size: 12.5px;
}
.fc-lines { flex-shrink: 0; font-family: var(--font-mono); font-size: 12.5px; }
.fc-lines .add { color: var(--success); margin-right: 6px; }
.fc-lines .del { color: var(--danger); }
.fc-lines.binary { color: var(--text-secondary); }

.fc-diff { margin-top: 8px; }
.fc-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: 6px;
}
.fc-step {
  padding: 2px 8px;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 999px;
  cursor: pointer;
}
.fc-step.active {
  color: #fff;
  background: var(--primary);
  border-color: var(--primary);
}
.fc-diff-hint {
  padding: 6px 8px;
  font-size: 12px;
  color: var(--text-secondary);
  background: var(--bg);
  border-radius: 8px;
}
.fc-diff-file + .fc-diff-file { margin-top: 8px; }
.fc-diff-path {
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--text-secondary);
  padding: 2px 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.fc-diff-body {
  margin: 0;
  padding: 6px 0;
  max-height: 420px;
  overflow: auto;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 8px;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.5;
}
.fc-diff-body code { display: block; min-width: max-content; }
.fc-dl { display: block; padding: 0 8px; white-space: pre; }
.fc-dl.add { background: var(--success-soft); color: var(--success); }
.fc-dl.del { background: var(--danger-soft); color: var(--danger); }
.fc-dl.hunk { color: var(--primary); background: var(--primary-glow); }
.fc-dl.meta { color: var(--text-secondary); }
.fc-dl.ctx { color: var(--text-primary); }
</style>
