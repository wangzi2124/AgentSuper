<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { fetchStats } from '../../api/monitor'
import type { MonitorStats } from '../../types'

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
  <div class="m-monitor">
    <van-loading v-if="loading" class="loading" />
    <van-empty v-else-if="error" image="network" :description="error" />
    <template v-else-if="stats">
      <!-- 2x2 统计卡片 -->
      <div class="m-grid">
        <div class="m-stat-card">
          <div class="m-stat-icon ic-blue"><van-icon name="chart-trending-o" /></div>
          <div class="m-stat-meta">
            <div class="m-stat-label">HTTP 请求</div>
            <div class="m-stat-value">{{ num(stats.requests.total) }}</div>
          </div>
        </div>
        <div class="m-stat-card">
          <div class="m-stat-icon ic-violet"><van-icon name="records-o" /></div>
          <div class="m-stat-meta">
            <div class="m-stat-label">LLM 调用</div>
            <div class="m-stat-value">{{ num(stats.model_calls.total) }}</div>
          </div>
        </div>
        <div class="m-stat-card">
          <div class="m-stat-icon ic-cyan"><van-icon name="descending" /></div>
          <div class="m-stat-meta">
            <div class="m-stat-label">输入 Token</div>
            <div class="m-stat-value">{{ num(stats.model_calls.total_prompt_tokens) }}</div>
          </div>
        </div>
        <div class="m-stat-card">
          <div class="m-stat-icon ic-orange"><van-icon name="ascending" /></div>
          <div class="m-stat-meta">
            <div class="m-stat-label">输出 Token</div>
            <div class="m-stat-value">{{ num(stats.model_calls.total_completion_tokens) }}</div>
          </div>
        </div>
      </div>

      <!-- HTTP 明细 -->
      <div class="m-sec-title">HTTP 请求明细</div>
      <div class="m-card m-card-pad">
        <div v-for="(count, path) in stats.requests.by_path" :key="path" class="m-row">
          <span class="m-row-name mono">{{ path }}</span>
          <span class="m-pill">{{ num(count) }}</span>
        </div>
        <div v-if="Object.keys(stats.requests.by_status).length" class="m-tags">
          <span
            v-for="(count, status) in stats.requests.by_status"
            :key="'s' + status"
            class="m-tag"
          >状态 {{ status }} · {{ num(count) }}</span>
        </div>
      </div>

      <!-- LLM 明细 -->
      <div class="m-sec-title">LLM 调用</div>
      <div class="m-card m-card-pad">
        <div class="m-row">
          <span class="m-row-name">总耗时</span>
          <span class="m-row-val">{{ ms(stats.model_calls.total_duration_ms) }}</span>
        </div>
        <div class="m-row">
          <span class="m-row-name">平均耗时</span>
          <span class="m-row-val">{{ ms(stats.model_calls.avg_duration_ms) }}</span>
        </div>
        <div class="m-row">
          <span class="m-row-name">工具轮数</span>
          <span class="m-row-val">{{ num(stats.model_calls.tool_rounds_total) }}（均 {{ stats.model_calls.avg_tool_rounds }}）</span>
        </div>
      </div>

      <!-- 按模型 -->
      <div class="m-sec-title">按模型</div>
      <div class="m-card m-card-pad">
        <div v-for="(count, model) in stats.model_calls.by_model" :key="model" class="m-row">
          <span class="m-row-name mono">{{ model }}</span>
          <span class="m-pill">{{ num(count) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>


<style scoped>
.m-monitor { padding: 8px 12px 24px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }

/* 2x2 统计卡 */
.m-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px; margin-bottom: 4px; }
.m-stat-card {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: var(--m-card-radius, 16px);
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
  padding: 12px;
}
.m-stat-icon {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  color: #fff;
  flex-shrink: 0;
}
.ic-blue   { background: linear-gradient(135deg, #3b82f6, #38bdf8); }
.ic-violet { background: linear-gradient(135deg, #8b5cf6, #6d5ef1); }
.ic-cyan   { background: linear-gradient(135deg, #06b6d4, #22d3ee); }
.ic-orange { background: linear-gradient(135deg, #f59e0b, #f97316); }
.m-stat-meta { min-width: 0; }
.m-stat-label { font-size: 12px; color: var(--text-secondary, #64748b); }
.m-stat-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--text, #1e293b);
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 卡片内行 */
.m-card-pad { padding: 4px 14px; }
.m-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 11px 0;
  border-bottom: 1px dashed var(--border, #eef1f6);
}
.m-row:last-child { border-bottom: none; }
.m-row-name {
  font-size: 13px;
  color: var(--text, #1e293b);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.m-row-val { font-size: 13px; font-weight: 600; color: var(--text, #1e293b); flex-shrink: 0; }
.mono { font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; }
.m-pill {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
  color: var(--m-monitor, #f59e0b);
  background: var(--m-monitor-soft, rgba(245, 158, 11, 0.12));
  padding: 3px 10px;
  border-radius: 999px;
}
.m-tags { display: flex; flex-wrap: wrap; gap: 8px; padding: 10px 0 4px; }
.m-tag {
  font-size: 11px;
  font-weight: 600;
  color: var(--text-secondary, #64748b);
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: 999px;
  padding: 4px 10px;
}
</style>

