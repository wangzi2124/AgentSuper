/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, watch } from 'vue';
import { browseDirectories } from '../api/permission';
const props = defineProps();
const emit = defineEmits();
const currentPath = ref('');
const currentName = ref('');
const parentPath = ref('');
const dirs = ref([]);
const loading = ref(false);
const error = ref('');
async function load(path) {
    loading.value = true;
    error.value = '';
    try {
        const data = await browseDirectories(path);
        currentPath.value = data.path;
        currentName.value = data.name;
        parentPath.value = data.parent;
        dirs.value = data.dirs;
    }
    catch (e) {
        error.value = e?.message || '加载目录失败';
        dirs.value = [];
    }
    finally {
        loading.value = false;
    }
}
function openDir(dir) {
    load(dir.path);
}
function goUp() {
    if (parentPath.value)
        load(parentPath.value);
    else
        load('');
}
watch(() => props.show, (v) => {
    if (v) {
        currentPath.value = '';
        currentName.value = '';
        parentPath.value = '';
        dirs.value = [];
        error.value = '';
        load('');
    }
});
function confirm() {
    if (currentPath.value)
        emit('select', currentPath.value);
}
function cancel() {
    emit('close');
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
const __VLS_0 = {}.Teleport;
/** @type {[typeof __VLS_components.Teleport, typeof __VLS_components.Teleport, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    to: "body",
}));
const __VLS_2 = __VLS_1({
    to: "body",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_3.slots.default;
if (__VLS_ctx.show) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (__VLS_ctx.cancel) },
        ...{ class: "dir-modal-overlay" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "dir-modal" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "dir-modal-header" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "dir-modal-title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.cancel) },
        ...{ class: "dir-modal-close" },
        title: "关闭",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "14",
        height: "14",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
        'stroke-linecap': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M18 6L6 18M6 6l12 12",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "dir-path" },
    });
    if (__VLS_ctx.currentPath) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.goUp) },
            ...{ class: "dir-up" },
            title: "返回上级/盘符列表",
        });
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "dir-path-text" },
        title: (__VLS_ctx.currentPath),
    });
    (__VLS_ctx.currentPath || '请选择盘符/目录');
    if (__VLS_ctx.error) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "dir-error" },
        });
        (__VLS_ctx.error);
    }
    else if (__VLS_ctx.loading) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "dir-loading" },
        });
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "dir-list" },
        });
        if (__VLS_ctx.dirs.length === 0) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "dir-empty" },
            });
        }
        for (const [d] of __VLS_getVForSourceType((__VLS_ctx.dirs))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ onDblclick: (...[$event]) => {
                        if (!(__VLS_ctx.show))
                            return;
                        if (!!(__VLS_ctx.error))
                            return;
                        if (!!(__VLS_ctx.loading))
                            return;
                        __VLS_ctx.openDir(d);
                    } },
                key: (d.path),
                ...{ class: "dir-item" },
                title: (d.path),
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.show))
                            return;
                        if (!!(__VLS_ctx.error))
                            return;
                        if (!!(__VLS_ctx.loading))
                            return;
                        __VLS_ctx.openDir(d);
                    } },
                ...{ class: "dir-item-main" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
                width: "14",
                height: "14",
                viewBox: "0 0 24 24",
                fill: "none",
                stroke: "currentColor",
                'stroke-width': "2",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
                d: "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "dir-item-name" },
            });
            (d.name);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.show))
                            return;
                        if (!!(__VLS_ctx.error))
                            return;
                        if (!!(__VLS_ctx.loading))
                            return;
                        __VLS_ctx.openDir(d);
                    } },
                ...{ class: "dir-item-open" },
                title: "进入",
            });
        }
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "dir-modal-footer" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.cancel) },
        ...{ class: "dir-btn dir-btn-secondary" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.confirm) },
        ...{ class: "dir-btn dir-btn-primary" },
        disabled: (!__VLS_ctx.currentPath),
    });
}
var __VLS_3;
/** @type {__VLS_StyleScopedClasses['dir-modal-overlay']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-modal']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-modal-header']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-modal-title']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-modal-close']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-path']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-up']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-path-text']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-error']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-loading']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-list']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-empty']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-item']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-item-main']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-item-name']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-item-open']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-modal-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-btn-secondary']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['dir-btn-primary']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            currentPath: currentPath,
            dirs: dirs,
            loading: loading,
            error: error,
            openDir: openDir,
            goUp: goUp,
            confirm: confirm,
            cancel: cancel,
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
