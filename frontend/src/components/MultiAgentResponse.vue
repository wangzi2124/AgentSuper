<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MultiAgentMessage, AgentStep, AgentOutputPart } from '../types'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{
  message: MultiAgentMessage
  routingStatus: string
  isLast: boolean
  /** 本轮是否仍在执行中（父组件传 agent.loading && isLast）。
   *  true = 展开显示 plan/build 等 Agent 面板与工具卡片（便于观看进度）；
   *  false = 任务完成，折叠为「已思考（用时 X 秒）」一行，可点开查看。 */
  running?: boolean
  /** [查看改动] 按需拉取该轮次 diff（由父组件注入 store.fetchMessageDiff，保持组件无 store 依赖）
   *  step 省略 = 整轮净 diff；传 step=N = 只看第 N 步（每 step 快照） */
  loadDiff?: (messageId: string, step?: number) => Promise<unknown>
}>()

// [思考折叠] 是否手动展开了「已思考」明细（仅完成态有意义；执行中恒展开）。
const thoughtOpen = ref(false)
// 完成态：折叠成一行可展开；出错态：始终展开（便于排查）。
const collapsible = computed(() => !props.running && !props.message.isError)
const hasAgents = computed(() => props.message.agents.length > 0)

// 后端 agent_id → 中文展示名（agent_name 目前就是 build/plan 这类内部标识）。
const AGENT_LABELS: Record<string, string> = {
  build: '执行助手',
  plan: '任务规划',
  supervisor: '调度',
  research: '研究助手',
  code: '编码助手',
  file: '文件助手',
}
function agentLabel(a: { agent_id: string; agent_name?: string }): string {
  return AGENT_LABELS[a.agent_id] || a.agent_name || a.agent_id
}

// [思考中动态加载] 整轮执行期间常驻在顶部（与下方执行过程同时可见），
// 直到最终答案开始产出（message.content 非空）才隐藏。
const showThinking = computed(
  () => !!props.running && !props.message.isError && !(props.message.content || '').trim(),
)
// 是否渲染 Agent 明细：执行中 / 出错 / 手动展开。
// 与「思考中」并存：上方一行状态，下方实时执行过程。
const showAgents = computed(
  () => hasAgents.value && (!collapsible.value || thoughtOpen.value),
)
// 「已思考（用时 X 秒）」：累加各 Agent 步骤耗时（ms → 秒，向上取整，至少 1s）。
const thoughtSeconds = computed(() => {
  let ms = 0
  for (const a of props.message.agents) for (const s of a.steps || []) ms += s.duration_ms || 0
  return Math.max(1, Math.round(ms / 1000))
})
function toggleThought(): void { thoughtOpen.value = !thoughtOpen.value }

const emit = defineEmits<{ undo: []; restore: [] }>()

// [查看改动] diff 按需拉取（体积大不进 IndexedDB）：点「查看 diff」才请求，
// 拉回来缓存在 message.diffFiles 上，重复展开不再请求。
const diffOpen = ref(false)
const diffLoading = ref(false)
const diffError = ref('')

async function toggleDiff(): Promise<void> {
  diffOpen.value = !diffOpen.value
  if (!diffOpen.value) return
  if (props.message.diffFiles?.length || props.message.diffReason) return
  if (!props.loadDiff) return
  await loadStepDiff()
}

// [逐步快照] 切到第 N 步（opencode 语义：每个写工具调用各一个 tree）。
// 步骤数由整轮 diff 响应带回，只有 1 步时不必显示选择器。
async function loadStepDiff(step?: number): Promise<void> {
  if (!props.loadDiff) return
  diffLoading.value = true
  diffError.value = ''
  try {
    await props.loadDiff(props.message.id, step)
  } catch (e) {
    diffError.value = e instanceof Error ? e.message : String(e)
  } finally {
    diffLoading.value = false
  }
}

const showStepPicker = computed(() => (props.message.diffSteps ?? 0) > 1)
const stepLabels = computed(() => {
  const n = props.message.diffSteps ?? 0
  return Array.from({ length: n }, (_, i) => i)
})

