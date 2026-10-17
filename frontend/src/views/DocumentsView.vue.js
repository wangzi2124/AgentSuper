/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { onMounted } from 'vue';
import { useDocumentStore } from '../stores/documents';
import DocumentCard from '../components/DocumentCard.vue';
import FileUpload from '../components/FileUpload.vue';
const docs = useDocumentStore();
onMounted(() => {
    docs.fetchAll();
});
async function handleUpload(file) {
    try {
        await docs.upload(file);
    }
    catch (err) {
        alert(err.message);
    }
}
async function handleDelete(id) {
    if (confirm('Delete this document?')) {
        try {
            await docs.remove(id);
        }
        catch (err) {
            alert(err.message);
        }
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
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
/** @type {[typeof FileUpload, ]} */ ;
// @ts-ignore
const __VLS_0 = __VLS_asFunctionalComponent(FileUpload, new FileUpload({
    ...{ 'onUpload': {} },
    loading: (__VLS_ctx.docs.loading),
    progress: (__VLS_ctx.docs.uploadProgress),
    stage: (__VLS_ctx.docs.uploadStage),
}));
const __VLS_1 = __VLS_0({
    ...{ 'onUpload': {} },
    loading: (__VLS_ctx.docs.loading),
    progress: (__VLS_ctx.docs.uploadProgress),
    stage: (__VLS_ctx.docs.uploadStage),
}, ...__VLS_functionalComponentArgsRest(__VLS_0));
let __VLS_3;
let __VLS_4;
let __VLS_5;
const __VLS_6 = {
    onUpload: (__VLS_ctx.handleUpload)
};
var __VLS_2;
if (__VLS_ctx.docs.loading && !__VLS_ctx.docs.uploadStage) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "loading-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "spinner" },
    });
}
else if (__VLS_ctx.docs.documents.length === 0) {
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
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "doc-list" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "list-meta" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.docs.documents.length);
    for (const [doc] of __VLS_getVForSourceType((__VLS_ctx.docs.documents))) {
        /** @type {[typeof DocumentCard, ]} */ ;
        // @ts-ignore
        const __VLS_7 = __VLS_asFunctionalComponent(DocumentCard, new DocumentCard({
            ...{ 'onDelete': {} },
            key: (doc.id),
            doc: (doc),
            deleting: (__VLS_ctx.docs.deletingId === doc.id),
        }));
        const __VLS_8 = __VLS_7({
            ...{ 'onDelete': {} },
            key: (doc.id),
            doc: (doc),
            deleting: (__VLS_ctx.docs.deletingId === doc.id),
        }, ...__VLS_functionalComponentArgsRest(__VLS_7));
        let __VLS_10;
        let __VLS_11;
        let __VLS_12;
        const __VLS_13 = {
            onDelete: (__VLS_ctx.handleDelete)
        };
        var __VLS_9;
    }
}
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
/** @type {__VLS_StyleScopedClasses['page-content']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['icon']} */ ;
/** @type {__VLS_StyleScopedClasses['doc-list']} */ ;
/** @type {__VLS_StyleScopedClasses['list-meta']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            DocumentCard: DocumentCard,
            FileUpload: FileUpload,
            docs: docs,
            handleUpload: handleUpload,
            handleDelete: handleDelete,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
