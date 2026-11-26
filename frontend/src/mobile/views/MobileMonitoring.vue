<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { fetchStats } from '../../api/monitor'
import type { MonitorStats } from '../../types'

const stats = ref<MonitorStats | null>(null)
const loading = ref(true)
const error = ref('')
let timer: ReturnType<typeof setInterval> | undefined

async function load(initial = false) {
  try {
    stats.value = await fetchStats()
    if (initial) error.value = ''
  } catch (err: any) {
    if (initial) error.value = err.message
  } finally {
    if (initial) loading.value = false
  }
}

onMounted(() => {
  load(true)
  timer = setInterval(() => load(false), 5000)
})

onUnmounted(() => {
  if (timer !== undefined) clearInterval(timer)
})

const service = computed(() => stats.value?.service)

function pct(v?: number): string {
  return `${Number(v || 0).toFixed(1)}%`
}

function fmtBytes(v?: number): string {
  const bytes = Number(v || 0)
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GB`
  if (bytes >= 1024 ** 2) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${bytes} B`
}

function uptime(v?: number): string {
  const seconds = Number(v || 0)
  if (seconds >= 86400) return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`
  if (seconds >= 3600) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`
  if (seconds >= 60) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`
  return `${Math.round(seconds)}s`
}
</script>

<template>
  <div class="m-monitor">
    <van-loading v-if="loading" class="loading" />
    <van-empty v-else-if="error" image="network" :description="error" />
    <template v-else-if="service">
      <div class="m-card m-card-pad">
        <div class="m-row">
          <span class="m-row-name">{{ service.name }}</span>
          <span class="m-pill">{{ service.available ? '采样正常' : '采样不可用' }}</span>
        </div>
        <div class="m-row">
          <span class="m-row-name">PID / 运行时间</span>
          <span class="m-row-val">{{ service.pid }} · {{ uptime(service.uptime_seconds) }}</span>
        </div>
      </div>

      <div class="m-sec-title">服务进程</div>
      <div class="m-card m-card-pad">
        <div class="m-metric">
          <div class="m-row">
            <span class="m-row-name">CPU</span>
            <span class="m-row-val">{{ pct(service.cpu_percent) }}</span>
          </div>
          <div class="m-bar"><i :style="{ width: `${Math.min(service.cpu_percent, 100)}%` }"></i></div>
        </div>
        <div class="m-metric">
          <div class="m-row">
            <span class="m-row-name">内存</span>
            <span class="m-row-val">{{ pct(service.memory_percent) }}</span>
          </div>
          <div class="m-bar"><i :style="{ width: `${Math.min(service.memory_percent, 100)}%` }"></i></div>
          <div class="m-row">
            <span class="m-row-name">RSS / VMS</span>
            <span class="m-row-val">{{ fmtBytes(service.memory_rss_bytes) }} / {{ fmtBytes(service.memory_vms_bytes) }}</span>
          </div>
        </div>
      </div>

      <div class="m-sec-title">主机资源</div>
      <div class="m-card m-card-pad">
        <div class="m-metric">
          <div class="m-row">
            <span class="m-row-name">CPU</span>
            <span class="m-row-val">{{ pct(service.host_cpu_percent) }}</span>
          </div>
          <div class="m-bar"><i :style="{ width: `${Math.min(service.host_cpu_percent, 100)}%` }"></i></div>
        </div>
        <div class="m-metric">
          <div class="m-row">
            <span class="m-row-name">内存</span>
            <span class="m-row-val">{{ pct(service.host_memory_percent) }}</span>
          </div>
          <div class="m-bar"><i :style="{ width: `${Math.min(service.host_memory_percent, 100)}%` }"></i></div>
          <div class="m-row">
            <span class="m-row-name">已用 / 总量</span>
            <span class="m-row-val">{{ fmtBytes(service.host_memory_used_bytes) }} / {{ fmtBytes(service.host_memory_total_bytes) }}</span>
          </div>
        </div>
        <p v-if="!service.available" class="m-warning">资源采样不可用，请确认后端已安装 psutil。</p>
      </div>
    </template>
    <van-empty v-else description="服务资源指标不可用" />
  </div>
</template>

<style scoped>
.m-monitor { padding: 8px 12px 24px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.m-sec-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-secondary, #64748b);
  margin: 16px 2px 8px;
}
.m-card {
  background: var(--surface, #fff);
  border: 1px solid var(--border, #eef1f6);
  border-radius: var(--m-card-radius, 16px);
  box-shadow: var(--m-card-shadow, 0 2px 12px rgba(31, 41, 55, 0.06));
}
.m-card-pad { padding: 14px; }
.m-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 30px; }
.m-row-name { font-size: 13px; color: var(--text-secondary, #64748b); }
.m-row-val { font-size: 13px; font-weight: 600; color: var(--text, #0f172a); text-align: right; }
.m-pill {
  font-size: 11px;
  font-weight: 600;
  color: var(--primary, #2563eb);
  background: var(--primary-glow, rgba(37, 99, 235, 0.1));
  border-radius: 999px;
  padding: 4px 10px;
}
.m-metric + .m-metric { margin-top: 14px; padding-top: 12px; border-top: 1px solid var(--border, #eef1f6); }
.m-bar { height: 6px; margin: 8px 0; border-radius: 999px; background: var(--border, #eef1f6); overflow: hidden; }
.m-bar i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--primary, #2563eb), var(--accent, #7c3aed)); }
.m-warning { margin: 10px 0 0; font-size: 12px; color: var(--danger, #ef4444); }
</style>
