import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import {
  clearLogs,
  cleanupLogs,
  getFilterOptions,
  getStats,
  getTrace,
  listEntries,
  listTraces,
  type ChainLogEntry,
  type ChainLogFilters,
  type ChainLogStats,
  type ChainTraceSummary,
  type FilterOptions,
  type TraceDetail,
} from '../api/logs'

// 全链路日志 Store：列表（trace 摘要 / 原始条目）、单链路时间线、统计、筛选。
// 视图分「链路列表 → 点开看时间线」两层，两层共用同一份筛选条件。
export const useLogsStore = defineStore('logs', () => {
  // ── 筛选条件（两层视图共用）──
  const filters = ref<ChainLogFilters>({})
  // 视图模式：traces = 一次请求一行；entries = 原始节点
  const view = ref<'traces' | 'entries'>('traces')
  const offset = ref(0)
  const limit = ref(30)

  // ── 列表数据 ──
  const traces = ref<ChainTraceSummary[]>([])
  const entries = ref<ChainLogEntry[]>([])
  const total = ref(0)
  const listLoading = ref(false)
  const listError = ref('')

  // ── 选中的链路时间线 ──
  const activeTraceId = ref('')
  const detail = ref<TraceDetail | null>(null)
  const detailLoading = ref(false)
  const detailError = ref('')

  // ── 统计与筛选项 ──
  const stats = ref<ChainLogStats | null>(null)
  const options = ref<FilterOptions | null>(null)
  const statsLoading = ref(false)

  const hasFilters = computed(() => Object.values(filters.value).some(v => v !== undefined && v !== '' && v !== false))
  const page = computed(() => Math.floor(offset.value / limit.value) + 1)
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / limit.value)))
  const canPrev = computed(() => offset.value > 0)
  const canNext = computed(() => offset.value + limit.value < total.value)

  // 时间线按 seq 升序（后端已排序，这里兜底保证前端渲染顺序稳定）
  const timeline = computed<ChainLogEntry[]>(() => detail.value?.entries ?? [])

  async function fetchList() {
    listLoading.value = true
    listError.value = ''
    try {
      if (view.value === 'traces') {
        const res = await listTraces(filters.value, offset.value, limit.value)
        traces.value = res.traces ?? []
        total.value = res.total
      } else {
        const res = await listEntries(filters.value, offset.value, limit.value)
        entries.value = res.entries ?? []
        total.value = res.total
      }
    } catch (err: any) {
      listError.value = err?.message || '日志加载失败'
      traces.value = []
      entries.value = []
      total.value = 0
    } finally {
      listLoading.value = false
    }
  }

  // 打开一条链路的时间线
  async function openTrace(traceId: string) {
    activeTraceId.value = traceId
    detailLoading.value = true
    detailError.value = ''
    detail.value = null
    try {
      detail.value = await getTrace(traceId)
    } catch (err: any) {
      detailError.value = err?.message || '链路详情加载失败'
    } finally {
      detailLoading.value = false
    }
  }

  function closeTrace() {
    activeTraceId.value = ''
    detail.value = null
    detailError.value = ''
  }

  async function loadStats() {
    statsLoading.value = true
    try {
      stats.value = await getStats(filters.value)
    } catch {
      stats.value = null
    } finally {
      statsLoading.value = false
    }
  }

  async function loadOptions() {
    try {
      options.value = await getFilterOptions()
    } catch {
      options.value = null
    }
  }

  // 设置单个筛选条件（传 undefined/'' 视为清除），回到第一页并刷新
  async function setFilter<K extends keyof ChainLogFilters>(key: K, value: ChainLogFilters[K]) {
    const next = { ...filters.value }
    if (value === undefined || value === '' || value === false) delete next[key]
    else next[key] = value
    filters.value = next
    offset.value = 0
    await Promise.all([fetchList(), loadStats()])
  }

  function resetFilters() {
    filters.value = {}
    offset.value = 0
    view.value = 'traces'
    return Promise.all([fetchList(), loadStats()])
  }

  async function setView(next: 'traces' | 'entries') {
    if (view.value === next) return
    view.value = next
    offset.value = 0
    await fetchList()
  }

  async function changeLimit(next: number) {
    limit.value = next
    offset.value = 0
    await fetchList()
  }

  async function prevPage() {
    if (!canPrev.value) return
    offset.value = Math.max(0, offset.value - limit.value)
    await fetchList()
  }

  async function nextPage() {
    if (!canNext.value) return
    offset.value += limit.value
    await fetchList()
  }

  // 手动触发 TTL / 行数裁剪，随后刷新列表与统计
  async function cleanup() {
    const res = await cleanupLogs()
    await Promise.all([fetchList(), loadStats()])
    return res
  }

  // 清理当前筛选命中的日志（全量清理时 payload 传 all）
  async function clear(opts: { trace_id?: string; all?: boolean } = {}) {
    const res = await clearLogs(opts)
    if (opts.trace_id && opts.trace_id === activeTraceId.value) closeTrace()
    offset.value = 0
    await Promise.all([fetchList(), loadStats(), loadOptions()])
    return res
  }

  // 首屏加载
  async function init() {
    await Promise.all([fetchList(), loadStats(), loadOptions()])
  }

  return {
    filters, view, offset, limit, traces, entries, total,
    listLoading, listError, activeTraceId, detail, detailLoading, detailError,
    stats, options, statsLoading, timeline,
    hasFilters, page, pageCount, canPrev, canNext,
    init, fetchList, openTrace, closeTrace, loadStats, loadOptions,
    setFilter, resetFilters, setView, changeLimit, prevPage, nextPage,
    cleanup, clear,
  }
})
