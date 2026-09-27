<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useLogsStore } from '../stores/logs'
import {
  LEVELS, LEVEL_LABELS, STAGES, STAGE_LABELS,
  type ChainLogEntry,
} from '../api/logs'

const store = useLogsStore()

// 关键字搜索（本地受控，回车或点按钮提交）
const keyword = ref(String(store.filters.keyword ?? ''))
const sinceHours = ref(0)

onMounted(async () => {
  if (store.total === 0 && store.traces.length === 0 && store.entries.length === 0) {
    await store.init()
  }
})

function applyKeyword() {
  void store.setFilter('keyword', keyword.value.trim())
}

function applySince() {
  const h = Number(sinceHours.value) || 0
  void store.setFilter('since', h > 0 ? Date.now() - h * 3600 * 1000 : undefined)
}

function levelOf(v: string): string {
  return (v || 'INFO').toUpperCase()
}

function stageOf(v: string): string {
  return v || 'system'
}

function stageLabel(v: string): string {
  return STAGE_LABELS[v] || v || '系统'
}

function levelLabel(v: string): string {
  return LEVEL_LABELS[levelOf(v)] || v
}

function fmtTime(ts: number): string {
  if (!ts) return '—'
  const d = new Date(ts)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

function fmtTimeMs(ts: number): string {
  const base = fmtTime(ts)
  return ts ? `${base}.${String(new Date(ts).getMilliseconds()).padStart(3, '0')}` : '—'
}

function fmtDur(ms?: number | null): string {
  if (ms === null || ms === undefined) return '—'
  if (ms >= 60000) return (ms / 60000).toFixed(1) + ' min'
  if (ms >= 1000) return (ms / 1000).toFixed(2) + ' s'
  if (ms >= 1) return Math.round(ms) + ' ms'
  return '<1 ms'
}

function fmtCost(v?: number): string {
  if (!v || v <= 0) return '—'
  return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 6, maximumFractionDigits: 6 })
}

function fmtNum(v?: number): string {
  return (v ?? 0).toLocaleString()
}

// ── 时间线：计算相对首节点的偏移，用于左侧时间轴刻度 ──
const timelineBase = computed(() => {
  const first = store.timeline[0]
  return first ? first.ts : 0
})

interface TimelineRow {
  entry: ChainLogEntry
  offsetMs: number
  depth: number
}

const timelineRows = computed<TimelineRow[]>(() => {
  const byId = new Map<string, ChainLogEntry>()
  for (const e of store.timeline) byId.set(e.id, e)
  return store.timeline.map(e => {
    let depth = 0
    let cur: ChainLogEntry | undefined = e
    const seen = new Set<string>()
    while (cur?.parent_id && depth < 8) {
      const parent: ChainLogEntry | undefined = byId.get(cur.parent_id)
      if (!parent || seen.has(parent.id)) break
      seen.add(parent.id)
      depth += 1
      cur = parent
    }
    return { entry: e, offsetMs: e.ts - timelineBase.value, depth }
  })
})

