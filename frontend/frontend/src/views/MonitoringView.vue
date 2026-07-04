<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchStats } from '../api/monitor'
import type { MonitorStats } from '../types'

const stats = ref<MonitorStats | null>(null)
const loading = ref(true)
const error = ref('')

onMounted(async () => {
  try {
    stats.value = await fetchStats()
  } catch (err: any) {
    error.value = err.message
  } finally {
    loading.value = false
  }
})

function ms(v: number): string {
  if (v >= 60000) return (v / 60000).toFixed(1) + ' min'
  if (v >= 1000) return (v / 1000).toFixed(1) + ' s'
  return Math.round(v) + ' ms'
}

function num(v: number): string {
  return v.toLocaleString()
}
</script>

<template>
  <div class="page-header">
    <h2>Monitoring</h2>
    <p>System usage statistics</p>
  </div>
  <div class="page-content">
    <div v-if="loading" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state">
      <p style="color:var(--danger);">Failed to load stats: {{ error }}</p>
    </div>

    <div v-if="stats" style="display:flex;flex-direction:column;gap:20px;">
      <!-- HTTP Requests -->
      <section>
        <h3 style="margin-bottom:8px;">HTTP Requests</h3>
        <div class="card" style="padding:16px;">
          <div class="stat-grid">
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.requests.total) }}</div>
              <div class="stat-label">Total Requests</div>
            </div>
          </div>
          <h4 style="margin:12px 0 6px;font-size:13px;">By Path</h4>
          <div class="list-table">
            <div
              v-for="(count, path) in stats.requests.by_path"
              :key="path"
              class="list-row"
            >
              <span style="font-family:monospace;font-size:12px;">{{ path }}</span>
              <span class="badge">{{ num(count) }}</span>
            </div>
          </div>
          <h4 style="margin:12px 0 6px;font-size:13px;">By Status</h4>
          <div class="list-table">
            <div
              v-for="(count, status) in stats.requests.by_status"
              :key="status"
              class="list-row"
            >
              <span style="font-family:monospace;font-size:12px;">{{ status }}</span>
              <span class="badge">{{ num(count) }}</span>
            </div>
          </div>
        </div>
      </section>

      <!-- Model Calls -->
      <section>
        <h3 style="margin-bottom:8px;">LLM Calls</h3>
        <div class="card" style="padding:16px;">
          <div class="stat-grid stat-grid-4">
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.total) }}</div>
              <div class="stat-label">Total Calls</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.total_prompt_tokens) }}</div>
              <div class="stat-label">Prompt Tokens</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.total_completion_tokens) }}</div>
              <div class="stat-label">Completion Tokens</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ ms(stats.model_calls.total_duration_ms) }}</div>
              <div class="stat-label">Total Duration</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ ms(stats.model_calls.avg_duration_ms) }}</div>
              <div class="stat-label">Avg Duration</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.tool_rounds_total) }}</div>
              <div class="stat-label">Tool Rounds</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.model_calls.avg_tool_rounds }}</div>
              <div class="stat-label">Avg Tool Rounds</div>
            </div>
          </div>
          <h4 style="margin:12px 0 6px;font-size:13px;">By Model</h4>
          <div class="list-table">
            <div
              v-for="(count, model) in stats.model_calls.by_model"
              :key="model"
              class="list-row"
            >
              <span style="font-family:monospace;font-size:12px;">{{ model }}</span>
              <span class="badge">{{ num(count) }}</span>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.stat-grid-4 {
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
}
.stat-item {
  text-align: center;
  padding: 12px 8px;
  background: var(--bg);
  border-radius: var(--radius);
}
.stat-value {
  font-size: 22px;
  font-weight: 700;
  color: var(--primary);
}
.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 2px;
}
.list-table {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.list-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 10px;
  background: var(--bg);
  border-radius: var(--radius);
}
.badge {
  font-size: 12px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 10px;
  background: var(--primary);
  color: #fff;
}
</style>
