/// <reference types="../../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { onMounted } from 'vue';
import { useDocumentStore } from '../../stores/documents';
import { showConfirmDialog, showToast } from 'vant';
const docs = useDocumentStore();
onMounted(() => {
    docs.fetchAll();
});
function formatSize(bytes) {
    if (bytes < 1024)
        return `${bytes} B`;
    if (bytes < 1024 * 1024)
        return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
function formatDate(iso) {
    const d = new Date(iso);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
// 文件类型 → 彩色方块（颜色/缩写）
function typeMeta(filename) {
    const ext = (filename.split('.').pop() || '').toUpperCase();
    const map = {
        PDF: { label: 'PDF', color: 'var(--m-pdf)', soft: 'var(--m-pdf-soft)' },
        MD: { label: 'MD', color: 'var(--m-md)', soft: 'var(--m-md-soft)' },
        MARKDOWN: { label: 'MD', color: 'var(--m-md)', soft: 'var(--m-md-soft)' },
        TXT: { label: 'TXT', color: 'var(--m-txt)', soft: 'var(--m-txt-soft)' },
        DOC: { label: 'DOC', color: 'var(--m-doc)', soft: 'var(--m-doc-soft)' },
        DOCX: { label: 'DOCX', color: 'var(--m-doc)', soft: 'var(--m-doc-soft)' },
    };
    return map[ext] || { label: ext.slice(0, 4), color: 'var(--m-file)', soft: 'var(--m-file-soft)' };
}
async function handleDelete(id) {
    try {
        await showConfirmDialog({
            title: '删除文档',
            message: '确定要删除该文档吗？相关向量分块也会被清除。',
        });
        await docs.remove(id);
        showToast('已删除');
    }
    catch (err) {
        if (err?.message && !String(err.message).includes('cancel')) {
            showToast(err.message);
        }
    }
}
async function handleUpload(file) {
    try {
        await docs.upload(file);
        showToast('上传成功');
    }
    catch (err) {
        showToast(err.message || '上传失败');
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['upload-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['doc-del']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-docs" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "upload-wrap" },
});
const __VLS_0 = {}.VanUploader;
/** @type {[typeof __VLS_components.VanUploader, typeof __VLS_components.vanUploader, typeof __VLS_components.VanUploader, typeof __VLS_components.vanUploader, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    afterRead: ((item) => __VLS_ctx.handleUpload(item.file)),
    maxCount: (1),
    accept: "*",
}));
const __VLS_2 = __VLS_1({
    afterRead: ((item) => __VLS_ctx.handleUpload(item.file)),
    maxCount: (1),
    accept: "*",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_3.slots.default;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "upload-btn" },
});
const __VLS_4 = {}.VanIcon;
/** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
// @ts-ignore
const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
    name: "plus",
}));
const __VLS_6 = __VLS_5({
    name: "plus",
}, ...__VLS_functionalComponentArgsRest(__VLS_5));
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "upload-sub" },
});
var __VLS_3;
if (__VLS_ctx.docs.loading && !__VLS_ctx.docs.uploadStage) {
    const __VLS_8 = {}.VanLoading;
    /** @type {[typeof __VLS_components.VanLoading, typeof __VLS_components.vanLoading, ]} */ ;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent(__VLS_8, new __VLS_8({
        ...{ class: "loading" },
    }));
    const __VLS_10 = __VLS_9({
        ...{ class: "loading" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
}
else if (__VLS_ctx.docs.documents.length === 0) {
    const __VLS_12 = {}.VanEmpty;
    /** @type {[typeof __VLS_components.VanEmpty, typeof __VLS_components.vanEmpty, typeof __VLS_components.VanEmpty, typeof __VLS_components.vanEmpty, ]} */ ;
    // @ts-ignore
    const __VLS_13 = __VLS_asFunctionalComponent(__VLS_12, new __VLS_12({
        image: "search",
        description: "还没有上传文档",
    }));
    const __VLS_14 = __VLS_13({
        image: "search",
        description: "还没有上传文档",
    }, ...__VLS_functionalComponentArgsRest(__VLS_13));
    __VLS_15.slots.default;
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-hint" },
    });
    var __VLS_15;
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "doc-list" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-count-pill" },
    });
    const __VLS_16 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_17 = __VLS_asFunctionalComponent(__VLS_16, new __VLS_16({
        name: "description",
    }));
    const __VLS_18 = __VLS_17({
        name: "description",
    }, ...__VLS_functionalComponentArgsRest(__VLS_17));
    (__VLS_ctx.docs.documents.length);
    for (const [doc] of __VLS_getVForSourceType((__VLS_ctx.docs.documents))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (doc.id),
            ...{ class: "m-card doc-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "m-type-block" },
            ...{ style: ({ background: __VLS_ctx.typeMeta(doc.filename).soft, color: __VLS_ctx.typeMeta(doc.filename).color }) },
        });
        (__VLS_ctx.typeMeta(doc.filename).label);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "doc-main" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "doc-name" },
        });
        (doc.filename);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "doc-meta" },
        });
        (__VLS_ctx.formatSize(doc.size));
        (doc.chunk_count);
        (__VLS_ctx.formatDate(doc.created_at));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.docs.loading && !__VLS_ctx.docs.uploadStage))
                        return;
                    if (!!(__VLS_ctx.docs.documents.length === 0))
                        return;
                    __VLS_ctx.handleDelete(doc.id);
                } },
            ...{ class: "doc-del" },
            'aria-label': "删除",
        });
        const __VLS_20 = {}.VanIcon;
        /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
        // @ts-ignore
        const __VLS_21 = __VLS_asFunctionalComponent(__VLS_20, new __VLS_20({
            name: "delete-o",
        }));
        const __VLS_22 = __VLS_21({
            name: "delete-o",
        }, ...__VLS_functionalComponentArgsRest(__VLS_21));
    }
}
/** @type {__VLS_StyleScopedClasses['m-docs']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-sub']} */ ;
/** @type {__VLS_StyleScopedClasses['loading']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['doc-list']} */ ;
/** @type {__VLS_StyleScopedClasses['m-count-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['m-card']} */ ;
/** @type {__VLS_StyleScopedClasses['doc-item']} */ ;
/** @type {__VLS_StyleScopedClasses['m-type-block']} */ ;
/** @type {__VLS_StyleScopedClasses['doc-main']} */ ;
/** @type {__VLS_StyleScopedClasses['doc-name']} */ ;
/** @type {__VLS_StyleScopedClasses['doc-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['doc-del']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            docs: docs,
            formatSize: formatSize,
            formatDate: formatDate,
            typeMeta: typeMeta,
            handleDelete: handleDelete,
            handleUpload: handleUpload,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
