/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, computed, onMounted, onBeforeUnmount } from 'vue';
import { useRouter } from 'vue-router';
import { useMultiAgentStore } from '../stores/multiAgent';
import { usePermissionStore } from '../stores/permission';
import { useAuthStore } from '../stores/auth';
import { deleteConversation as apiDelete } from '../api/sessions';
import { estimateTokens } from '../api/models';
function fmtTokens(n) {
    if (!n)
        return '';
    return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)}M` : n >= 1_000 ? `${(n / 1_000).toFixed(1)}k` : `${n}`;
}
function fmtCost(n) {
    if (!n)
        return '';
    return n < 0.01 ? '<$0.01' : `$${n.toFixed(2)}`;
}
function fmtTime(s) {
    if (!s)
        return '';
    try {
        return new Date(s).toLocaleString();
    }
    catch {
        return s;
    }
}
const router = useRouter();
const agent = useMultiAgentStore();
const perm = usePermissionStore();
const auth = useAuthStore();
const searchQuery = ref('');
const editingId = ref(null);
const editingTitle = ref('');
// [ctx-strip 迁移] 会话用量浮层：鼠标悬浮历史项即显示（当前会话额外显示上下文占用）
// [perf] 原实现在每次 mouseenter 都把整个 messages 数组 POST 给 /api/models/estimate-tokens，
// 悬停一列历史 = N 个大请求。改为：悬停停留 250ms 才发 + 按会话/消息指纹缓存 + 同请求去重。
const hoveredId = ref(null);
const ctxEst = ref(null);
const CTX_EST_DEBOUNCE_MS = 250;
let ctxHoverTimer = null;
let ctxEstInFlight = null;
let ctxEstKey = '';
function ctxEstCacheKey() {
    const ms = agent.messages;
    // 指纹：条数 + 每条长度 + 末条内容尾部，变化即视为新上下文
    let sig = `${agent.conversationId}|${ms.length}|`;
    for (const m of ms)
        sig += `${(m.content || '').length},`;
    const last = ms.length ? (ms[ms.length - 1].content || '') : '';
    return sig + last.slice(-64);
}
async function loadCtxEst() {
    if (!agent.messages.length) {
        ctxEst.value = null;
        return;
    }
    const key = ctxEstCacheKey();
    if (key === ctxEstKey && ctxEst.value)
        return;
    // 同一指纹的并发请求共享一个 Promise（悬停快速划过时只发一次）
    if (key === ctxEstKey && ctxEstInFlight) {
        try {
            ctxEst.value = await ctxEstInFlight;
        }
        catch { /* 静默失败 */ }
        return;
    }
    ctxEstKey = key;
    ctxEstInFlight = estimateTokens({
        messages: agent.messages.map(m => ({ role: m.role === 'assistant' ? 'assistant' : 'user', content: m.content || '' })),
    });
    try {
        ctxEst.value = await ctxEstInFlight;
    }
    catch { /* 静默失败 */ }
    finally {
        ctxEstInFlight = null;
    }
}
function ctxLimitFor(c) {
    const m = agent.models.find(mm => mm.id === (c.model?.id || agent.selectedModel));
    return m?.context_length || 0;
}
function ctxPctFor(c) {
    const lim = ctxLimitFor(c);
    if (!lim || !ctxEst.value)
        return 0;
    return Math.min(100, Math.round((ctxEst.value.tokens / lim) * 100));
}
function showUsage(c) {
    hoveredId.value = c.id;
    if (agent.conversationId !== c.id)
        return;
    // 悬停停留一小会儿再请求，避免划过式悬停产生请求风暴
    if (ctxHoverTimer)
        clearTimeout(ctxHoverTimer);
    ctxHoverTimer = setTimeout(() => { ctxHoverTimer = null; void loadCtxEst(); }, CTX_EST_DEBOUNCE_MS);
}
function hideUsage() {
    hoveredId.value = null;
    if (ctxHoverTimer) {
        clearTimeout(ctxHoverTimer);
        ctxHoverTimer = null;
    }
}
onBeforeUnmount(() => { if (ctxHoverTimer)
    clearTimeout(ctxHoverTimer); });
onMounted(() => {
    // 双保险：鉴权启用但未登录时不发会话/工作区请求（登录页不会挂载本组件，防止时序异常）
    if (auth.enabled && !auth.isLoggedIn)
        return;
    agent.loadConversations();
    perm.loadWorkspaces();
});
// 对话列表（按更新时间倒序；不再按目录分组显示路径地址）
const sortedConversations = computed(() => {
    const filtered = agent.conversations.filter(c => !searchQuery.value || c.title.toLowerCase().includes(searchQuery.value.toLowerCase()));
    return [...filtered].sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1));
});
// 新建对话（可选绑定工作目录，目录成为会话 cwd）
function handleNewChat(dir) {
    agent.newChat(dir);
    router.push({ name: 'MultiAgent' });
}
function selectConversation(id) { agent.loadConversation(id); router.push({ name: 'MultiAgentConversation', params: { id } }); }
function startRename(c) { editingId.value = c.id; editingTitle.value = c.title; }
function saveRename() { if (editingId.value && editingTitle.value.trim()) {
    agent.renameConversation(editingId.value, editingTitle.value.trim());
} ; editingId.value = null; }
function cancelRename() { editingId.value = null; }
function handleDelete(e, id) { e.stopPropagation(); if (agent.conversationId === id) {
    agent.newChat();
    router.push({ name: 'MultiAgent' });
} ; apiDelete(id).then(() => agent.loadConversations()); }
// [D1/D2] fork / compact / status：把后端此前无人调用的三条会话路由接进历史列表
async function handleFork(e, id) {
    e.stopPropagation();
    const newId = await agent.forkConversation(id);
    if (newId)
        selectConversation(newId);
}
async function handleCompact(e, id) {
    e.stopPropagation();
    const s = agent.sessions[id];
    if (s?.streamPhase === 'running' || s?.streamPhase === 'queued') {
        agent.setNotice('会话运行中，请先停止后再压缩');
        return;
    }
    await agent.compactConversation(id);
    if (agent.conversationId === id)
        await agent.loadConversation(id);
}
const statusOf = ref({});
async function handleStatus(e, c) {
    e.stopPropagation();
    const st = await agent.fetchSessionStatus(c.id);
    statusOf.value = { ...statusOf.value, [c.id]: st?.status || 'unknown' };
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "chat-history" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "history-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "new-chat-wrap" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.handleNewChat();
        } },
    ...{ class: "new-chat-btn" },
    title: "新建对话",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "16",
    height: "16",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M12 5v14M5 12h14",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "search-box" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.circle)({
    cx: "11",
    cy: "11",
    r: "8",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M21 21l-4.35-4.35",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    placeholder: "搜索...",
});
(__VLS_ctx.searchQuery);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "history-list" },
});
if (__VLS_ctx.sortedConversations.length === 0) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-hint" },
    });
    (__VLS_ctx.searchQuery ? '无匹配结果' : '暂无历史对话');
}
for (const [c] of __VLS_getVForSourceType((__VLS_ctx.sortedConversations))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.selectConversation(c.id);
            } },
        ...{ onMouseenter: (...[$event]) => {
                __VLS_ctx.showUsage(c);
            } },
        ...{ onMouseleave: (...[$event]) => {
                __VLS_ctx.hideUsage();
            } },
        key: (c.id),
        ...{ class: "history-item" },
        ...{ class: ({ active: __VLS_ctx.agent.conversationId === c.id }) },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "item-content" },
    });
    if (__VLS_ctx.editingId === c.id) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
            ...{ onKeyup: (__VLS_ctx.saveRename) },
            ...{ onKeyup: (__VLS_ctx.cancelRename) },
            ...{ onBlur: (__VLS_ctx.saveRename) },
            ...{ class: "rename-input" },
            autofocus: true,
        });
        (__VLS_ctx.editingTitle);
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "item-title-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ onDblclick: (...[$event]) => {
                    if (!!(__VLS_ctx.editingId === c.id))
                        return;
                    __VLS_ctx.startRename(c);
                } },
            ...{ class: "item-title" },
        });
        (c.title);
        if (__VLS_ctx.agent.sessions[c.id]?.streamPhase === 'queued') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "stream-badge queued" },
            });
            (__VLS_ctx.agent.sessions[c.id]?.queuePosition);
        }
        else if (__VLS_ctx.agent.sessions[c.id]?.streamPhase === 'running') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "stream-badge running" },
            });
        }
        if (c.tokens_input || c.tokens_output || c.cost) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "item-meta" },
            });
            if (c.model) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "meta-model" },
                });
                (c.model.id);
            }
            if (c.tokens_input || c.tokens_output) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
                (__VLS_ctx.fmtTokens(c.tokens_input));
                (__VLS_ctx.fmtTokens(c.tokens_output));
            }
            if (c.cost) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    ...{ class: "meta-cost" },
                });
                (__VLS_ctx.fmtCost(c.cost));
            }
        }
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "item-actions" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.handleFork($event, c.id);
            } },
        ...{ class: "action-btn" },
        title: "分叉会话",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.circle)({
        cx: "6",
        cy: "6",
        r: "3",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.circle)({
        cx: "18",
        cy: "18",
        r: "3",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M6 9v6a3 3 0 0 0 3 3h6",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.handleCompact($event, c.id);
            } },
        ...{ class: "action-btn" },
        title: "压缩上下文",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M4 8h16M4 16h10",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M18 13l3 3-3 3",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.handleStatus($event, c);
            } },
        ...{ class: "action-btn" },
        title: (__VLS_ctx.statusOf[c.id] ? `状态：${__VLS_ctx.statusOf[c.id]}` : '查询状态'),
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M22 12h-4l-3 9L9 3l-3 9H2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.startRename(c);
            } },
        ...{ class: "action-btn" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.handleDelete($event, c.id);
            } },
        ...{ class: "action-btn delete" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "12",
        height: "12",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
        points: "3 6 5 6 21 6",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",
    });
    if (__VLS_ctx.hoveredId === c.id) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "usage-pop" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "up-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
        (c.model?.id || __VLS_ctx.agent.selectedModel || '—');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "up-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
        (__VLS_ctx.fmtTokens(c.tokens_input) || '0');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "up-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
        (__VLS_ctx.fmtTokens(c.tokens_output) || '0');
        if (c.tokens_reasoning) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "up-row" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
            (__VLS_ctx.fmtTokens(c.tokens_reasoning));
        }
        if (c.tokens_cache_read || c.tokens_cache_write) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "up-row" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
            (__VLS_ctx.fmtTokens(c.tokens_cache_read) || 0);
            (__VLS_ctx.fmtTokens(c.tokens_cache_write) || 0);
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "up-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({
            ...{ class: "up-cost" },
        });
        (__VLS_ctx.fmtCost(c.cost) || '—');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "up-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
        (__VLS_ctx.fmtTime(c.updated_at));
        if (__VLS_ctx.agent.conversationId === c.id && __VLS_ctx.ctxLimitFor(c)) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "up-row up-ctx" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
            __VLS_asFunctionalElement(__VLS_intrinsicElements.b, __VLS_intrinsicElements.b)({});
            (__VLS_ctx.ctxEst ? __VLS_ctx.ctxEst.tokens.toLocaleString() : '···');
            (__VLS_ctx.ctxLimitFor(c).toLocaleString());
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "up-bar" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ style: ({ width: __VLS_ctx.ctxPctFor(c) + '%' }) },
            });
        }
    }
}
/** @type {__VLS_StyleScopedClasses['chat-history']} */ ;
/** @type {__VLS_StyleScopedClasses['history-header']} */ ;
/** @type {__VLS_StyleScopedClasses['new-chat-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['new-chat-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['history-list']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['history-item']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['item-content']} */ ;
/** @type {__VLS_StyleScopedClasses['rename-input']} */ ;
/** @type {__VLS_StyleScopedClasses['item-title-row']} */ ;
/** @type {__VLS_StyleScopedClasses['item-title']} */ ;
/** @type {__VLS_StyleScopedClasses['stream-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['queued']} */ ;
/** @type {__VLS_StyleScopedClasses['stream-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['running']} */ ;
/** @type {__VLS_StyleScopedClasses['item-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['meta-model']} */ ;
/** @type {__VLS_StyleScopedClasses['meta-cost']} */ ;
/** @type {__VLS_StyleScopedClasses['item-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['action-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['action-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['action-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['action-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['action-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['delete']} */ ;
/** @type {__VLS_StyleScopedClasses['usage-pop']} */ ;
/** @type {__VLS_StyleScopedClasses['up-row']} */ ;
/** @type {__VLS_StyleScopedClasses['up-row']} */ ;
/** @type {__VLS_StyleScopedClasses['up-row']} */ ;
/** @type {__VLS_StyleScopedClasses['up-row']} */ ;
/** @type {__VLS_StyleScopedClasses['up-row']} */ ;
/** @type {__VLS_StyleScopedClasses['up-row']} */ ;
/** @type {__VLS_StyleScopedClasses['up-cost']} */ ;
/** @type {__VLS_StyleScopedClasses['up-row']} */ ;
/** @type {__VLS_StyleScopedClasses['up-row']} */ ;
/** @type {__VLS_StyleScopedClasses['up-ctx']} */ ;
/** @type {__VLS_StyleScopedClasses['up-bar']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            fmtTokens: fmtTokens,
            fmtCost: fmtCost,
            fmtTime: fmtTime,
            agent: agent,
            searchQuery: searchQuery,
            editingId: editingId,
            editingTitle: editingTitle,
            hoveredId: hoveredId,
            ctxEst: ctxEst,
            ctxLimitFor: ctxLimitFor,
            ctxPctFor: ctxPctFor,
            showUsage: showUsage,
            hideUsage: hideUsage,
            sortedConversations: sortedConversations,
            handleNewChat: handleNewChat,
            selectConversation: selectConversation,
            startRename: startRename,
            saveRename: saveRename,
            cancelRename: cancelRename,
            handleDelete: handleDelete,
            handleFork: handleFork,
            handleCompact: handleCompact,
            statusOf: statusOf,
            handleStatus: handleStatus,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
