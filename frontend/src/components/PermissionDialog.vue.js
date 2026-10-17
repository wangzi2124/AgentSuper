/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { computed, ref, watch, onBeforeUnmount } from 'vue';
import { usePermissionStore } from '../stores/permission';
const perm = usePermissionStore();
const currentRequest = computed(() => perm.pendingRequests[0] ?? null);
const remainingMs = ref(0);
const totalMs = ref(60000);
let ticker = null;
function stopTicker() {
    if (ticker) {
        clearInterval(ticker);
        ticker = null;
    }
}
watch(currentRequest, (req) => {
    stopTicker();
    if (!req) {
        remainingMs.value = 0;
        return;
    }
    const deadline = perm.expiryFor(req);
    totalMs.value = Math.max(1000, deadline - (new Date(req.created_at).getTime() || Date.now()));
    remainingMs.value = Math.max(0, deadline - Date.now());
    ticker = setInterval(() => {
        remainingMs.value = Math.max(0, deadline - Date.now());
    }, 500);
});
const remainingSec = computed(() => Math.ceil(remainingMs.value / 1000));
const barPct = computed(() => totalMs.value ? Math.min(100, (remainingMs.value / totalMs.value) * 100) : 0);
function allow(remember = false) {
    if (!currentRequest.value)
        return;
    perm.respond(currentRequest.value.id, 'allowed', remember);
}
function deny() {
    if (!currentRequest.value)
        return;
    perm.respond(currentRequest.value.id, 'denied');
}
onBeforeUnmount(stopTicker);
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
if (__VLS_ctx.currentRequest) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-card" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "perm-avatar" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-head-text" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "perm-agent" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "perm-action-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-detail" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "perm-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "perm-op" },
    });
    (__VLS_ctx.currentRequest.operation === 'command' ? '执行命令' :
        __VLS_ctx.currentRequest.operation === 'write' ? '写入文件' :
            __VLS_ctx.currentRequest.operation === 'read' ? '读取文件' :
                __VLS_ctx.currentRequest.operation);
    if (__VLS_ctx.currentRequest.operation === 'command') {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "perm-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "perm-label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({
            ...{ class: "perm-cmd" },
        });
        (__VLS_ctx.currentRequest.tool_args?.command || __VLS_ctx.currentRequest.path);
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "perm-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "perm-label" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({
            ...{ class: "perm-path" },
        });
        (__VLS_ctx.currentRequest.path);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "perm-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({
        ...{ class: "perm-tool" },
    });
    (__VLS_ctx.currentRequest.tool_name);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-countdown-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-countdown-track" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-countdown-bar" },
        ...{ style: ({ width: __VLS_ctx.barPct + '%' }) },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "perm-countdown-hint" },
    });
    (__VLS_ctx.remainingSec);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "perm-actions" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.deny) },
        ...{ class: "btn-perm btn-perm-deny" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.currentRequest))
                    return;
                __VLS_ctx.allow(false);
            } },
        ...{ class: "btn-perm btn-perm-allow-once" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.currentRequest))
                    return;
                __VLS_ctx.allow(true);
            } },
        ...{ class: "btn-perm btn-perm-allow-always" },
    });
}
/** @type {__VLS_StyleScopedClasses['perm-card']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-head']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-avatar']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-head-text']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-agent']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-action-label']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-detail']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-row']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-label']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-op']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-row']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-label']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-cmd']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-row']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-label']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-path']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-row']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-label']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-tool']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-countdown-row']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-countdown-track']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-countdown-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-countdown-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['perm-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-perm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-perm-deny']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-perm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-perm-allow-once']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-perm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-perm-allow-always']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            currentRequest: currentRequest,
            remainingSec: remainingSec,
            barPct: barPct,
            allow: allow,
            deny: deny,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
