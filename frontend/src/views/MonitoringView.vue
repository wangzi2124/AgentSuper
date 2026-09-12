<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
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
function fmtCost(v?: number) {
  if (!v || v <= 0) return '免费'
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: 6, maximumFractionDigits: 6 })}`
}

interface ModelRow {
  model: string
  calls: number
  input: number
  output: number
  reasoning: number
  cacheRead: number
  cacheWrite: number
  cost: number
}

const modelRows = computed<ModelRow[]>(() => {
  const mc = stats.value?.model_calls
  if (!mc) return []
  const models = new Set<string>(Object.keys(mc.by_model || {}))
  for (const k of Object.keys(mc.prompt_tokens_by_model || {})) models.add(k)
  for (const k of Object.keys(mc.cost_by_model || {})) models.add(k)
  return [...models].map(m => ({
    model: m,
    calls: mc.by_model?.[m] ?? 0,
    input: mc.prompt_tokens_by_model?.[m] ?? 0,
    output: mc.completion_tokens_by_model?.[m] ?? 0,
    reasoning: mc.reasoning_tokens_by_model?.[m] ?? 0,
    cacheRead: mc.cache_read_by_model?.[m] ?? 0,
    cacheWrite: mc.cache_write_by_model?.[m] ?? 0,
    cost: mc.cost_by_model?.[m] ?? 0,
  })).sort((a, b) => b.calls - a.calls)
})
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
      <!-- Model Calls（置顶：LLM 调用为最重要的具体信息） -->
      <section class="monitor-section">
        <div class="section-head">
          <span class="section-dot"></span>
          <h3>LLM 调用</h3>
        </div>
        <div class="card stat-card">
          <div v-if="modelRows.length" class="list-block">
            <h4>按模型明细（Token / 成本）</h4>
            <div class="model-table">
              <div class="model-table-head">
                <span>模型</span><span>调用</span><span>输入</span><span>输出</span><span>推理</span><span>缓存</span><span>成本</span>
              </div>
              <div v-for="row in modelRows" :key="row.model" class="model-table-row">
                <span class="mt-model">{{ row.model }}</span>
                <span class="mt-cell">{{ num(row.calls) }}</span>
                <span class="mt-cell">{{ num(row.input) }}</span>
                <span class="mt-cell">{{ num(row.output) }}</span>
                <span class="mt-cell">{{ num(row.reasoning) }}</span>
                <span class="mt-cell">{{ num(row.cacheRead) }}→{{ num(row.cacheWrite) }}</span>
                <span class="mt-cell cost">{{ fmtCost(row.cost) }}</span>
              </div>
            </div>
          </div>
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
              <div class="stat-value">{{ num(stats.model_calls.total_reasoning_tokens || 0) }}</div>
              <div class="stat-label">推理 Token</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ num(stats.model_calls.total_cache_read || 0) }}</div>
              <div class="stat-label">缓存命中</div>
            </div>
            <div class="stat-item">
              <div class="stat-value">{{ fmtCost(stats.total_cost) }}</div>
              <div class="stat-label">总成本 (USD)</div>
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
            <h4>按模型调用次数</h4>
            <div class="list-table">
              <div v-for="(count, model) in stats.model_calls.by_model" :key="model" class="list-row">
                <span class="row-path">{{ model }}</span>
                <span class="badge badge-count">{{ num(count) }}</span>
              </div>
            </div>
          </div>
        </div>
      </section>

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
.model-table { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; overflow-x: auto; }
.model-table-head, .model-table-row { display: grid; grid-template-columns: 2fr 0.7fr 0.8fr 0.8fr 0.8fr 1fr 0.8fr; gap: 6px; align-items: center; font-size: 11px; padding: 6px 10px; }
.model-table-head { color: var(--text-secondary); font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em; }
.model-table-head span { text-align: right; }
.model-table-head span:first-child { text-align: left; }
.model-table-row { background: var(--bg-subtle); border-radius: var(--radius); border: 1px solid var(--border-subtle); }
.model-table-row:hover { background: var(--surface); border-color: color-mix(in srgb, var(--primary) 20%, var(--border)); }
.mt-model { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11.5px; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.mt-cell { font-variant-numeric: tabular-nums; color: var(--text); white-space: nowrap; text-align: right; }
.mt-cell.cost { color: #22c55e; font-weight: 600; }
</style>
