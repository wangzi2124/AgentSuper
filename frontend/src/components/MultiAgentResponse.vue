<script setup lang="ts">
import { computed, ref } from 'vue'
import type { MultiAgentMessage, TurnDiff } from '../types'
import { sumDurationMs } from '../utils/agentDisplay'
import MarkdownContent from './MarkdownContent.vue'
import AgentPanel from './AgentPanel.vue'
import DiffViewer from './DiffViewer.vue'
import PlanFileChip from './PlanFileChip.vue'

const props = defineProps<{
  message: MultiAgentMessage
  routingStatus: string
  isLast: boolean
  /** 本轮是否仍在执行中。true = 展开 Agent 面板与工具卡片（便于观看进度）；
   *  false = 任务完成，折叠为「已思考（用时 X 秒）」一行，可点开查看。 */
  running?: boolean
  /** [查看改动] 按需拉取该轮次 diff（由父组件注入 store.fetchMessageDiff，保持组件无 store 依赖）。
   *  step 省略 = 整轮净 diff；传 step=N = 只看第 N 步（每 step 快照） */
  loadDiff?: (messageId: string, step?: number) => Promise<TurnDiff>
}>()
const emit = defineEmits<{ restore: [] }>()

// [思考折叠] 是否手动展开了「已思考」明细（仅完成态有意义；执行中恒展开）。
const thoughtOpen = ref(false)
// 完成态：折叠成一行可展开；出错态：始终展开（便于排查）。
const collapsible = computed(() => !props.running && !props.message.isError)
const hasAgents = computed(() => props.message.agents.length > 0)

// [思考中动态加载] 整轮执行期间常驻在顶部，直到最终答案开始产出（message.content 非空）才隐藏。
const showThinking = computed(
  () => !!props.running && !props.message.isError && !(props.message.content || '').trim(),
)
// 是否渲染 Agent 明细：执行中 / 出错 / 手动展开。
const showAgents = computed(
  () => hasAgents.value && (!collapsible.value || thoughtOpen.value),
)
// 「已思考（用时 X 秒）」：累加各 Agent 步骤耗时（ms → 秒，向上取整，至少 1s）。
const thoughtSeconds = computed(
  () => Math.max(1, Math.round(sumDurationMs(props.message.agents) / 1000)),
)

// 修复：仅当 Agent 面板已展开且内容完全相同时才去重。
// 折叠态（面板不可见）必须始终展示最终答案，展开态内容不同也仍可见。
const isDuplicateOfSingleAgent = computed(() => {
  const agents = props.message.agents
  if (agents.length !== 1) return false
  const c = props.message.content?.trim()
  return !!c && agents[0].content?.trim() === c
})
const showFinalAnswer = computed(
  () => !!props.message.content && !(isDuplicateOfSingleAgent.value && showAgents.value),
)
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
      @click="thoughtOpen = !thoughtOpen"
    >
      <span class="tt-label">{{ message.isError ? '执行出错' : '已思考' }}</span>
      <span class="tt-time" v-if="!message.isError">（用时 {{ thoughtSeconds }} 秒）</span>
      <svg class="tt-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
    </button>

    <!-- per-agent panels：执行中 / 出错 / 手动展开时展示；完成态默认折叠 -->
    <div v-if="showAgents" class="agents">
      <AgentPanel v-for="a in message.agents" :key="a.agent_id" :agent="a" />
    </div>

    <!-- final answer -->
    <div v-if="showFinalAnswer" class="text"><MarkdownContent :text="message.content" /></div>

    <!-- [C8] 计划文件（plan Agent 落盘产物）：点击路径即复制，便于在编辑器/资源管理器打开 -->
    <PlanFileChip v-if="message.plan_path" :path="message.plan_path" />

    <!-- files changed（快照 diff：本轮改动了哪些文件 + 行数，可展开看 diff 文本） -->
    <DiffViewer
      v-if="message.files_changed?.length"
      :message-id="message.id"
      :files="message.files_changed"
      :restored="message.snapshotRestored"
      :load-diff="loadDiff"
      @restore="emit('restore')"
    />

    <!-- error -->
    <div v-if="message.isError && message.errorInfo" class="err">
      ⚠️ {{ message.errorInfo.message }}
    </div>
  </div>
</template>

<style scoped src="../styles/chat/multiAgentResponse.css"></style>