// 把 unified diff 拆成行；行首 +/-/空格决定着色（@@ 与 diff/index/mode 头不着色）
function splitDiff(diff: string): string[] {
  if (!diff) return []
  return diff.split('\n')
}

function diffLineClass(line: string): string {
  if (line.startsWith('@@')) return 'hunk'
  // --- / +++ 是文件头（路径行），不是增删内容
  if (line.startsWith('---') || line.startsWith('+++')) return 'meta'
  if (line.startsWith('+')) return 'add'
  if (line.startsWith('-')) return 'del'
  if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('new file')
    || line.startsWith('deleted file') || line.startsWith('similarity ')
    || line.startsWith('rename ')) return 'meta'
  return 'ctx'
}

// [C8] 计划文件路径：整块可点击（点一下即复制，便于粘到编辑器/资源管理器打开），
// 浏览器无法直接打开后端本地文件，故用「点击复制 + 显式提示」而非伪链接
// （href="file://" 在 http(s) 页面会被浏览器拦截）。navigator.clipboard 在非安全
// 上下文可能不可用 → 降级为 prompt 让用户手动复制。
const copied = ref(false)
async function copyPlanPath(): Promise<void> {
  const path = props.message.plan_path
  if (!path) return
  try {
    await navigator.clipboard.writeText(path)
    copied.value = true
    setTimeout(() => { copied.value = false }, 1500)
  } catch {
    copied.value = false
    window.prompt('请复制计划文件路径：', path)
  }
}

// 单 Agent 路由时，最终答案与 Agent 面板内容完全一致，
// 再渲染一遍会造成"两条最终答案"的重复。仅在内容有新增信息时才展示。
// 但任务完成后 Agent 面板已隐藏 → 必须始终展示最终答案（作为唯一总结）。
const showFinalAnswer = computed(() => {
  const content = props.message.content
  if (!content) return false
  if (!showAgents.value) return true
  const agents = props.message.agents
  if (agents.length === 1 && agents[0].content && agents[0].content.trim() === content.trim()) {
    return false
  }
  return true
})

// opencode 风格状态符号
function getStatusIcon(status: string): string {
  if (status === 'completed') return '✓'
  if (status === 'failed') return '✕'
  return '•'
}

function formatDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms.toFixed(0)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// 工具/步骤卡片标题（对齐 opencode BasicTool trigger 的子标题文案）
function stepTitle(step: AgentStep): string {
  // 图节点进度（无 tool_name）不展示 detail —— 那是原始 JSON，对用户无意义。
  if (!step.tool_name) return ''
  if (step.detail) return step.detail
  return step.tool_name
}

// 极简工具名展示（去掉 tool_ 前缀，对齐 opencode 工具名可读性）
function shortToolName(name?: string): string {
  if (!name) return '工具'
  return name.replace(/^plugin_[^_]+_/, '').replace(/^tool_/, '')
}

// 图节点 → 中文阶段名（后台执行过程展示；替代原先直出的 plan/rag/execute 英文节点名）。
const NODE_LABELS: Record<string, string> = {
  filter: '识别意图',
  plan: '任务规划',
  rag: '知识库检索',
  memory_recall: '记忆召回',
  context_compress: '整理上下文',
  execute_subagent: '执行任务',
  parallel: '并行执行',
  parallel_worker: '并行执行',
  grade_docs: '评估检索结果',
  rewrite: '改写查询',
  synthesize: '汇总结果',
  memory_store: '保存记忆',
}

// 卡片标签：真实工具调用显示工具名；图节点进度显示中文阶段名。
function cardLabel(step: AgentStep): string {
  if (step.tool_name) return shortToolName(step.tool_name)
  return NODE_LABELS[step.name] || '处理中'
}

