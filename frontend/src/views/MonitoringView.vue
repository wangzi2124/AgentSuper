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
    <h2>系统监控</h2>
    <p>系统使用统计</p>
  </div>
  <div class="page-content">
    <div v-if="loading" class="loading-wrap">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state">
      <p style="color:var(--danger);">统计数据加载失败：{{ error }}</p>
    </div>

    <div v-if="stats" class="monitor-wrap">
      <!-- HTTP Requests -->
      <section class="monitor-section">
        <div class="section-head">
          <span class="section-dot"></span>
          <h3>HTTP 请求</h3>
        </div>
        <div class="card stat-card">
          <div class="stat-grid">
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.requests.total) }}</div>
              <div class="stat-label">请求总数</div>
            </div>
          </div>
          <div class="list-block">
            <h4>按路径</h4>
            <div class="list-table">
              <div v-for="(count, path) in stats.requests.by_path" :key="path" class="list-row">
                <span class="row-path">{{ path }}</span>
                <span class="badge badge-count">{{ num(count) }}</span>
              </div>
            </div>
          </div>
          <div class="list-block">
            <h4>按状态码</h4>
            <div class="list-table">
              <div v-for="(count, status) in stats.requests.by_status" :key="status" class="list-row">
                <span class="row-path">{{ status }}</span>
                <span class="badge badge-count">{{ num(count) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- Model Calls -->
      <section class="monitor-section">
        <div class="section-head">
          <span class="section-dot"></span>
          <h3>LLM 调用</h3>
        </div>
        <div class="card stat-card">
          <div class="stat-grid stat-grid-4">
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.total) }}</div>
              <div class="stat-label">调用总数</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.total_prompt_tokens) }}</div>
              <div class="stat-label">输入 Token</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.total_completion_tokens) }}</div>
              <div class="stat-label">输出 Token</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ ms(stats.model_calls.total_duration_ms) }}</div>
              <div class="stat-label">总耗时</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ ms(stats.model_calls.avg_duration_ms) }}</div>
              <div class="stat-label">平均耗时</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.tool_rounds_total) }}</div>
              <div class="stat-label">工具轮数</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ stats.model_calls.avg_tool_rounds }}</div>
              <div class="stat-label">平均工具轮数</div>
            </div>
          </div>
          <div class="list-block">
            <h4>按模型</h4>
            <div class="list-table">
              <div v-for="(count, model) in stats.model_calls.by_model" :key="model" class="list-row">
                <span class="row-path">{{ model }}</span>
                <span class="badge badge-count">{{ num(count) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.loading-wrap { display: flex; justify-content: center; padding: 48px; }
.monitor-wrap { display: flex; flex-direction: column; gap: 28px; animation: fadeSlideUp 0.4s var(--ease); }
.monitor-section { display: flex; flex-direction: column; gap: 12px; }
.section-head { display: flex; align-items: center; gap: 10px; }
.section-head h3 { font-size: 16px; font-weight: 700; letter-spacing: -0.02em; }
.section-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: linear-gradient(135deg, var(--primary), var(--accent));
  box-shadow: 0 0 10px var(--primary-glow);
}
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: 12px;
}
.stat-grid-4 { grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
.stat-item {
  text-align: center;
  padding: 16px 10px;
  background: var(--bg-subtle);
  border-radius: var(--radius);
  border: 1px solid var(--border-subtle);
  transition: all var(--duration) var(--ease);
}
.stat-item:hover {
  border-color: color-mix(in srgb, var(--primary) 30%, var(--border));
  box-shadow: var(--shadow-sm);
}
.stat-value {
  font-size: 22px;
  font-weight: 800;
  color: var(--primary);
  letter-spacing: -0.02em;
  font-variant-numeric: tabular-nums;
}
.stat-label {
  font-size: 11px;
  color: var(--text-secondary);
  margin-top: 4px;
  font-weight: 500;
}
.list-block { margin-top: 16px; }
.list-block h4 {
  margin: 0 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.list-table { display: flex; flex-direction: column; gap: 6px; }
.list-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--bg-subtle);
  border-radius: var(--radius);
  border: 1px solid var(--border-subtle);
  transition: all var(--duration) var(--ease);
}
.list-row:hover { background: var(--surface); border-color: color-mix(in srgb, var(--primary) 20%, var(--border)); }
.row-path {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.badge-count {
  font-size: 12px;
  font-weight: 700;
  padding: 2px 10px;
  border-radius: var(--radius-pill);
  background: var(--primary-glow);
  color: var(--primary);
  flex-shrink: 0;
}
</style>
