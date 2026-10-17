/// <reference types="../../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { onMounted, ref, computed } from 'vue';
import { useVectorStore } from '../../stores/vectors';
import { useDocumentStore } from '../../stores/documents';
import { showConfirmDialog, showToast, showDialog } from 'vant';
const vs = useVectorStore();
const ds = useDocumentStore();
const searchText = ref('');
const filterSheet = ref(false);
const selectedDocId = ref('');
const totalPages = computed(() => Math.ceil(vs.total / vs.limit));
const hasMore = computed(() => vs.chunks.length < vs.total);
const docOptions = computed(() => [
    { name: '全部文档', value: '' },
    ...ds.documents.map(d => ({ name: d.filename, value: d.id })),
]);
function selectedDocLabel() {
    const hit = docOptions.value.find(o => o.value === selectedDocId.value);
    return hit ? hit.name : '全部文档';
}
function chunkSource(chunk) {
    const f = chunk.metadata.filename;
    const s = chunk.metadata.source;
    return String(f || s || '-');
}
onMounted(() => {
    ds.fetchAll();
    vs.fetch();
    vs.loadConfig();
});
function onSearch() {
    vs.reset(selectedDocId.value, searchText.value.trim());
}
function onPickDoc(option) {
    const val = (option && option.value);
    selectedDocId.value = val || '';
    vs.reset(val || '', searchText.value.trim());
}
function loadMore() {
    vs.offset += vs.limit;
    vs.fetch(true);
}
async function handleClearAll() {
    try {
        await showConfirmDialog({
            title: '清空向量库',
            message: '将删除向量库全部分块、章节库记录、BM25 索引和全部上传文件，此操作不可撤销！',
        });
        await vs.clearAll();
        ds.fetchAll();
        showToast('已清空');
    }
    catch (err) {
        if (err?.message && !String(err.message).includes('cancel'))
            showToast(err.message || '清空失败');
    }
}
async function handleClearExpired() {
    try {
        await showConfirmDialog({ title: '清理过期', message: '按 TTL 配置清理所有过期文档？' });
        const res = await vs.clearExpired();
        ds.fetchAll();
        showDialog({ title: '清理过期', message: res.message });
    }
    catch (err) {
        if (err?.message && !String(err.message).includes('cancel'))
            showToast(err.message || '清理失败');
    }
}
async function handleRepair() {
    const pending = vs.config?.pending_repair ?? 0;
    try {
        await showConfirmDialog({
            title: '自愈重建',
            message: `${pending} 个文档索引未就绪，重新构建其向量/BM25/章节数据？`,
        });
        const res = await vs.repair();
        ds.fetchAll();
        showDialog({
            title: '自愈重建',
            message: res.message + (res.failed?.length ? `\n${res.failed.length} 个失败` : ''),
        });
    }
    catch (err) {
        if (err?.message && !String(err.message).includes('cancel'))
            showToast(err.message || '修复失败');
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['m-hero-clear']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-line']} */ ;
/** @type {__VLS_StyleScopedClasses['van-search']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-filter']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-filter']} */ ;
/** @type {__VLS_StyleScopedClasses['m-vectors']} */ ;
/** @type {__VLS_StyleScopedClasses['m-manage-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['van-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['m-manage-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['m-manage-btn']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-vectors" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-query-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-query-line" },
});
const __VLS_0 = {}.VanSearch;
/** @type {[typeof __VLS_components.VanSearch, typeof __VLS_components.vanSearch, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    ...{ 'onSearch': {} },
    ...{ 'onClear': {} },
    modelValue: (__VLS_ctx.searchText),
    placeholder: "搜索分块内容...",
    shape: "round",
}));
const __VLS_2 = __VLS_1({
    ...{ 'onSearch': {} },
    ...{ 'onClear': {} },
    modelValue: (__VLS_ctx.searchText),
    placeholder: "搜索分块内容...",
    shape: "round",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
let __VLS_4;
let __VLS_5;
let __VLS_6;
const __VLS_7 = {
    onSearch: (__VLS_ctx.onSearch)
};
const __VLS_8 = {
    onClear: (__VLS_ctx.onSearch)
};
var __VLS_3;
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.filterSheet = true;
        } },
    ...{ class: "m-query-filter" },
});
const __VLS_9 = {}.VanIcon;
/** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
// @ts-ignore
const __VLS_10 = __VLS_asFunctionalComponent(__VLS_9, new __VLS_9({
    name: "filter-o",
}));
const __VLS_11 = __VLS_10({
    name: "filter-o",
}, ...__VLS_functionalComponentArgsRest(__VLS_10));
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "m-query-filter-label" },
});
(__VLS_ctx.selectedDocLabel());
const __VLS_13 = {}.VanIcon;
/** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
// @ts-ignore
const __VLS_14 = __VLS_asFunctionalComponent(__VLS_13, new __VLS_13({
    name: "arrow-down",
}));
const __VLS_15 = __VLS_14({
    name: "arrow-down",
}, ...__VLS_functionalComponentArgsRest(__VLS_14));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-hero" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-hero-icon" },
});
const __VLS_17 = {}.VanIcon;
/** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
// @ts-ignore
const __VLS_18 = __VLS_asFunctionalComponent(__VLS_17, new __VLS_17({
    name: "apps-o",
}));
const __VLS_19 = __VLS_18({
    name: "apps-o",
}, ...__VLS_functionalComponentArgsRest(__VLS_18));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-hero-meta" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-hero-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-hero-value" },
});
(__VLS_ctx.vs.total);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-hero-extra" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-hero-mini" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "m-hero-num" },
});
(__VLS_ctx.ds.documents.length);
if (__VLS_ctx.vs.searchQuery) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-hero-mini" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "m-hero-num" },
    });
    (__VLS_ctx.vs.chunks.length);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.handleClearAll) },
    ...{ class: "m-hero-clear" },
    'aria-label': "清空向量库",
});
const __VLS_21 = {}.VanIcon;
/** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
// @ts-ignore
const __VLS_22 = __VLS_asFunctionalComponent(__VLS_21, new __VLS_21({
    name: "delete-o",
}));
const __VLS_23 = __VLS_22({
    name: "delete-o",
}, ...__VLS_functionalComponentArgsRest(__VLS_22));
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
if (__VLS_ctx.vs.loading && __VLS_ctx.vs.chunks.length === 0) {
    const __VLS_25 = {}.VanLoading;
    /** @type {[typeof __VLS_components.VanLoading, typeof __VLS_components.vanLoading, ]} */ ;
    // @ts-ignore
    const __VLS_26 = __VLS_asFunctionalComponent(__VLS_25, new __VLS_25({
        ...{ class: "loading" },
    }));
    const __VLS_27 = __VLS_26({
        ...{ class: "loading" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_26));
}
else if (__VLS_ctx.vs.chunks.length === 0) {
    const __VLS_29 = {}.VanEmpty;
    /** @type {[typeof __VLS_components.VanEmpty, typeof __VLS_components.vanEmpty, ]} */ ;
    // @ts-ignore
    const __VLS_30 = __VLS_asFunctionalComponent(__VLS_29, new __VLS_29({
        image: "search",
        description: "未找到分块",
    }));
    const __VLS_31 = __VLS_30({
        image: "search",
        description: "未找到分块",
    }, ...__VLS_functionalComponentArgsRest(__VLS_30));
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "chunk-list" },
    });
    for (const [chunk, i] of __VLS_getVForSourceType((__VLS_ctx.vs.chunks))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (chunk.id),
            ...{ class: "m-chunk-card" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "m-chunk-head" },
        });
        if (chunk.metadata.chapter_title) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "m-chunk-chapter" },
            });
            const __VLS_33 = {}.VanIcon;
            /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
            // @ts-ignore
            const __VLS_34 = __VLS_asFunctionalComponent(__VLS_33, new __VLS_33({
                name: "orders-o",
            }));
            const __VLS_35 = __VLS_34({
                name: "orders-o",
            }, ...__VLS_functionalComponentArgsRest(__VLS_34));
            (chunk.metadata.chapter_title);
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "m-chunk-idx" },
        });
        (__VLS_ctx.vs.offset + i + 1);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "m-chunk-src" },
        });
        (__VLS_ctx.chunkSource(chunk));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "m-chunk-text" },
        });
        (chunk.text.slice(0, 200));
        (chunk.text.length > 200 ? '...' : '');
    }
}
if (__VLS_ctx.hasMore) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "load-more" },
    });
    const __VLS_37 = {}.VanButton;
    /** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
    // @ts-ignore
    const __VLS_38 = __VLS_asFunctionalComponent(__VLS_37, new __VLS_37({
        ...{ 'onClick': {} },
        size: "small",
        round: true,
        plain: true,
        type: "primary",
    }));
    const __VLS_39 = __VLS_38({
        ...{ 'onClick': {} },
        size: "small",
        round: true,
        plain: true,
        type: "primary",
    }, ...__VLS_functionalComponentArgsRest(__VLS_38));
    let __VLS_41;
    let __VLS_42;
    let __VLS_43;
    const __VLS_44 = {
        onClick: (__VLS_ctx.loadMore)
    };
    __VLS_40.slots.default;
    var __VLS_40;
}
if (__VLS_ctx.vs.config && ((__VLS_ctx.vs.config.ttl_days ?? 0) > 0 || (__VLS_ctx.vs.config.pending_repair ?? 0) > 0)) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-manage-bar" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-manage-left" },
    });
    if (__VLS_ctx.vs.config.ttl_days > 0) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.handleClearExpired) },
            ...{ class: "m-manage-btn" },
        });
        const __VLS_45 = {}.VanIcon;
        /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
        // @ts-ignore
        const __VLS_46 = __VLS_asFunctionalComponent(__VLS_45, new __VLS_45({
            name: "clock-o",
        }));
        const __VLS_47 = __VLS_46({
            name: "clock-o",
        }, ...__VLS_functionalComponentArgsRest(__VLS_46));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    }
    if ((__VLS_ctx.vs.config.pending_repair ?? 0) > 0) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.handleRepair) },
            ...{ class: "m-manage-btn repair" },
        });
        const __VLS_49 = {}.VanIcon;
        /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
        // @ts-ignore
        const __VLS_50 = __VLS_asFunctionalComponent(__VLS_49, new __VLS_49({
            name: "rebuild",
        }));
        const __VLS_51 = __VLS_50({
            name: "rebuild",
        }, ...__VLS_functionalComponentArgsRest(__VLS_50));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (__VLS_ctx.vs.config.pending_repair);
    }
}
const __VLS_53 = {}.VanActionSheet;
/** @type {[typeof __VLS_components.VanActionSheet, typeof __VLS_components.vanActionSheet, ]} */ ;
// @ts-ignore
const __VLS_54 = __VLS_asFunctionalComponent(__VLS_53, new __VLS_53({
    ...{ 'onSelect': {} },
    show: (__VLS_ctx.filterSheet),
    actions: (__VLS_ctx.docOptions),
    title: "按文档筛选",
    cancelText: "取消",
}));
const __VLS_55 = __VLS_54({
    ...{ 'onSelect': {} },
    show: (__VLS_ctx.filterSheet),
    actions: (__VLS_ctx.docOptions),
    title: "按文档筛选",
    cancelText: "取消",
}, ...__VLS_functionalComponentArgsRest(__VLS_54));
let __VLS_57;
let __VLS_58;
let __VLS_59;
const __VLS_60 = {
    onSelect: (__VLS_ctx.onPickDoc)
};
var __VLS_56;
/** @type {__VLS_StyleScopedClasses['m-vectors']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-line']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-filter']} */ ;
/** @type {__VLS_StyleScopedClasses['m-query-filter-label']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-label']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-value']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-extra']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-num']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-mini']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-num']} */ ;
/** @type {__VLS_StyleScopedClasses['m-hero-clear']} */ ;
/** @type {__VLS_StyleScopedClasses['loading']} */ ;
/** @type {__VLS_StyleScopedClasses['chunk-list']} */ ;
/** @type {__VLS_StyleScopedClasses['m-chunk-card']} */ ;
/** @type {__VLS_StyleScopedClasses['m-chunk-head']} */ ;
/** @type {__VLS_StyleScopedClasses['m-chunk-chapter']} */ ;
/** @type {__VLS_StyleScopedClasses['m-chunk-idx']} */ ;
/** @type {__VLS_StyleScopedClasses['m-chunk-src']} */ ;
/** @type {__VLS_StyleScopedClasses['m-chunk-text']} */ ;
/** @type {__VLS_StyleScopedClasses['load-more']} */ ;
/** @type {__VLS_StyleScopedClasses['m-manage-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['m-manage-left']} */ ;
/** @type {__VLS_StyleScopedClasses['m-manage-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['m-manage-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['repair']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            vs: vs,
            ds: ds,
            searchText: searchText,
            filterSheet: filterSheet,
            hasMore: hasMore,
            docOptions: docOptions,
            selectedDocLabel: selectedDocLabel,
            chunkSource: chunkSource,
            onSearch: onSearch,
            onPickDoc: onPickDoc,
            loadMore: loadMore,
            handleClearAll: handleClearAll,
            handleClearExpired: handleClearExpired,
            handleRepair: handleRepair,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
