<script setup lang="ts">
import type { AgentOutputPart } from '../types'
import MarkdownContent from './MarkdownContent.vue'
import AgentStepCard from './AgentStepCard.vue'

defineProps<{ part: AgentOutputPart }>()
</script>

<template>
  <div v-if="part.kind === 'text'" class="text o-text">
    <MarkdownContent :text="part.text || ''" />
  </div>
  <AgentStepCard v-else-if="part.kind === 'tool' && part.step" :step="part.step" />
</template>

<style scoped>
.text { white-space: normal; word-break: break-word; }
.o-text > *:first-child { margin-top: 0; }
</style>
