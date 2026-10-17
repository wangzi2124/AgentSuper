/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { computed, onMounted, ref } from 'vue';
import { useLogsStore } from '../stores/logs';
import { LEVELS, LEVEL_LABELS, STAGES, STAGE_LABELS, } from '../api/logs';
const store = useLogsStore();
// 关键字搜索（本地受控，回车或点按钮提交）
const keyword = ref(String(store.filters.keyword ?? ''));
const sinceHours = ref(0);
onMounted(async () => {
    if (store.total === 0 && store.traces.length === 0 && store.entries.length === 0) {
        await store.init();
    }
});
function applyKeyword() {
    void store.setFilter('keyword', keyword.value.trim());
}
function applySince() {
    const h = Number(sinceHours.value) || 0;
    void store.setFilter('since', h > 0 ? Date.now() - h * 3600 * 1000 : undefined);
}
function levelOf(v) {
    return (v || 'INFO').toUpperCase();
}
function stageOf(v) {
    return v || 'system';
}
function stageLabel(v) {
    return STAGE_LABELS[v] || v || '系统';
}
function levelLabel(v) {
    return LEVEL_LABELS[levelOf(v)] || v;
}
function fmtTime(ts) {
    if (!ts)
        return '—';
    const d = new Date(ts);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function fmtTimeMs(ts) {
    const base = fmtTime(ts);
    return ts ? `${base}.${String(new Date(ts).getMilliseconds()).padStart(3, '0')}` : '—';
}
function fmtDur(ms) {
    if (ms === null || ms === undefined)
        return '—';
    if (ms >= 60000)
        return (ms / 60000).toFixed(1) + ' min';
    if (ms >= 1000)
        return (ms / 1000).toFixed(2) + ' s';
    if (ms >= 1)
        return Math.round(ms) + ' ms';
    return '<1 ms';
}
function fmtCost(v) {
    if (!v || v <= 0)
        return '—';
    return '$' + v.toLocaleString('en-US', { minimumFractionDigits: 6, maximumFractionDigits: 6 });
}
function fmtNum(v) {
    return (v ?? 0).toLocaleString();
}
// ── 时间线：计算相对首节点的偏移，用于左侧时间轴刻度 ──
const timelineBase = computed(() => {
    const first = store.timeline[0];
    return first ? first.ts : 0;
});
const timelineRows = computed(() => {
    const byId = new Map();
    for (const e of store.timeline)
        byId.set(e.id, e);
    return store.timeline.map(e => {
        let depth = 0;
        let cur = e;
        const seen = new Set();
        while (cur?.parent_id && depth < 8) {
            const parent = byId.get(cur.parent_id);
            if (!parent || seen.has(parent.id))
                break;
            seen.add(parent.id);
            depth += 1;
            cur = parent;
        }
        return { entry: e, offsetMs: e.ts - timelineBase.value, depth };
    });
});
// 展开的节点 id（查看 data 明细）
const expanded = ref(new Set());
function toggleData(id) {
    const next = new Set(expanded.value);
    if (next.has(id))
        next.delete(id);
    else
        next.add(id);
    expanded.value = next;
}
// LLM 节点的关键指标（直接显示在时间线上，不用展开 data）
function llmBadge(e) {
    if (stageOf(e.stage) !== 'llm')
        return '';
    const d = e.data || {};
    const parts = [];
    if (d.prompt_tokens || d.completion_tokens) {
        parts.push(`${fmtNum(d.prompt_tokens)}↑ / ${fmtNum(d.completion_tokens)}↓`);
    }
    if (d.cache_read)
        parts.push(`缓存${fmtNum(d.cache_read)}`);
    const cost = fmtCost(d.cost);
    if (cost !== '—')
        parts.push(cost);
    return parts.join(' · ');
}
const counters = computed(() => store.stats?.counters);
const showCleanupResult = ref('');
async function onCleanup() {
    try {
        const res = await store.cleanup();
        showCleanupResult.value = `已裁剪过期 ${res.expired} 条、超限 ${res.overflow} 条`;
    }
    catch (err) {
        showCleanupResult.value = `裁剪失败：${err?.message || err}`;
    }
}
async function onClearAll() {
    if (!window.confirm('确定清空全部链路日志？该操作不可恢复。'))
        return;
    try {
        const res = await store.clear({ all: true });
        showCleanupResult.value = `已清空 ${res.deleted} 条日志`;
    }
    catch (err) {
        showCleanupResult.value = `清空失败：${err?.message || err}`;
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['btn-ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['f-input']} */ ;
/** @type {__VLS_StyleScopedClasses['f-input']} */ ;
/** @type {__VLS_StyleScopedClasses['f-select']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['view-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['view-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-head']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-row']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-row']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-row']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-row']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-stage']} */ ;
/** @type {__VLS_StyleScopedClasses['st-http']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-stage']} */ ;
/** @type {__VLS_StyleScopedClasses['st-agent']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-stage']} */ ;
/** @type {__VLS_StyleScopedClasses['st-tool']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-stage']} */ ;
/** @type {__VLS_StyleScopedClasses['st-llm']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-stage']} */ ;
/** @type {__VLS_StyleScopedClasses['st-permission']} */ ;
/** @type {__VLS_StyleScopedClasses['list-head']} */ ;
/** @type {__VLS_StyleScopedClasses['list-traces']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-row']} */ ;
/** @type {__VLS_StyleScopedClasses['list-head']} */ ;
/** @type {__VLS_StyleScopedClasses['list-entries']} */ ;
/** @type {__VLS_StyleScopedClasses['entry-row']} */ ;
/** @type {__VLS_StyleScopedClasses['list-head']} */ ;
/** @type {__VLS_StyleScopedClasses['list-head']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-row']} */ ;
/** @type {__VLS_StyleScopedClasses['entry-row']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-row']} */ ;
/** @type {__VLS_StyleScopedClasses['entry-row']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-row']} */ ;
/** @type {__VLS_StyleScopedClasses['entry-row']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-row']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-row']} */ ;
/** @type {__VLS_StyleScopedClasses['err']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-title']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-title']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-title']} */ ;
/** @type {__VLS_StyleScopedClasses['lvl']} */ ;
/** @type {__VLS_StyleScopedClasses['lvl']} */ ;
/** @type {__VLS_StyleScopedClasses['lv-DEBUG']} */ ;
/** @type {__VLS_StyleScopedClasses['lvl']} */ ;
/** @type {__VLS_StyleScopedClasses['lv-WARNING']} */ ;
/** @type {__VLS_StyleScopedClasses['lvl']} */ ;
/** @type {__VLS_StyleScopedClasses['lv-ERROR']} */ ;
/** @type {__VLS_StyleScopedClasses['lvl']} */ ;
/** @type {__VLS_StyleScopedClasses['ev-msg']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-content logs-page" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card filter-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "filter-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ onKeyup: (__VLS_ctx.applyKeyword) },
    ...{ class: "f-input" },
    placeholder: "搜索事件 / 消息（回车）",
});
(__VLS_ctx.keyword);
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    ...{ onChange: (...[$event]) => {
            __VLS_ctx.store.setFilter('stage', $event.target.value);
        } },
    ...{ class: "f-select" },
    value: (__VLS_ctx.store.filters.stage ?? ''),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "",
});
for (const [s] of __VLS_getVForSourceType((__VLS_ctx.STAGES))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        key: (s),
        value: (s),
    });
    (__VLS_ctx.stageLabel(s));
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    ...{ onChange: (...[$event]) => {
            __VLS_ctx.store.setFilter('level', $event.target.value);
        } },
    ...{ class: "f-select" },
    value: (__VLS_ctx.store.filters.level ?? ''),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "",
});
for (const [l] of __VLS_getVForSourceType((__VLS_ctx.LEVELS))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        key: (l),
        value: (l),
    });
    (__VLS_ctx.levelLabel(l));
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    ...{ onChange: (...[$event]) => {
            __VLS_ctx.store.setFilter('agent_id', $event.target.value);
        } },
    ...{ class: "f-select" },
    value: (__VLS_ctx.store.filters.agent_id ?? ''),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "",
});
for (const [a] of __VLS_getVForSourceType((__VLS_ctx.store.options?.agents ?? []))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        key: (a),
        value: (a),
    });
    (a);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    ...{ onChange: (...[$event]) => {
            __VLS_ctx.store.setFilter('component', $event.target.value);
        } },
    ...{ class: "f-select" },
    value: (__VLS_ctx.store.filters.component ?? ''),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "",
});
for (const [c] of __VLS_getVForSourceType((__VLS_ctx.store.options?.components ?? []))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        key: (c),
        value: (c),
    });
    (c);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    ...{ onChange: (__VLS_ctx.applySince) },
    value: (__VLS_ctx.sinceHours),
    ...{ class: "f-select f-narrow" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: (0),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: (1),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: (24),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: (168),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
    ...{ class: "f-check" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ onChange: (...[$event]) => {
            __VLS_ctx.store.setFilter('has_error', $event.target.checked);
        } },
    type: "checkbox",
    checked: (__VLS_ctx.store.filters.has_error === true),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.store.resetFilters();
            __VLS_ctx.keyword = '';
            __VLS_ctx.sinceHours = 0;
        } },
    ...{ class: "btn btn-ghost" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "filter-actions" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "view-toggle" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.store.setView('traces');
        } },
    ...{ class: ({ on: __VLS_ctx.store.view === 'traces' }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.store.setView('entries');
        } },
    ...{ class: ({ on: __VLS_ctx.store.view === 'entries' }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "spacer" },
});
if (__VLS_ctx.store.hasFilters) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "filter-note" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.onCleanup) },
    ...{ class: "btn btn-ghost" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.onClearAll) },
    ...{ class: "btn btn-danger" },
});
if (__VLS_ctx.showCleanupResult) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "filter-result" },
    });
    (__VLS_ctx.showCleanupResult);
}
if (__VLS_ctx.store.stats) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "stat-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "stat-mini" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
    (__VLS_ctx.fmtNum(__VLS_ctx.store.stats.total));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "stat-mini" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
    (__VLS_ctx.fmtNum(__VLS_ctx.store.stats.traces));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "stat-mini" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
    (__VLS_ctx.fmtDur(__VLS_ctx.store.stats.avg_duration_ms));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "stat-mini err" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
    (__VLS_ctx.fmtNum(__VLS_ctx.store.stats.by_level.ERROR || 0));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "stat-mini warn" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
    (__VLS_ctx.fmtNum(__VLS_ctx.store.stats.by_level.WARNING || 0));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    if (__VLS_ctx.counters) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "stat-mini muted" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
        (__VLS_ctx.fmtNum(__VLS_ctx.counters.written));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (__VLS_ctx.counters.queued);
        (__VLS_ctx.counters.queue_size);
        (__VLS_ctx.counters.dropped);
    }
}
if (__VLS_ctx.store.activeTraceId) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "card detail-card" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "detail-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.h3, __VLS_intrinsicElements.h3)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({
        ...{ class: "trace-id" },
    });
    (__VLS_ctx.store.activeTraceId);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.store.activeTraceId))
                    return;
                __VLS_ctx.store.closeTrace();
            } },
        ...{ class: "btn btn-ghost" },
    });
    if (__VLS_ctx.store.detailLoading) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "loading-wrap" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "spinner" },
        });
    }
    else if (__VLS_ctx.store.detailError) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "empty-state" },
            ...{ style: {} },
        });
        (__VLS_ctx.store.detailError);
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "timeline" },
        });
        for (const [row] of __VLS_getVForSourceType((__VLS_ctx.timelineRows))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                key: (row.entry.id),
                ...{ class: "tl-row" },
                ...{ class: (['lv-' + __VLS_ctx.levelOf(row.entry.level), 'st-' + __VLS_ctx.stageOf(row.entry.stage)]) },
                ...{ style: ({ paddingLeft: (8 + row.depth * 14) + 'px' }) },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "tl-gutter" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "tl-dot" },
                ...{ class: ('st-' + __VLS_ctx.stageOf(row.entry.stage)) },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "tl-offset" },
                title: ('自链路起点 ' + __VLS_ctx.fmtDur(row.offsetMs)),
            });
            (__VLS_ctx.fmtDur(row.offsetMs));
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "tl-main" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "tl-line1" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "tl-stage" },
                ...{ class: ('st-' + __VLS_ctx.stageOf(row.entry.stage)) },
            });
            (__VLS_ctx.stageLabel(row.entry.stage));
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "tl-event" },
            });
            (row.entry.event);
            if (row.entry.agent_id) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "tl-agent" },
                });
                (row.entry.agent_id);
            }
            if (row.entry.duration_ms !== null) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "tl-dur" },
                });
                (__VLS_ctx.fmtDur(row.entry.duration_ms));
            }
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "tl-time" },
            });
            (__VLS_ctx.fmtTimeMs(row.entry.ts));
            if (Object.keys(row.entry.data || {}).length) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.store.activeTraceId))
                                return;
                            if (!!(__VLS_ctx.store.detailLoading))
                                return;
                            if (!!(__VLS_ctx.store.detailError))
                                return;
                            if (!(Object.keys(row.entry.data || {}).length))
                                return;
                            __VLS_ctx.toggleData(row.entry.id);
                        } },
                    ...{ class: "tl-toggle" },
                });
                (__VLS_ctx.expanded.has(row.entry.id) ? '收起数据' : '数据');
            }
            if (row.entry.message) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "tl-msg" },
                });
                (row.entry.message);
            }
            if (__VLS_ctx.llmBadge(row.entry)) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ class: "tl-llm" },
                });
                (__VLS_ctx.llmBadge(row.entry));
            }
            if (__VLS_ctx.expanded.has(row.entry.id)) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
                    ...{ class: "tl-data" },
                });
                (JSON.stringify(row.entry.data, null, 2));
            }
        }
        if (!__VLS_ctx.timelineRows.length && !__VLS_ctx.store.detailLoading) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
                ...{ class: "empty-state" },
            });
        }
    }
}
if (__VLS_ctx.store.view === 'traces') {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "card list-card list-traces" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "list-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "al-r" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "al-r" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "al-r" },
    });
    if (__VLS_ctx.store.listLoading && !__VLS_ctx.store.traces.length) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "loading-wrap" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "spinner" },
        });
    }
    else if (__VLS_ctx.store.listError) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "empty-state" },
            ...{ style: {} },
        });
        (__VLS_ctx.store.listError);
    }
    else if (!__VLS_ctx.store.traces.length) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "empty-state" },
        });
    }
    for (const [t] of __VLS_getVForSourceType((__VLS_ctx.store.traces))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.store.view === 'traces'))
                        return;
                    __VLS_ctx.store.openTrace(t.trace_id);
                } },
            key: (t.trace_id),
            ...{ class: "trace-row" },
            ...{ class: ({ active: __VLS_ctx.store.activeTraceId === t.trace_id, hasErr: t.errors > 0 }) },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mono nowrap" },
        });
        (__VLS_ctx.fmtTime(t.start_ts));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "trace-title" },
            title: ([t.path, t.title].filter(Boolean).join(' · ')),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
        (t.path || '—');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.i, __VLS_intrinsicElements.i)({});
        (t.title);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mono dim nowrap" },
            title: (t.session_id),
        });
        (t.session_id ? t.session_id.slice(0, 12) : '—');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mono al-r" },
        });
        (t.nodes);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mono al-r nowrap" },
            ...{ class: (t.errors ? 'err' : 'dim') },
        });
        (t.errors || t.warnings || '—');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mono al-r nowrap" },
        });
        (__VLS_ctx.fmtDur(t.duration_ms));
    }
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "card list-card list-entries" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "list-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "al-c" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "al-c" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    if (__VLS_ctx.store.listLoading && !__VLS_ctx.store.entries.length) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "loading-wrap" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "spinner" },
        });
    }
    else if (__VLS_ctx.store.listError) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "empty-state" },
            ...{ style: {} },
        });
        (__VLS_ctx.store.listError);
    }
    else if (!__VLS_ctx.store.entries.length) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "empty-state" },
        });
    }
    for (const [e] of __VLS_getVForSourceType((__VLS_ctx.store.entries))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.store.view === 'traces'))
                        return;
                    __VLS_ctx.store.openTrace(e.trace_id);
                } },
            key: (e.id),
            ...{ class: "entry-row" },
            ...{ class: (['lv-' + __VLS_ctx.levelOf(e.level)]) },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mono nowrap" },
        });
        (__VLS_ctx.fmtTimeMs(e.ts));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "lvl nowrap" },
            ...{ class: ('lv-' + __VLS_ctx.levelOf(e.level)) },
        });
        (__VLS_ctx.levelLabel(e.level));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "stage-chip nowrap" },
            ...{ class: ('st-' + __VLS_ctx.stageOf(e.stage)) },
        });
        (__VLS_ctx.stageLabel(e.stage));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mono ev-name" },
            title: (e.event),
        });
        (e.event);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "mono dim ev-comp" },
            title: (e.component),
        });
        (e.component || '—');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "ev-msg" },
            title: (e.message),
        });
        (e.message || '—');
    }
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "pager" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "pager-info" },
});
(__VLS_ctx.fmtNum(__VLS_ctx.store.total));
(__VLS_ctx.store.page);
(__VLS_ctx.store.pageCount);
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    ...{ onChange: (...[$event]) => {
            __VLS_ctx.store.changeLimit(Number($event.target.value));
        } },
    ...{ class: "f-select f-narrow" },
    value: (__VLS_ctx.store.limit),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: (30),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: (50),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: (100),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: (200),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.store.prevPage();
        } },
    ...{ class: "btn btn-ghost" },
    disabled: (!__VLS_ctx.store.canPrev),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.store.nextPage();
        } },
    ...{ class: "btn btn-ghost" },
    disabled: (!__VLS_ctx.store.canNext),
});
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
/** @type {__VLS_StyleScopedClasses['page-content']} */ ;
/** @type {__VLS_StyleScopedClasses['logs-page']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-card']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-row']} */ ;
/** @type {__VLS_StyleScopedClasses['f-input']} */ ;
/** @type {__VLS_StyleScopedClasses['f-select']} */ ;
/** @type {__VLS_StyleScopedClasses['f-select']} */ ;
/** @type {__VLS_StyleScopedClasses['f-select']} */ ;
/** @type {__VLS_StyleScopedClasses['f-select']} */ ;
/** @type {__VLS_StyleScopedClasses['f-select']} */ ;
/** @type {__VLS_StyleScopedClasses['f-narrow']} */ ;
/** @type {__VLS_StyleScopedClasses['f-check']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['view-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['on']} */ ;
/** @type {__VLS_StyleScopedClasses['on']} */ ;
/** @type {__VLS_StyleScopedClasses['spacer']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-note']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-result']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-row']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['err']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['warn']} */ ;
/** @type {__VLS_StyleScopedClasses['stat-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['muted']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-card']} */ ;
/** @type {__VLS_StyleScopedClasses['detail-head']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-id']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['timeline']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-row']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-gutter']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-offset']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-main']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-line1']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-stage']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-event']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-agent']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-dur']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-time']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-msg']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-llm']} */ ;
/** @type {__VLS_StyleScopedClasses['tl-data']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['list-card']} */ ;
/** @type {__VLS_StyleScopedClasses['list-traces']} */ ;
/** @type {__VLS_StyleScopedClasses['list-head']} */ ;
/** @type {__VLS_StyleScopedClasses['al-r']} */ ;
/** @type {__VLS_StyleScopedClasses['al-r']} */ ;
/** @type {__VLS_StyleScopedClasses['al-r']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-row']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['hasErr']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['nowrap']} */ ;
/** @type {__VLS_StyleScopedClasses['trace-title']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['dim']} */ ;
/** @type {__VLS_StyleScopedClasses['nowrap']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['al-r']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['al-r']} */ ;
/** @type {__VLS_StyleScopedClasses['nowrap']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['al-r']} */ ;
/** @type {__VLS_StyleScopedClasses['nowrap']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['list-card']} */ ;
/** @type {__VLS_StyleScopedClasses['list-entries']} */ ;
/** @type {__VLS_StyleScopedClasses['list-head']} */ ;
/** @type {__VLS_StyleScopedClasses['al-c']} */ ;
/** @type {__VLS_StyleScopedClasses['al-c']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['entry-row']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['nowrap']} */ ;
/** @type {__VLS_StyleScopedClasses['lvl']} */ ;
/** @type {__VLS_StyleScopedClasses['nowrap']} */ ;
/** @type {__VLS_StyleScopedClasses['stage-chip']} */ ;
/** @type {__VLS_StyleScopedClasses['nowrap']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['ev-name']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['dim']} */ ;
/** @type {__VLS_StyleScopedClasses['ev-comp']} */ ;
/** @type {__VLS_StyleScopedClasses['ev-msg']} */ ;
/** @type {__VLS_StyleScopedClasses['pager']} */ ;
/** @type {__VLS_StyleScopedClasses['pager-info']} */ ;
/** @type {__VLS_StyleScopedClasses['f-select']} */ ;
/** @type {__VLS_StyleScopedClasses['f-narrow']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-ghost']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-ghost']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            LEVELS: LEVELS,
            STAGES: STAGES,
            store: store,
            keyword: keyword,
            sinceHours: sinceHours,
            applyKeyword: applyKeyword,
            applySince: applySince,
            levelOf: levelOf,
            stageOf: stageOf,
            stageLabel: stageLabel,
            levelLabel: levelLabel,
            fmtTime: fmtTime,
            fmtTimeMs: fmtTimeMs,
            fmtDur: fmtDur,
            fmtNum: fmtNum,
            timelineRows: timelineRows,
            expanded: expanded,
            toggleData: toggleData,
            llmBadge: llmBadge,
            counters: counters,
            showCleanupResult: showCleanupResult,
            onCleanup: onCleanup,
            onClearAll: onClearAll,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
