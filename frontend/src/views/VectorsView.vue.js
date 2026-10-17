/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { onMounted, ref, computed } from 'vue';
import { useVectorStore } from '../stores/vectors';
import { useDocumentStore } from '../stores/documents';
// 向量存储状态管理
const vs = useVectorStore();
// 文档状态管理
const ds = useDocumentStore();
// 选中的文档ID（用于筛选）
const selectedDocId = ref('');
// 搜索关键词
const searchText = ref('');
// 当前页码
const currentPage = ref(1);
// 计算属性：总页数
const totalPages = computed(() => Math.ceil(vs.total / vs.limit));
// 组件挂载时加载文档和向量数据
onMounted(() => {
    ds.fetchAll();
    vs.fetch();
    vs.loadConfig();
});
// 筛选条件变化时重置分页并刷新
function onFilterChange() {
    currentPage.value = 1;
    vs.reset(selectedDocId.value, searchText.value);
}
// 执行搜索
function onSearch() {
    currentPage.value = 1;
    vs.offset = 0;
    vs.reset(selectedDocId.value, searchText.value);
}
// 跳转到指定页码
function goToPage(page) {
    if (page < 1 || page > totalPages.value)
        return;
    currentPage.value = page;
    vs.offset = (page - 1) * vs.limit;
    vs.fetch();
}
// 清空全部知识库数据（向量库 + 章节库 + BM25 + 上传文件）
async function handleClearAll() {
    const confirmMsg = '确定要清空全部知识库数据吗？\n\n将删除：\n- 向量库全部分块\n- 章节库全部记录\n- BM25 索引\n- 全部上传文件\n\n此操作不可撤销！';
    if (!confirm(confirmMsg))
        return;
    try {
        await vs.clearAll();
        ds.fetchAll();
        alert('知识库数据已全部清空');
    }
    catch (e) {
        alert(`清空失败：${e.message || e}`);
    }
}
// 手动触发 TTL 过期清理
async function handleClearExpired() {
    if (!confirm('按 TTL 配置清理所有过期文档？'))
        return;
    try {
        const res = await vs.clearExpired();
        ds.fetchAll();
        alert(res.message);
    }
    catch (e) {
        alert(`清理失败：${e.message || e}`);
    }
}
// 自愈重建：补齐 index_state != ready 文档的构建索引
async function handleRepair() {
    const pending = vs.config?.pending_repair ?? 0;
    if (!confirm(`检测到 ${pending} 个文档的索引未就绪，将重新构建其向量/BM25/章节数据？\n（幂等操作，可重复执行）`))
        return;
    try {
        const res = await vs.repair();
        ds.fetchAll();
        const failed = res.failed?.length ?? 0;
        alert(failed > 0
            ? `${res.message}\n${failed} 个索引重建失败：${res.failed.map((f) => f.error).join('；')}`
            : res.message);
    }
    catch (e) {
        alert(`修复失败：${e.message || e}`);
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['btn-repair']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-select']} */ ;
/** @type {__VLS_StyleScopedClasses['search-input']} */ ;
/** @type {__VLS_StyleScopedClasses['chunk-table']} */ ;
/** @type {__VLS_StyleScopedClasses['chunk-table']} */ ;
/** @type {__VLS_StyleScopedClasses['chunk-table']} */ ;
/** @type {__VLS_StyleScopedClasses['chunk-table']} */ ;
/** @type {__VLS_StyleScopedClasses['chunk-table']} */ ;
/** @type {__VLS_StyleScopedClasses['row-source']} */ ;
/** @type {__VLS_StyleScopedClasses['row-source']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-content" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "toolbar" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "toolbar-search" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    ...{ onChange: (__VLS_ctx.onFilterChange) },
    value: (__VLS_ctx.selectedDocId),
    ...{ class: "filter-select" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "",
});
for (const [doc] of __VLS_getVForSourceType((__VLS_ctx.ds.documents))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        key: (doc.id),
        value: (doc.id),
    });
    (doc.filename);
    (doc.chunk_count);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "search-box" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ onKeyup: (__VLS_ctx.onSearch) },
    value: (__VLS_ctx.searchText),
    type: "text",
    placeholder: "搜索分块内容...",
    ...{ class: "search-input" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.onSearch) },
    ...{ class: "btn btn-primary btn-sm" },
});
if (__VLS_ctx.vs.searchQuery) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.vs.searchQuery))
                    return;
                __VLS_ctx.searchText = '';
                __VLS_ctx.onSearch();
            } },
        ...{ class: "btn btn-sm" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "toolbar-actions" },
});
if (__VLS_ctx.vs.config) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "ttl-info" },
    });
    if (__VLS_ctx.vs.config.ttl_days > 0) {
        (__VLS_ctx.vs.config.cleanup_interval_hours);
    }
    if (__VLS_ctx.vs.config.ttl_days > 0) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.handleClearExpired) },
            ...{ class: "btn btn-sm" },
        });
    }
    if ((__VLS_ctx.vs.config.pending_repair ?? 0) > 0) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.handleRepair) },
            ...{ class: "btn btn-sm btn-repair" },
        });
        (__VLS_ctx.vs.config.pending_repair);
    }
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.handleClearAll) },
    ...{ class: "btn btn-sm btn-danger" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "result-meta" },
});
(__VLS_ctx.vs.total);
if (__VLS_ctx.vs.searchQuery) {
    (__VLS_ctx.vs.searchQuery);
}
if (__VLS_ctx.vs.chunks.length > 0) {
    (__VLS_ctx.currentPage);
    (__VLS_ctx.totalPages);
}
if (__VLS_ctx.vs.loading && __VLS_ctx.vs.chunks.length === 0) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "loading-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "spinner" },
    });
}
else if (__VLS_ctx.vs.chunks.length === 0) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-state" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "icon" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ style: {} },
    });
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "table-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.table, __VLS_intrinsicElements.table)({
        ...{ class: "chunk-table" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.thead, __VLS_intrinsicElements.thead)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.tr, __VLS_intrinsicElements.tr)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.th, __VLS_intrinsicElements.th)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.tbody, __VLS_intrinsicElements.tbody)({});
    for (const [chunk, i] of __VLS_getVForSourceType((__VLS_ctx.vs.chunks))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.tr, __VLS_intrinsicElements.tr)({
            key: (chunk.id),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({
            ...{ class: "row-idx" },
        });
        (__VLS_ctx.vs.offset + i + 1);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({
            ...{ class: "row-source" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
        (chunk.id.slice(0, 12));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (chunk.metadata.filename || chunk.metadata.source || '-');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
        if (chunk.metadata.chapter_title) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "badge badge-enabled" },
            });
            (chunk.metadata.chapter_title);
        }
        else {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "badge badge-disabled" },
            });
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.td, __VLS_intrinsicElements.td)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
            ...{ class: "row-text" },
        });
        (chunk.text.slice(0, 300));
        (chunk.text.length > 300 ? '...' : '');
    }
    if (__VLS_ctx.totalPages > 1) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "pagination" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.vs.loading && __VLS_ctx.vs.chunks.length === 0))
                        return;
                    if (!!(__VLS_ctx.vs.chunks.length === 0))
                        return;
                    if (!(__VLS_ctx.totalPages > 1))
                        return;
                    __VLS_ctx.goToPage(__VLS_ctx.currentPage - 1);
                } },
            ...{ class: "btn btn-sm" },
            disabled: (__VLS_ctx.currentPage <= 1),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "page-info" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
            ...{ onChange: (...[$event]) => {
                    if (!!(__VLS_ctx.vs.loading && __VLS_ctx.vs.chunks.length === 0))
                        return;
                    if (!!(__VLS_ctx.vs.chunks.length === 0))
                        return;
                    if (!(__VLS_ctx.totalPages > 1))
                        return;
                    __VLS_ctx.goToPage(Number($event.target.value));
                } },
            value: (__VLS_ctx.currentPage),
            ...{ class: "page-select" },
        });
        for (const [p] of __VLS_getVForSourceType((__VLS_ctx.totalPages))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
                key: (p),
                value: (p),
            });
            (p);
        }
        (__VLS_ctx.totalPages);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.vs.loading && __VLS_ctx.vs.chunks.length === 0))
                        return;
                    if (!!(__VLS_ctx.vs.chunks.length === 0))
                        return;
                    if (!(__VLS_ctx.totalPages > 1))
                        return;
                    __VLS_ctx.goToPage(__VLS_ctx.currentPage + 1);
                } },
            ...{ class: "btn btn-sm" },
            disabled: (__VLS_ctx.currentPage >= __VLS_ctx.totalPages),
        });
    }
}
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
/** @type {__VLS_StyleScopedClasses['page-content']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar-search']} */ ;
/** @type {__VLS_StyleScopedClasses['filter-select']} */ ;
/** @type {__VLS_StyleScopedClasses['search-box']} */ ;
/** @type {__VLS_StyleScopedClasses['search-input']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['toolbar-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['ttl-info']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-repair']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
/** @type {__VLS_StyleScopedClasses['result-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['icon']} */ ;
/** @type {__VLS_StyleScopedClasses['table-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['chunk-table']} */ ;
/** @type {__VLS_StyleScopedClasses['row-idx']} */ ;
/** @type {__VLS_StyleScopedClasses['row-source']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge-enabled']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['badge-disabled']} */ ;
/** @type {__VLS_StyleScopedClasses['row-text']} */ ;
/** @type {__VLS_StyleScopedClasses['pagination']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['page-info']} */ ;
/** @type {__VLS_StyleScopedClasses['page-select']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            vs: vs,
            ds: ds,
            selectedDocId: selectedDocId,
            searchText: searchText,
            currentPage: currentPage,
            totalPages: totalPages,
            onFilterChange: onFilterChange,
            onSearch: onSearch,
            goToPage: goToPage,
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
