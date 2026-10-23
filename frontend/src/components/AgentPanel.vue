<script setup lang="ts">
import { computed } from 'vue'
import type { AgentStreamData } from '../types'
import { agentLabel, normalizeAgent } from '../utils/agentDisplay'
import MessagePartDispatcher from './MessagePartDispatcher.vue'

const props = defineProps<{ agent: AgentStreamData }>()

const parts = computed(() => normalizeAgent(props.agent).parts)
</script>

<template>
  <div class="agent" :class="agent.status">
    <div class="agent-h">
      <span class="avatar">{{ agent.agent_avatar || '🤖' }}</span>
      <span class="name">{{ agentLabel(agent) }}</span>
      <span class="badge" :class="agent.status">
        {{ agent.status === 'running' ? '● 运行中' : agent.status === 'completed' ? '✓ 完成' : '✗ 失败' }}
      </span>
    </div>

    <!-- 输出部件：按 agent 真实输出顺序交错（正文 ↔ 工具卡片，对齐 opencode Part 渲染） -->
    <div v-if="parts.length" class="o-parts">
      <MessagePartDispatcher v-for="p in parts" :key="p.seq" :part="p" />
    </div>
    <div v-else-if="agent.status === 'running'" class="steps">
      <div class="step-item running">
        <div class="step-row">
          <span class="step-icon">⏳</span>
          <span class="step-name">正在初始化...</span>
          <span class="step-time spinning">...</span>
        </div>
      </div>
    </div>

    <div v-if="agent.error" class="err">⚠️ {{ agent.error }}</div>
  </div>
</template>

<style scoped>
.agent {
  position: relative;
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  background: var(--surface-elevated);
  transition: border-color var(--duration) var(--ease), box-shadow var(--duration) var(--ease);
}
.agent::before { display: none; }
.agent.running { border-color: color-mix(in srgb, var(--primary) 36%, var(--border)); }
.agent-h { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; font-size: 14px; }
.avatar {
  width: 26px; height: 26px;
  font-size: 14px;
  border-radius: 8px;
  background: var(--surface);
  display: inline-flex; align-items: center; justify-content: center;
  flex-shrink: 0;
  border: 1px solid var(--border);
}
.name { font-weight: 600; color: var(--text); }
.badge { font-size: 11px; padding: 2px 9px; border-radius: var(--radius-pill); margin-left: auto; font-weight: 600; letter-spacing: 0.02em; flex-shrink: 0; }
.badge.running { background: var(--primary-glow); color: var(--primary); }
.badge.completed { background: var(--success-soft); color: var(--success); }
.badge.failed { background: var(--danger-soft); color: var(--danger); }

/* 运行中占位（无部件时提示正在初始化） */
.steps { display: flex; flex-direction: column; gap: 2px; margin: 10px 0 4px; }
.step-item { padding: 4px 6px; border-radius: 6px; font-size: 14px; color: var(--text-secondary); }
.step-row { display: flex; align-items: center; gap: 8px; }
.step-icon { font-size: 12px; flex-shrink: 0; width: 10px; text-align: center; color: var(--text-secondary); }
.step-name { font-weight: 500; }
.step-time { margin-left: auto; color: var(--text-secondary); font-size: 13px; flex-shrink: 0; font-variant-numeric: tabular-nums; }
.step-time.spinning { color: var(--primary); animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.o-parts { display: flex; flex-direction: column; gap: 8px; margin: 8px 0 2px; }
.err { padding: 10px 14px; background: var(--danger-soft); border: 1px solid color-mix(in srgb, var(--danger) 30%, transparent); border-radius: 12px; font-size: 13px; color: var(--danger); margin-top: 4px; }
</style>
