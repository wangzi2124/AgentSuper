/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, computed } from 'vue';
const emit = defineEmits();
const props = defineProps();
// 拖拽悬停状态
const dragOver = ref(false);
// 文件输入元素引用
const inputRef = ref();
// 是否正在上传中
const uploading = computed(() => !!(props.stage) || (props.loading && (props.progress ?? 0) > 0));
// 文件选择事件处理
function onFileInput(e) {
    const files = e.target.files;
    if (files && files[0]) {
        emit('upload', files[0]);
    }
}
// 拖拽释放事件处理
function onDrop(e) {
    dragOver.value = false;
    const files = e.dataTransfer?.files;
    if (files && files[0]) {
        emit('upload', files[0]);
    }
}
// 触发文件选择对话框
function triggerInput() {
    inputRef.value?.click();
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ onDragover: (...[$event]) => {
            __VLS_ctx.dragOver = true;
        } },
    ...{ onDragleave: (...[$event]) => {
            __VLS_ctx.dragOver = false;
        } },
    ...{ onDrop: (__VLS_ctx.onDrop) },
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.uploading ? undefined : __VLS_ctx.triggerInput();
        } },
    ...{ class: "file-upload" },
    ...{ class: ({ 'drag-over': __VLS_ctx.dragOver, uploading: __VLS_ctx.uploading }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ onChange: (__VLS_ctx.onFileInput) },
    ref: "inputRef",
    type: "file",
    accept: ".txt,.md,.pdf",
    ...{ style: {} },
});
/** @type {typeof __VLS_ctx.inputRef} */ ;
if (!__VLS_ctx.uploading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "upload-icon" },
    });
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "spinner" },
    });
}
if (!__VLS_ctx.uploading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "upload-status" },
    });
    (__VLS_ctx.stage || '上传中...');
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "hint" },
});
if (__VLS_ctx.uploading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "progress-bar" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "progress-fill" },
        ...{ style: ({ width: (__VLS_ctx.progress ?? 0) + '%' }) },
    });
}
if (__VLS_ctx.uploading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "progress-text" },
    });
    (__VLS_ctx.progress ?? 0);
}
/** @type {__VLS_StyleScopedClasses['file-upload']} */ ;
/** @type {__VLS_StyleScopedClasses['uploading']} */ ;
/** @type {__VLS_StyleScopedClasses['drag-over']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['upload-status']} */ ;
/** @type {__VLS_StyleScopedClasses['hint']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-fill']} */ ;
/** @type {__VLS_StyleScopedClasses['progress-text']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            dragOver: dragOver,
            inputRef: inputRef,
            uploading: uploading,
            onFileInput: onFileInput,
            onDrop: onDrop,
            triggerInput: triggerInput,
        };
    },
    __typeEmits: {},
    __typeProps: {},
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
    __typeEmits: {},
    __typeProps: {},
});
; /* PartiallyEnd: #4569/main.vue */
