<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { MultiAgentMessage, TurnDiff } from '../types'
import { sumDurationMs } from '../utils/agentDisplay'
import { useTimelineItems } from '../harness/useTimelineStream'
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

const { items, isDuplicateAnswer } = useTimelineItems(() => props.message)
const durationMs = computed(() => {
  if (typeof props.message.duration_ms === 'number') return props.message.duration_ms
  return sumDurationMs(props.message.agents)
})
const durationLabel = computed(() => {
  const ms = durationMs.value
  if (!ms || ms <= 0) return ''
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`
})
const collapsible = computed(() => !props.running && !props.message.isError && items.value.length > 0)
const showFinalAnswer = computed(() =>
  !!props.message.content && !(isDuplicateAnswer.value && showTimeline.value),
)
const statusText = computed(() => {
  if (props.message.isError) return 'Failed'
  if (props.running) return 'Working…'
  return durationLabel.value ? `Completed · ${durationLabel.value}` : 'Completed'
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
      <span>{{ showTimeline ? '隐藏执行步骤' : `已执行 ${items.length} 步${durationLabel ? ` · ${durationLabel}` : ''}` }}</span>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
    </button>

    <div v-if="showTimeline" class="timeline-items">
      <template v-for="item in items" :key="item.key">
        <TimelineToolRow v-if="item.kind === 'tool' && item.step" :step="item.step" />
        <div v-else-if="item.kind === 'notice'" class="timeline-notice">
          <span class="notice-dot"></span>
          <span>
            上下文已裁剪：{{ item.notice?.estimated_before || '—' }} → {{ item.notice?.estimated_after || '—' }} tokens
            （窗口 {{ item.notice?.window || '—' }}<template v-if="item.notice?.dropped_messages"> · 丢弃 {{ item.notice.dropped_messages }} 条</template>）
          </span>
        </div>
        <details v-else-if="item.kind === 'reasoning'" class="reasoning-block">
          <summary>思考过程</summary>
          <MarkdownContent :text="item.text || ''" :streaming="running" />
        </details>
        <div v-else class="timeline-text">
          <MarkdownContent :text="item.text || ''" :streaming="running" />
        </div>
      </template>
      <div v-if="running && !items.length" class="timeline-empty">正在初始化…</div>
    </div>

    <div v-if="showFinalAnswer" class="timeline-final">
      <MarkdownContent :text="message.content" :streaming="running" />
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