// 展开的节点 id（查看 data 明细）
const expanded = ref<Set<string>>(new Set())
function toggleData(id: string) {
  const next = new Set(expanded.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  expanded.value = next
}

// LLM 节点的关键指标（直接显示在时间线上，不用展开 data）
function llmBadge(e: ChainLogEntry): string {
  if (stageOf(e.stage) !== 'llm') return ''
  const d = e.data || {}
  const parts: string[] = []
  if (d.prompt_tokens || d.completion_tokens) {
    parts.push(`${fmtNum(d.prompt_tokens)}↑ / ${fmtNum(d.completion_tokens)}↓`)
  }
  if (d.cache_read) parts.push(`缓存${fmtNum(d.cache_read)}`)
  const cost = fmtCost(d.cost)
  if (cost !== '—') parts.push(cost)
  return parts.join(' · ')
}

const counters = computed(() => store.stats?.counters)
const showCleanupResult = ref('')

async function onCleanup() {
  try {
    const res = await store.cleanup()
    showCleanupResult.value = `已裁剪过期 ${res.expired} 条、超限 ${res.overflow} 条`
  } catch (err: any) {
    showCleanupResult.value = `裁剪失败：${err?.message || err}`
  }
}

async function onClearAll() {
  if (!window.confirm('确定清空全部链路日志？该操作不可恢复。')) return
  try {
    const res = await store.clear({ all: true })
    showCleanupResult.value = `已清空 ${res.deleted} 条日志`
  } catch (err: any) {
    showCleanupResult.value = `清空失败：${err?.message || err}`
  }
}
</script>

<template>
  <div class="page-header">
    <h2>日志管理</h2>
    <p>全链路追踪：一次请求的 HTTP、会话、路由、智能体、工具、模型调用与落库</p>
  </div>

  <div class="page-content logs-page">
    <!-- ── 筛选栏 ── -->
    <div class="card filter-card">
      <div class="filter-row">
        <input
          v-model="keyword"
          class="f-input"
          placeholder="搜索事件 / 消息（回车）"
          @keyup.enter="applyKeyword"
        />
        <select class="f-select" :value="store.filters.stage ?? ''" @change="store.setFilter('stage', ($event.target as HTMLSelectElement).value)">
          <option value="">全部阶段</option>
          <option v-for="s in STAGES" :key="s" :value="s">{{ stageLabel(s) }}</option>
        </select>
        <select class="f-select" :value="store.filters.level ?? ''" @change="store.setFilter('level', ($event.target as HTMLSelectElement).value)">
          <option value="">全部级别</option>
          <option v-for="l in LEVELS" :key="l" :value="l">{{ levelLabel(l) }}</option>
        </select>
        <select class="f-select" :value="store.filters.agent_id ?? ''" @change="store.setFilter('agent_id', ($event.target as HTMLSelectElement).value)">
          <option value="">全部智能体</option>
          <option v-for="a in store.options?.agents ?? []" :key="a" :value="a">{{ a }}</option>
        </select>
        <select class="f-select" :value="store.filters.component ?? ''" @change="store.setFilter('component', ($event.target as HTMLSelectElement).value)">
          <option value="">全部组件</option>
          <option v-for="c in store.options?.components ?? []" :key="c" :value="c">{{ c }}</option>
        </select>
        <select v-model.number="sinceHours" class="f-select f-narrow" @change="applySince">
          <option :value="0">全部时间</option>
          <option :value="1">最近 1 小时</option>
          <option :value="24">最近 24 小时</option>
          <option :value="168">最近 7 天</option>
        </select>
        <label class="f-check">
          <input
            type="checkbox"
            :checked="store.filters.has_error === true"
            @change="store.setFilter('has_error', ($event.target as HTMLInputElement).checked)"
          />
          仅看错误
        </label>
        <button class="btn btn-ghost" @click="store.resetFilters(); keyword = ''; sinceHours = 0">重置</button>
      </div>
      <div class="filter-actions">
        <div class="view-toggle">
          <button :class="{ on: store.view === 'traces' }" @click="store.setView('traces')">链路列表</button>
          <button :class="{ on: store.view === 'entries' }" @click="store.setView('entries')">原始节点</button>
        </div>
        <div class="spacer"></div>
        <span v-if="store.hasFilters" class="filter-note">已应用筛选</span>
        <button class="btn btn-ghost" @click="onCleanup">按保留策略裁剪</button>
        <button class="btn btn-danger" @click="onClearAll">清空全部</button>
      </div>
      <p v-if="showCleanupResult" class="filter-result">{{ showCleanupResult }}</p>
    </div>

    <!-- ── 统计概览 ── -->
    <div v-if="store.stats" class="stat-row">
      <div class="stat-mini"><b>{{ fmtNum(store.stats.total) }}</b><span>节点总数</span></div>
      <div class="stat-mini"><b>{{ fmtNum(store.stats.traces) }}</b><span>链路数</span></div>
      <div class="stat-mini"><b>{{ fmtDur(store.stats.avg_duration_ms) }}</b><span>平均节点耗时</span></div>
      <div class="stat-mini err"><b>{{ fmtNum(store.stats.by_level.ERROR || 0) }}</b><span>错误节点</span></div>
      <div class="stat-mini warn"><b>{{ fmtNum(store.stats.by_level.WARNING || 0) }}</b><span>警告节点</span></div>
      <div v-if="counters" class="stat-mini muted">
        <b>{{ fmtNum(counters.written) }}</b>
        <span>已落库 · 队列 {{ counters.queued }}/{{ counters.queue_size }} · 丢弃 {{ counters.dropped }}</span>
      </div>
    </div>

    <!-- ── 单条链路时间线（点开 trace 后置顶展示）── -->
    <div v-if="store.activeTraceId" class="card detail-card">
      <div class="detail-head">
        <div>
          <h3>链路时间线</h3>
          <code class="trace-id">{{ store.activeTraceId }}</code>
        </div>
        <button class="btn btn-ghost" @click="store.closeTrace()">收起</button>
      </div>

      <div v-if="store.detailLoading" class="loading-wrap"><span class="spinner"></span></div>
      <p v-else-if="store.detailError" class="empty-state" style="color:var(--danger)">{{ store.detailError }}</p>

      <div v-else class="timeline">
        <div
          v-for="row in timelineRows"
          :key="row.entry.id"
          class="tl-row"
          :class="['lv-' + levelOf(row.entry.level), 'st-' + stageOf(row.entry.stage)]"
          :style="{ paddingLeft: (8 + row.depth * 14) + 'px' }"
        >
          <div class="tl-gutter">
            <span class="tl-dot" :class="'st-' + stageOf(row.entry.stage)"></span>
            <span class="tl-offset" :title="'自链路起点 ' + fmtDur(row.offsetMs)">+{{ fmtDur(row.offsetMs) }}</span>
          </div>
          <div class="tl-main">
            <div class="tl-line1">
              <span class="tl-stage" :class="'st-' + stageOf(row.entry.stage)">{{ stageLabel(row.entry.stage) }}</span>
              <span class="tl-event">{{ row.entry.event }}</span>
              <span v-if="row.entry.agent_id" class="tl-agent">{{ row.entry.agent_id }}</span>
              <span v-if="row.entry.duration_ms !== null" class="tl-dur">{{ fmtDur(row.entry.duration_ms) }}</span>
              <span class="tl-time">{{ fmtTimeMs(row.entry.ts) }}</span>
              <button
                v-if="Object.keys(row.entry.data || {}).length"
                class="tl-toggle"
                @click="toggleData(row.entry.id)"
              >{{ expanded.has(row.entry.id) ? '收起数据' : '数据' }}</button>
            </div>
            <div v-if="row.entry.message" class="tl-msg">{{ row.entry.message }}</div>
            <div v-if="llmBadge(row.entry)" class="tl-llm">{{ llmBadge(row.entry) }}</div>
            <pre v-if="expanded.has(row.entry.id)" class="tl-data">{{ JSON.stringify(row.entry.data, null, 2) }}</pre>
          </div>
        </div>
        <p v-if="!timelineRows.length && !store.detailLoading" class="empty-state">该链路暂无节点</p>
      </div>
    </div>

    <!-- ── 链路列表 ── -->
    <div v-if="store.view === 'traces'" class="card list-card list-traces">
      <div class="list-head">
        <span>时间</span><span>路径 / 标题</span><span>会话</span><span class="al-r">节点</span><span class="al-r">异常</span><span class="al-r">耗时</span>
      </div>
      <div v-if="store.listLoading && !store.traces.length" class="loading-wrap"><span class="spinner"></span></div>
      <p v-else-if="store.listError" class="empty-state" style="color:var(--danger)">{{ store.listError }}</p>
      <p v-else-if="!store.traces.length" class="empty-state">暂无链路日志</p>
      <div
        v-for="t in store.traces"
        :key="t.trace_id"
        class="trace-row"
        :class="{ active: store.activeTraceId === t.trace_id, hasErr: t.errors > 0 }"
        @click="store.openTrace(t.trace_id)"
      >
        <span class="mono nowrap">{{ fmtTime(t.start_ts) }}</span>
        <span class="trace-title" :title="[t.path, t.title].filter(Boolean).join(' · ')">
          <b>{{ t.path || '—' }}</b>
          <i>{{ t.title }}</i>
        </span>
        <span class="mono dim nowrap" :title="t.session_id">{{ t.session_id ? t.session_id.slice(0, 12) : '—' }}</span>
        <span class="mono al-r">{{ t.nodes }}</span>
        <span class="mono al-r nowrap" :class="t.errors ? 'err' : 'dim'">{{ t.errors || t.warnings || '—' }}</span>
        <span class="mono al-r nowrap">{{ fmtDur(t.duration_ms) }}</span>
      </div>
    </div>

    <!-- ── 原始节点列表 ── -->
    <div v-else class="card list-card list-entries">
      <div class="list-head">
        <span>时间</span><span class="al-c">级别</span><span class="al-c">阶段</span><span>事件</span><span>组件</span><span>消息</span>
      </div>
      <div v-if="store.listLoading && !store.entries.length" class="loading-wrap"><span class="spinner"></span></div>
      <p v-else-if="store.listError" class="empty-state" style="color:var(--danger)">{{ store.listError }}</p>
      <p v-else-if="!store.entries.length" class="empty-state">暂无日志节点</p>
      <div
        v-for="e in store.entries"
        :key="e.id"
        class="entry-row"
        :class="['lv-' + levelOf(e.level)]"
        @click="store.openTrace(e.trace_id)"
      >
        <span class="mono nowrap">{{ fmtTimeMs(e.ts) }}</span>
        <span class="lvl nowrap" :class="'lv-' + levelOf(e.level)">{{ levelLabel(e.level) }}</span>
        <span class="stage-chip nowrap" :class="'st-' + stageOf(e.stage)">{{ stageLabel(e.stage) }}</span>
        <span class="mono ev-name" :title="e.event">{{ e.event }}</span>
        <span class="mono dim ev-comp" :title="e.component">{{ e.component || '—' }}</span>
        <span class="ev-msg" :title="e.message">{{ e.message || '—' }}</span>
      </div>
    </div>

    <!-- ── 分页 ── -->
    <div class="pager">
      <span class="pager-info">共 {{ fmtNum(store.total) }} 条 · 第 {{ store.page }}/{{ store.pageCount }} 页</span>
      <select class="f-select f-narrow" :value="store.limit" @change="store.changeLimit(Number(($event.target as HTMLSelectElement).value))">
        <option :value="30">30 / 页</option>
        <option :value="50">50 / 页</option>
        <option :value="100">100 / 页</option>
        <option :value="200">200 / 页</option>
      </select>
      <button class="btn btn-ghost" :disabled="!store.canPrev" @click="store.prevPage()">上一页</button>
      <button class="btn btn-ghost" :disabled="!store.canNext" @click="store.nextPage()">下一页</button>
    </div>
  </div>
</template>

<style scoped>
.logs-page { display: flex; flex-direction: column; gap: 16px; }

.loading-wrap { display: flex; justify-content: center; padding: 32px; }
.btn-ghost { background: var(--bg-subtle); color: var(--text-secondary); border: 1px solid var(--border-subtle); }
.btn-ghost:hover:not(:disabled) { color: var(--text); border-color: var(--border); }
.btn-ghost:disabled, .btn:disabled { opacity: 0.5; cursor: not-allowed; }

/* ── 筛选栏 ── */
.filter-card { display: flex; flex-direction: column; gap: 10px; padding: 12px 14px; }
.filter-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.f-input, .f-select {
  height: 30px; padding: 0 9px; font-size: 12px; color: var(--text);
  background: var(--bg-subtle); border: 1px solid var(--border-subtle);
  border-radius: var(--radius); outline: none;
}
.f-input { min-width: 220px; flex: 1 1 220px; }
.f-input:focus, .f-select:focus { border-color: color-mix(in srgb, var(--primary) 50%, var(--border)); }
.f-narrow { min-width: 96px; }
.f-check { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--text-secondary); cursor: pointer; }
.filter-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.filter-actions .spacer { flex: 1; }
.filter-note { font-size: 11px; color: var(--primary); font-weight: 600; }
.filter-result { margin: 0; font-size: 11.5px; color: var(--text-secondary); }
.view-toggle { display: inline-flex; border: 1px solid var(--border-subtle); border-radius: var(--radius); overflow: hidden; }
.view-toggle button {
  padding: 5px 12px; font-size: 12px; border: none; cursor: pointer;
  background: var(--bg-subtle); color: var(--text-secondary);
}
.view-toggle button.on { background: var(--primary); color: #fff; font-weight: 600; }

/* ── 统计 ── */
.stat-row { display: flex; flex-wrap: wrap; gap: 10px; }
.stat-mini {
  display: flex; flex-direction: column; gap: 2px; padding: 8px 12px; min-width: 108px;
  background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: var(--radius);
}
.stat-mini b { font-size: 17px; font-weight: 800; color: var(--text); font-variant-numeric: tabular-nums; }
.stat-mini span { font-size: 10.5px; color: var(--text-secondary); }
.stat-mini.err b { color: var(--danger); }
.stat-mini.warn b { color: #f59e0b; }
.stat-mini.muted b { color: var(--text-secondary); }

/* ── 时间线 ── */
.detail-card { padding: 12px 14px; display: flex; flex-direction: column; gap: 10px; }
.detail-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.detail-head h3 { margin: 0 0 3px; font-size: 14px; font-weight: 700; }
.trace-id {
  font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11px; color: var(--text-secondary);
  word-break: break-all;
}
.timeline { display: flex; flex-direction: column; gap: 2px; max-height: 60vh; overflow-y: auto; }
.tl-row { display: flex; gap: 8px; padding: 5px 6px; border-radius: var(--radius); border-left: 2px solid transparent; }
.tl-row:hover { background: var(--bg-subtle); }
.tl-row.lv-ERROR { border-left-color: var(--danger); background: color-mix(in srgb, var(--danger) 6%, transparent); }
.tl-row.lv-WARNING { border-left-color: #f59e0b; }
.tl-row.lv-DEBUG { opacity: 0.7; }
.tl-gutter { display: flex; flex-direction: column; align-items: center; gap: 2px; width: 62px; flex-shrink: 0; }
.tl-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--text-secondary); }
.tl-dot.st-http { background: #38bdf8; }
.tl-dot.st-agent { background: #a78bfa; }
.tl-dot.st-tool { background: #34d399; }
.tl-dot.st-llm { background: var(--primary); }
.tl-dot.st-permission { background: #f59e0b; }
.tl-dot.st-persist { background: #94a3b8; }
.tl-offset { font-size: 9.5px; color: var(--text-secondary); font-variant-numeric: tabular-nums; white-space: nowrap; }
.tl-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.tl-line1 { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; }
.tl-stage {
  font-size: 10px; font-weight: 700; padding: 1px 6px; border-radius: var(--radius-pill);
  background: var(--bg-subtle); color: var(--text-secondary); border: 1px solid var(--border-subtle);
}
.tl-stage.st-http { color: #38bdf8; }
.tl-stage.st-agent { color: #a78bfa; }
.tl-stage.st-tool { color: #34d399; }
.tl-stage.st-llm { color: var(--primary); }
.tl-stage.st-permission { color: #f59e0b; }
.tl-event {
  font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11.5px; font-weight: 600;
  color: var(--text); word-break: break-all;
}
.tl-agent { font-size: 10.5px; color: #a78bfa; font-weight: 600; }
.tl-dur { font-size: 10.5px; color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.tl-time { font-size: 10px; color: var(--text-secondary); font-variant-numeric: tabular-nums; margin-left: auto; }
.tl-toggle { font-size: 10px; padding: 1px 7px; border-radius: var(--radius-pill); cursor: pointer;
  background: var(--bg-subtle); color: var(--text-secondary); border: 1px solid var(--border-subtle); }
.tl-msg { font-size: 11.5px; color: var(--text-secondary); word-break: break-word; }
.tl-llm { font-size: 10.5px; color: var(--primary); font-variant-numeric: tabular-nums; }
.tl-data {
  margin: 4px 0 0; padding: 7px 9px; font-size: 10.5px; line-height: 1.5;
  font-family: 'JetBrains Mono', Consolas, monospace; white-space: pre-wrap; word-break: break-all;
  background: var(--bg-subtle); border: 1px solid var(--border-subtle); border-radius: var(--radius);
  color: var(--text-secondary); max-height: 220px; overflow: auto;
}

/* ── 列表 ──
   表头与数据行必须共用同一套 grid 列模板，否则「内容与列」永远对不上。
   两种视图列宽不同，故在 list-card 上用视图修饰类分别绑定 list-head + 数据行。
   数值/胶囊类窄列统一右对齐或居中，表头同步用 .al-r / .al-c 保持一致。 */
.list-card { padding: 0; overflow: hidden; }
.list-head, .trace-row, .entry-row {
  display: grid; gap: 8px; align-items: center; padding: 7px 12px; font-size: 12px;
}
/* 时间 104px = "MM-DD HH:MM:SS" 14 字符 × 7.2px；会话 92px = 12 字符 + 留白 */
.list-traces .list-head,
.list-traces .trace-row { grid-template-columns: 104px minmax(0, 1fr) 92px 44px 44px 70px; }
/* 时间 134px = "MM-DD HH:MM:SS.mmm" 18 字符 × 7.2px；
   级别 44px = 最宽「严重」胶囊(2 CJK@10px + padding) 与表头「级别」取大；阶段 66px = 「权限审批」胶囊 */
.list-entries .list-head,
.list-entries .entry-row { grid-template-columns: 134px 44px 66px minmax(0, 1fr) 130px minmax(0, 1.4fr); }
.list-head {
  color: var(--text-secondary); font-weight: 600; font-size: 10.5px;
  text-transform: uppercase; letter-spacing: 0.04em;
  border-bottom: 1px solid var(--border-subtle); background: var(--bg-subtle);
}
/* 单元格默认 min-width:auto 会被内容顶开，min-width:0 才能让 ellipsis 生效 */
.list-head > span, .trace-row > span, .entry-row > span { min-width: 0; }
.trace-row, .entry-row { border-bottom: 1px solid var(--border-subtle); cursor: pointer; }
.trace-row:hover, .entry-row:hover { background: var(--bg-subtle); }
.trace-row.active { background: color-mix(in srgb, var(--primary) 10%, transparent); }
.trace-row.hasErr .trace-title b { color: var(--danger); }
.mono { font-family: 'JetBrains Mono', Consolas, monospace; font-variant-numeric: tabular-nums; }
.nowrap { white-space: nowrap; }
.al-r { text-align: right; }
.al-c { text-align: center; }
.dim { color: var(--text-secondary); }
.err { color: var(--danger); font-weight: 700; }
.trace-title { display: flex; flex-direction: column; min-width: 0; }
.trace-title b { font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.trace-title i {
  font-style: normal; font-size: 11px; color: var(--text-secondary);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
/* justify-self:center 让胶囊贴合文字宽度，而不是拉满整列 */
.lvl {
  justify-self: center; font-size: 10px; font-weight: 700; text-align: center;
  padding: 1px 6px; border-radius: var(--radius-pill);
}
.lvl.lv-INFO { color: var(--text-secondary); }
.lvl.lv-DEBUG { color: var(--text-secondary); opacity: 0.7; }
.lvl.lv-WARNING { color: #f59e0b; }
.lvl.lv-ERROR, .lvl.lv-CRITICAL { color: var(--danger); }
.stage-chip {
  justify-self: center; font-size: 10px; padding: 1px 6px; border-radius: var(--radius-pill);
  background: var(--bg-subtle); color: var(--text-secondary);
}
.ev-name, .ev-comp, .ev-msg { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ev-msg { color: var(--text-secondary); }

/* ── 分页 ── */
.pager { display: flex; align-items: center; gap: 8px; }
.pager-info { font-size: 11.5px; color: var(--text-secondary); margin-right: auto; }
</style>
