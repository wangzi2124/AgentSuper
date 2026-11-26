<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { fetchStats } from '../api/monitor'
import type { MonitorStats } from '../types'

const stats = ref<MonitorStats | null>(null)
const loading = ref(true)
const error = ref('')
const cpuHistory = ref<number[]>([])
const memHistory = ref<number[]>([])
let timer: ReturnType<typeof setInterval> | undefined

async function load(initial = false) {
  try {
    stats.value = await fetchStats()
    const service = stats.value.service
    if (service?.available) {
      cpuHistory.value = [...cpuHistory.value, service.cpu_percent].slice(-60)
      memHistory.value = [...memHistory.value, service.memory_percent].slice(-60)
    }
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

function sparkPoints(values: number[], width = 120, height = 32): string {
  if (!values.length) return ''
  const max = Math.max(100, ...values)
  const step = values.length > 1 ? width / (values.length - 1) : width
  return values
    .map((value, index) => `${index * step},${height - Math.min(value, max) / max * height}`)
    .join(' ')
}
</script>

<template>
  <div class="page-header">
    <h2>运行监控</h2>
    <p>后端服务 CPU / 内存实时状态</p>
  </div>
  <div class="page-content">
    <div v-if="loading" class="loading-wrap">
      <span class="spinner"></span>
    </div>

    <div v-else-if="error" class="empty-state">
      <p style="color:var(--danger);">资源指标加载失败：{{ error }}</p>
    </div>

    <div v-else-if="service" class="monitor-wrap">
      <section class="monitor-section">
        <div class="section-head">
          <span class="section-dot"></span>
          <h3>服务资源</h3>
        </div>
        <div class="card stat-card svc-card">
          <div class="svc-head">
            <div>
              <div class="svc-title">{{ service.name }}</div>
              <div class="svc-sub">PID {{ service.pid }} · 已运行 {{ uptime(service.uptime_seconds) }}</div>
            </div>
            <span class="svc-pill" :class="service.available ? 'svc-pill-ok' : 'svc-pill-bad'">
              {{ service.available ? '采样正常' : '采样不可用' }}
            </span>
          </div>

          <div class="svc-grid">
            <div class="svc-metric">
              <div class="svc-metric-head">
                <span>进程 CPU</span>
                <strong class="svc-cpu-value">{{ pct(service.cpu_percent) }}</strong>
              </div>
              <div class="svc-bar"><i :style="{ width: `${Math.min(service.cpu_percent, 100)}%` }"></i></div>
              <svg class="svc-spark" viewBox="0 0 120 32" preserveAspectRatio="none">
                <polyline :points="sparkPoints(cpuHistory)" />
              </svg>
            </div>
            <div class="svc-metric">
              <div class="svc-metric-head">
                <span>进程内存</span>
                <strong class="svc-mem-value">{{ pct(service.memory_percent) }}</strong>
              </div>
              <div class="svc-bar"><i :style="{ width: `${Math.min(service.memory_percent, 100)}%` }"></i></div>
              <svg class="svc-spark" viewBox="0 0 120 32" preserveAspectRatio="none">
                <polyline :points="sparkPoints(memHistory)" />
              </svg>
              <div class="svc-sub">RSS {{ fmtBytes(service.memory_rss_bytes) }} · VMS {{ fmtBytes(service.memory_vms_bytes) }}</div>
            </div>
            <div class="svc-metric">
              <div class="svc-metric-head">
                <span>主机 CPU</span>
                <strong>{{ pct(service.host_cpu_percent) }}</strong>
              </div>
              <div class="svc-bar"><i :style="{ width: `${Math.min(service.host_cpu_percent, 100)}%` }"></i></div>
              <div class="svc-sub">宿主机逻辑核平均值</div>
            </div>
            <div class="svc-metric">
              <div class="svc-metric-head">
                <span>主机内存</span>
                <strong>{{ pct(service.host_memory_percent) }}</strong>
              </div>
              <div class="svc-bar"><i :style="{ width: `${Math.min(service.host_memory_percent, 100)}%` }"></i></div>
              <div class="svc-sub">{{ fmtBytes(service.host_memory_used_bytes) }} / {{ fmtBytes(service.host_memory_total_bytes) }}</div>
            </div>
          </div>
          <p v-if="!service.available" class="svc-warning">资源采样不可用，请确认后端已安装 psutil。</p>
        </div>
      </section>
    </div>

    <div v-else class="empty-state">
      <p>服务资源指标不可用</p>
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
.stat-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg, 16px);
  padding: 20px;
  box-shadow: var(--shadow-sm);
}
.svc-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 18px; }
.svc-title { font-size: 17px; font-weight: 700; }
.svc-sub { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
.svc-pill { font-size: 12px; font-weight: 600; padding: 4px 10px; border-radius: var(--radius-pill, 999px); }
.svc-pill-ok { color: #16a34a; background: rgba(34, 197, 94, 0.14); }
.svc-pill-bad { color: #dc2626; background: rgba(239, 68, 68, 0.14); }
.svc-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
.svc-metric {
  padding: 14px;
  border-radius: var(--radius, 12px);
  border: 1px solid var(--border-subtle);
  background: var(--bg-subtle);
}
.svc-metric-head { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; font-size: 13px; }
.svc-metric-head strong { font-size: 20px; font-variant-numeric: tabular-nums; color: var(--primary); }
.svc-bar { height: 6px; margin-top: 10px; border-radius: 999px; background: var(--border); overflow: hidden; }
.svc-bar i { display: block; height: 100%; border-radius: inherit; background: linear-gradient(90deg, var(--primary), var(--accent)); }
.svc-spark { width: 100%; height: 32px; margin-top: 10px; }
.svc-spark polyline { fill: none; stroke: var(--primary); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
.svc-warning { margin: 14px 0 0; font-size: 12px; color: var(--danger); }
</style>
