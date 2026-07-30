<script setup lang="ts">
import type { MultiAgentMessage } from '../types'

const props = defineProps<{
  message: MultiAgentMessage
  routingStatus: string
  isLast: boolean
}>()

defineEmits<{ undo: [] }>()
</script>

<template>
  <div class="response" :class="{ loading: isLast && !!routingStatus, error: message.isError }">
    <!-- routing -->
    <div v-if="isLast && routingStatus" class="routing">
      <span class="spinner"></span>
      {{ routingStatus }}
    </div>

    <!-- steps -->
    <div v-if="message.agents.length" class="agents">
      <div v-for="a in message.agents" :key="a.agent_id" class="agent">
        <div class="agent-h">
          <span class="avatar">{{ a.agent_avatar || '🤖' }}</span>
          <span class="name">{{ a.agent_name }}</span>
          <span class="badge" :class="a.status">{{ a.status === 'running' ? '● running' : a.status === 'completed' ? '✓ done' : '✗ failed' }}</span>
        </div>
        <div v-if="a.content" class="text" v-text="a.content"></div>
        <div v-if="a.error" class="err">⚠️ {{ a.error }}</div>
      </div>
    </div>

    <!-- final answer -->
    <div v-if="message.content && (!message.agents.length || !isLast)" class="text" v-text="message.content"></div>

    <!-- error -->
    <div v-if="message.isError && message.errorInfo" class="err">
      ⚠️ {{ message.errorInfo.message }}
    </div>
  </div>
</template>

<style scoped>
.response { font-size: 14px; line-height: 1.6; }
.routing { display: flex; align-items: center; gap: 8px; padding: 8px 0; font-size: 13px; color: var(--text-secondary); }
.spinner { width: 14px; height: 14px; border: 2px solid var(--border); border-top-color: var(--primary); border-radius: 50%; animation: spin 0.8s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }
.text { white-space: pre-wrap; word-break: break-word; }
.agents { display: flex; flex-direction: column; gap: 8px; }
.agent { border: 1px solid var(--border); border-radius: 10px; padding: 12px; }
.agent-h { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; font-size: 13px; }
.avatar { font-size: 18px; }
.name { font-weight: 600; }
.badge { font-size: 11px; padding: 2px 8px; border-radius: 10px; margin-left: auto; }
.badge.running { background: rgba(99,102,241,0.12); color: var(--primary); }
.badge.completed { background: rgba(34,197,94,0.12); color: #22c55e; }
.badge.failed { background: rgba(239,68,68,0.12); color: #ef4444; }
.err { padding: 8px 12px; background: rgba(239,68,68,0.06); border: 1px solid rgba(239,68,68,0.2); border-radius: 8px; font-size: 13px; color: #ef4444; margin-top: 4px; }
</style>
