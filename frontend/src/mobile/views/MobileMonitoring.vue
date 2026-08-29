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
      <div class="section">
        <div class="sec-title">HTTP 请求</div>
        <van-cell-group inset>
          <van-cell title="请求总数" :value="num(stats.requests.total)" />
          <van-cell
            v-for="(count, path) in stats.requests.by_path"
            :key="path"
            :title="path"
            :value="num(count)"
            title-class="mono"
          />
          <van-cell
            v-for="(count, status) in stats.requests.by_status"
            :key="'s' + status"
            :title="`状态 ${status}`"
            :value="num(count)"
          />
        </van-cell-group>
      </div>

      <div class="section">
        <div class="sec-title">LLM 调用</div>
        <van-cell-group inset>
          <van-cell title="调用总数" :value="num(stats.model_calls.total)" />
          <van-cell title="输入 Token" :value="num(stats.model_calls.total_prompt_tokens)" />
          <van-cell title="输出 Token" :value="num(stats.model_calls.total_completion_tokens)" />
          <van-cell title="总耗时" :value="ms(stats.model_calls.total_duration_ms)" />
          <van-cell title="平均耗时" :value="ms(stats.model_calls.avg_duration_ms)" />
          <van-cell title="工具轮数" :value="num(stats.model_calls.tool_rounds_total)" />
          <van-cell title="平均工具轮数" :value="String(stats.model_calls.avg_tool_rounds)" />
        </van-cell-group>

        <div class="sec-title sub">按模型</div>
        <van-cell-group inset>
          <van-cell
            v-for="(count, model) in stats.model_calls.by_model"
            :key="model"
            :title="model"
            :value="num(count)"
            title-class="mono"
          />
        </van-cell-group>
      </div>
    </template>
  </div>
</template>

<style scoped>
.m-monitor { padding: 8px 0 16px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.section { margin-bottom: 4px; }
.sec-title { font-size: 14px; font-weight: 600; color: #1e293b; padding: 12px 16px 8px; }
.sec-title.sub { padding-top: 4px; }
:deep(.mono) { font-family: 'Cascadia Code', Consolas, monospace; font-size: 12px; }
</style>
