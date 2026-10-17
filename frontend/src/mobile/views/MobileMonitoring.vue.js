/// <reference types="../../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { onMounted, ref } from 'vue';
import { fetchStats } from '../../api/monitor';
const stats = ref(null);
const loading = ref(true);
const error = ref('');
onMounted(async () => {
    try {
        stats.value = await fetchStats();
    }
    catch (err) {
        error.value = err.message;
    }
    finally {
        loading.value = false;
    }
});
function ms(v) {
    if (v >= 60000)
        return (v / 60000).toFixed(1) + ' min';
    if (v >= 1000)
        return (v / 1000).toFixed(1) + ' s';
    return Math.round(v) + ' ms';
}
function num(v) {
    return v.toLocaleString();
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['m-row']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-monitor" },
});
if (__VLS_ctx.loading) {
    const __VLS_0 = {}.VanLoading;
    /** @type {[typeof __VLS_components.VanLoading, typeof __VLS_components.vanLoading, ]} */ ;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
        ...{ class: "loading" },
    }));
    const __VLS_2 = __VLS_1({
        ...{ class: "loading" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
}
else if (__VLS_ctx.error) {
    const __VLS_4 = {}.VanEmpty;
    /** @type {[typeof __VLS_components.VanEmpty, typeof __VLS_components.vanEmpty, ]} */ ;
    // @ts-ignore
    const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
        image: "network",
        description: (__VLS_ctx.error),
    }));
    const __VLS_6 = __VLS_5({
        image: "network",
        description: (__VLS_ctx.error),
    }, ...__VLS_functionalComponentArgsRest(__VLS_5));
}
else if (__VLS_ctx.stats) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-grid" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-card" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-icon ic-blue" },
    });
    const __VLS_8 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent(__VLS_8, new __VLS_8({
        name: "chart-trending-o",
    }));
    const __VLS_10 = __VLS_9({
        name: "chart-trending-o",
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-meta" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-value" },
    });
    (__VLS_ctx.num(__VLS_ctx.stats.requests.total));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-card" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-icon ic-violet" },
    });
    const __VLS_12 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_13 = __VLS_asFunctionalComponent(__VLS_12, new __VLS_12({
        name: "records-o",
    }));
    const __VLS_14 = __VLS_13({
        name: "records-o",
    }, ...__VLS_functionalComponentArgsRest(__VLS_13));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-meta" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-value" },
    });
    (__VLS_ctx.num(__VLS_ctx.stats.model_calls.total));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-card" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-icon ic-cyan" },
    });
    const __VLS_16 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_17 = __VLS_asFunctionalComponent(__VLS_16, new __VLS_16({
        name: "descending",
    }));
    const __VLS_18 = __VLS_17({
        name: "descending",
    }, ...__VLS_functionalComponentArgsRest(__VLS_17));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-meta" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-value" },
    });
    (__VLS_ctx.num(__VLS_ctx.stats.model_calls.total_prompt_tokens));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-card" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-icon ic-orange" },
    });
    const __VLS_20 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_21 = __VLS_asFunctionalComponent(__VLS_20, new __VLS_20({
        name: "ascending",
    }));
    const __VLS_22 = __VLS_21({
        name: "ascending",
    }, ...__VLS_functionalComponentArgsRest(__VLS_21));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-meta" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-stat-value" },
    });
    (__VLS_ctx.num(__VLS_ctx.stats.model_calls.total_completion_tokens));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-sec-title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-card m-card-pad" },
    });
    for (const [count, path] of __VLS_getVForSourceType((__VLS_ctx.stats.requests.by_path))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (path),
            ...{ class: "m-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "m-row-name mono" },
        });
        (path);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "m-pill" },
        });
        (__VLS_ctx.num(count));
    }
    if (Object.keys(__VLS_ctx.stats.requests.by_status).length) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "m-tags" },
        });
        for (const [count, status] of __VLS_getVForSourceType((__VLS_ctx.stats.requests.by_status))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                key: ('s' + status),
                ...{ class: "m-tag" },
            });
            (status);
            (__VLS_ctx.num(count));
        }
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-sec-title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-card m-card-pad" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "m-row-name" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "m-row-val" },
    });
    (__VLS_ctx.ms(__VLS_ctx.stats.model_calls.total_duration_ms));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "m-row-name" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "m-row-val" },
    });
    (__VLS_ctx.ms(__VLS_ctx.stats.model_calls.avg_duration_ms));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "m-row-name" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "m-row-val" },
    });
    (__VLS_ctx.num(__VLS_ctx.stats.model_calls.tool_rounds_total));
    (__VLS_ctx.stats.model_calls.avg_tool_rounds);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-sec-title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-card m-card-pad" },
    });
    for (const [count, model] of __VLS_getVForSourceType((__VLS_ctx.stats.model_calls.by_model))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (model),
            ...{ class: "m-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "m-row-name mono" },
        });
        (model);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "m-pill" },
        });
        (__VLS_ctx.num(count));
    }
}
/** @type {__VLS_StyleScopedClasses['m-monitor']} */ ;
/** @type {__VLS_StyleScopedClasses['loading']} */ ;
/** @type {__VLS_StyleScopedClasses['m-grid']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ic-blue']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-value']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ic-violet']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-value']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ic-cyan']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-value']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['ic-orange']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-label']} */ ;
/** @type {__VLS_StyleScopedClasses['m-stat-value']} */ ;
/** @type {__VLS_StyleScopedClasses['m-sec-title']} */ ;
/** @type {__VLS_StyleScopedClasses['m-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-card-pad']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row-name']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['m-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tags']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tag']} */ ;
/** @type {__VLS_StyleScopedClasses['m-sec-title']} */ ;
/** @type {__VLS_StyleScopedClasses['m-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-card-pad']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row-name']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row-val']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row-name']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row-val']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row-name']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row-val']} */ ;
/** @type {__VLS_StyleScopedClasses['m-sec-title']} */ ;
/** @type {__VLS_StyleScopedClasses['m-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-card-pad']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row']} */ ;
/** @type {__VLS_StyleScopedClasses['m-row-name']} */ ;
/** @type {__VLS_StyleScopedClasses['mono']} */ ;
/** @type {__VLS_StyleScopedClasses['m-pill']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            stats: stats,
            loading: loading,
            error: error,
            ms: ms,
            num: num,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
