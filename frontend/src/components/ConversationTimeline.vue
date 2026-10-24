<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { MultiAgentMessage, TurnDiff } from '../types'
import { normalizeAgent, sumDurationMs } from '../utils/agentDisplay'
import MarkdownContent from './MarkdownContent.vue'
import TimelineToolRow from './TimelineToolRow.vue'
import DiffViewer from './DiffViewer.vue'
import PlanFileChip from './PlanFileChip.vue'

const props = defineProps<{
  message: MultiAgentMessage
  routingStatus: string
  isLast: boolean
  running?: boolean
  loadDiff?: (messageId: string, step?: number) => Promise<TurnDiff>
}>()
const emit = defineEmits<{ restore: [] }>()

const showTimeline = ref(Boolean(props.running))
watch(() => props.running, (running) => {
  if (running) showTimeline.value = true
})

type Item = { key: string; kind: 'text' | 'tool' | 'reasoning'; text?: string; step?: any }
const items = computed<Item[]>(() => {
  const out: Item[] = []
  for (const agent of props.message.agents) {
    const parts = normalizeAgent(agent).parts
    for (const part of parts) {
      // 隐藏 Harness 图内节点（无 tool_name），只保留真实工具调用。
      if (part.kind === 'tool' && part.step?.tool_name) {
        out.push({ key: `${agent.agent_id}:${part.step.step_id}`, kind: 'tool', step: part.step })
      } else if ((part.kind === 'text' || part.kind === 'reasoning') && (part.text || '').trim()) {
        out.push({
          key: `${agent.agent_id}:${part.seq}`,
          kind: part.kind === 'reasoning' ? 'reasoning' : 'text',
          text: part.text,
        })
      }
    }
  }
  return out
})
const thoughtSeconds = computed(() => Math.max(1, Math.round(sumDurationMs(props.message.agents) / 1000)))
const collapsible = computed(() => !props.running && !props.message.isError && items.value.length > 0)
const isDuplicateOfSingleAgent = computed(() => {
  const list = props.message.agents
  if (list.length !== 1) return false
  const c = props.message.content?.trim()
  return !!c && list[0].content?.trim() === c
})
const showFinalAnswer = computed(() =>
  !!props.message.content && !(isDuplicateOfSingleAgent.value && showTimeline.value),
)
const statusText = computed(() => {
  if (props.message.isError) return 'Failed'
  if (props.running) return 'Working…'
  return `Completed · ${thoughtSeconds.value}s`
})
</script>

<template>
  <div class="timeline" :class="{ running, error: message.isError }">
    <div class="timeline-status" aria-live="polite">
      <span class="status-dot" :class="{ running, error: message.isError }"></span>
      <span>{{ statusText }}</span>
      <span v-if="message.model" class="status-model">{{ message.model }}</span>
    </div>

    <button
      v-if="collapsible"
      type="button"
      class="timeline-toggle"
      :aria-expanded="showTimeline"
      @click="showTimeline = !showTimeline"
    >
      <span>{{ showTimeline ? '隐藏执行步骤' : `已执行 ${items.length} 步 · ${thoughtSeconds}s` }}</span>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
    </button>

    <div v-if="showTimeline" class="timeline-items">
      <template v-for="item in items" :key="item.key">
        <TimelineToolRow v-if="item.kind === 'tool'" :step="item.step" />
        <details v-else-if="item.kind === 'reasoning'" class="reasoning-block">
          <summary>思考过程</summary>
          <MarkdownContent :text="item.text || ''" />
        </details>
        <div v-else class="timeline-text">
          <MarkdownContent :text="item.text || ''" />
        </div>
      </template>
      <div v-if="running && !items.length" class="timeline-empty">正在初始化…</div>
    </div>

    <div v-if="showFinalAnswer" class="timeline-final">
      <MarkdownContent :text="message.content" />
    </div>

    <PlanFileChip v-if="message.plan_path" :path="message.plan_path" />

    <DiffViewer
      v-if="message.files_changed?.length"
      :message-id="message.id"
      :files="message.files_changed"
      :restored="message.snapshotRestored"
      :load-diff="loadDiff"
      @restore="emit('restore')"
    />

    <div v-if="message.isError && message.errorInfo" class="timeline-error" role="alert">
      <span class="err-type">{{ message.errorInfo.errorType || message.errorInfo.type }}</span>
      <span class="err-msg">{{ message.errorInfo.message }}</span>
      <span v-if="message.errorInfo.statusCode" class="err-code">{{ message.errorInfo.statusCode }}</span>
    </div>
  </div>
</template>

<style scoped src="../styles/chat/conversationTimeline.css"></style>