// 按输出顺序排列的展示部件：优先 parts（真实交错），否则组装 steps（含最终正文）
function orderedParts(agent: {
  parts?: AgentOutputPart[]
  steps: AgentStep[]
  content: string
}): AgentOutputPart[] {
  if (agent.parts && agent.parts.length > 0) return agent.parts
  // 回退：历史回放（服务端只存 steps+content）——
  // 组装 [执行步骤…, 最终正文]，保证「先出执行过程、再出答案」。
  const parts: AgentOutputPart[] = (agent.steps || []).map((s, i) => ({
    seq: i,
    kind: 'tool' as const,
    step: s,
  }))
  if (agent.content) {
    parts.push({ seq: parts.length, kind: 'text' as const, text: agent.content })
  }
  return parts
}

// 文件改动状态 → 中文徽标文案
function statusLabel(status: string): string {
  if (status === 'added') return '新增'
  if (status === 'deleted') return '删除'
  return '修改'
}
</script>

<template>
  <div class="response" :class="{ loading: isLast && !!routingStatus, error: message.isError }">
    <!-- waiting for response：动态「思考中」三点加载（排队/路由/等首 token 均可见） -->
    <div v-if="showThinking" class="thinking">
      <span class="thinking-dots" aria-hidden="true"><i></i><i></i><i></i></span>
      <span class="thinking-label">思考中<template v-if="routingStatus"> · {{ routingStatus }}</template></span>
    </div>

    <!-- [思考折叠] 完成态：折叠为一行「已思考（用时 X 秒）」，点击展开/收起明细 -->
    <button
      v-if="collapsible && hasAgents"
      type="button"
      class="thought-toggle"
      :class="{ open: thoughtOpen }"
      @click="toggleThought"
    >
      <span class="tt-label">{{ message.isError ? '执行出错' : '已思考' }}</span>
      <span class="tt-time" v-if="!message.isError">（用时 {{ thoughtSeconds }} 秒）</span>
      <svg class="tt-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
    </button>

    <!-- per-agent panels：执行中 / 出错 / 手动展开时展示；完成态默认折叠 -->
    <div v-if="showAgents" class="agents">
      <div v-for="a in message.agents" :key="a.agent_id" class="agent" :class="a.status">
        <div class="agent-h">
          <span class="avatar">{{ a.agent_avatar || '🤖' }}</span>
          <span class="name">{{ agentLabel(a) }}</span>
          <span class="badge" :class="a.status">{{ a.status === 'running' ? '● 运行中' : a.status === 'completed' ? '✓ 完成' : '✗ 失败' }}</span>
        </div>

        <!-- 输出部件：按 agent 真实输出顺序交错（正文 ↔ 工具卡片，对齐 opencode Part 渲染） -->
        <div v-if="orderedParts(a).length" class="o-parts">
          <template v-for="p in orderedParts(a)" :key="p.seq">
            <!-- 正文块 -->
            <div v-if="p.kind === 'text'" class="text o-text">
              <MarkdownContent :text="p.text || ''" />
            </div>
            <!-- 极简工具卡片（无参数 / 无结果展开） -->
            <div v-else-if="p.kind === 'tool' && p.step" class="o-tool" :class="[p.step.status, { 'is-step': !p.step.tool_name }]">
              <span class="o-tool-icon" :class="p.step.status">{{ getStatusIcon(p.step.status) }}</span>
              <span class="o-tool-name">{{ cardLabel(p.step) }}</span>
              <span class="o-tool-title">{{ stepTitle(p.step) }}</span>
              <span v-if="p.step.duration_ms != null" class="o-tool-time">{{ formatDuration(p.step.duration_ms) }}</span>
              <span v-else-if="p.step.status === 'running'" class="o-tool-spin"></span>
            </div>
          </template>
        </div>
        <div v-else-if="a.status === 'running'" class="steps">
          <div class="step-item running">
            <div class="step-row">
              <span class="step-icon">⏳</span>
              <span class="step-name">正在初始化...</span>
              <span class="step-time spinning">...</span>
            </div>
          </div>
        </div>

        <div v-if="a.error" class="err">⚠️ {{ a.error }}</div>
      </div>
    </div>

    <!-- final answer -->
    <div v-if="showFinalAnswer" class="text"><MarkdownContent :text="message.content" /></div>

    <!-- [C8] 计划文件（plan Agent 落盘产物）：点击路径即复制，便于在编辑器/资源管理器打开 -->
    <div v-if="message.plan_path" class="plan-file" role="button" tabindex="0"
         title="点击复制计划文件路径" @click="copyPlanPath" @keyup.enter="copyPlanPath">
      <span class="pf-icon">📋</span>
      <span class="pf-label">已生成计划文件</span>
      <code class="pf-path">{{ message.plan_path }}</code>
      <span class="pf-copy">{{ copied ? '已复制' : '点击复制路径' }}</span>
    </div>

    <!-- files changed（快照 diff：本次轮次改动了哪些文件 + 行数，可展开看 diff 文本） -->
    <div v-if="message.files_changed?.length" class="files-changed">
      <div class="fc-title">
        <span>文件改动 · {{ message.files_changed.length }}</span>
        <div class="fc-actions">
          <button class="fc-toggle" @click="toggleDiff">
            {{ diffOpen ? '收起 diff' : '查看 diff' }}
          </button>
          <button
            class="fc-restore"
            :disabled="message.snapshotRestored"
            :class="{ restored: message.snapshotRestored }"
            @click="emit('restore')"
          >{{ message.snapshotRestored ? '已撤回' : '撤回本轮改动' }}</button>
        </div>
      </div>
      <div v-for="fc in message.files_changed" :key="fc.file" class="fc-row">
        <span class="fc-badge" :class="fc.status">{{ statusLabel(fc.status) }}</span>
        <span class="fc-file" :title="fc.file">{{ fc.file }}</span>
        <span v-if="fc.external" class="fc-lines binary">外部路径</span>
        <span v-else-if="fc.binary" class="fc-lines binary">二进制</span>
        <span v-else class="fc-lines">
          <span class="add">+{{ fc.additions ?? 0 }}</span>
          <span class="del">-{{ fc.deletions ?? 0 }}</span>
        </span>
      </div>

      <!-- [查看改动] 展开的 unified diff：内部文件来自 git diff before_tree→after_tree，
           外部文件由归档 blob 与当前内容 difflib 对比生成 -->
      <div v-if="diffOpen" class="fc-diff">
        <!-- [逐步快照] 本轮有多个写步骤时可切到单步；「整轮」= 首尾净 diff -->
        <div v-if="showStepPicker" class="fc-steps">
          <button
            class="fc-step"
            :class="{ active: message.diffStep == null }"
            @click="loadStepDiff(undefined)"
          >整轮</button>
          <button
            v-for="s in stepLabels"
            :key="s"
            class="fc-step"
            :class="{ active: message.diffStep === s }"
            @click="loadStepDiff(s)"
          >第 {{ s + 1 }} 步</button>
        </div>
        <div v-if="diffLoading" class="fc-diff-hint">加载 diff…</div>
        <div v-else-if="diffError" class="fc-diff-hint">{{ diffError }}</div>
        <div v-else-if="message.diffReason" class="fc-diff-hint">{{ message.diffReason }}</div>
        <div v-else-if="!message.diffFiles?.length" class="fc-diff-hint">无可展示的 diff</div>
        <div v-for="d in message.diffFiles" :key="d.file" class="fc-diff-file">
          <div class="fc-diff-path">{{ d.file }}</div>
          <pre class="fc-diff-body"><code><span
            v-for="(line, i) in splitDiff(d.diff)"
            :key="i"
            class="fc-dl"
            :class="diffLineClass(line)"
          >{{ line }}{{ '\n' }}</span></code></pre>
        </div>
        <div v-if="message.diffTruncated" class="fc-diff-hint">diff 过长已截断</div>
      </div>
    </div>

    <!-- error -->
    <div v-if="message.isError && message.errorInfo" class="err">
      ⚠️ {{ message.errorInfo.message }}
    </div>
  </div>
</template>


<style scoped src="../styles/chat/multiAgentResponse.css"></style>

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
.plan-file:hover {
  border-color: var(--primary);
}
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
.plan-file:hover .pf-copy {
  border-color: var(--primary);
}

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

/* [查看改动] 展开的 unified diff */
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
