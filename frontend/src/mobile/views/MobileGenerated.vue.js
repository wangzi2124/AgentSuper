/// <reference types="../../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { onMounted, ref } from 'vue';
import { useGeneratedStore } from '../../stores/generated';
import { getGeneratedContent, downloadGenerated } from '../../api/generated';
import { showConfirmDialog, showToast } from 'vant';
const store = useGeneratedStore();
const runningFile = ref(null);
const runOutput = ref(null);
const runError = ref(null);
onMounted(() => {
    store.fetchAll();
});
function isJsFile(filename) {
    return filename.endsWith('.js');
}
async function handleDelete(filename) {
    try {
        await showConfirmDialog({ title: '删除文件', message: `确定删除「${filename}」？` });
        await store.remove(filename);
        showToast('已删除');
    }
    catch (err) {
        if (err?.message && !String(err.message).includes('cancel'))
            showToast(err.message);
    }
}
async function handleDownload(filename) {
    try {
        await downloadGenerated(filename);
    }
    catch (err) {
        showToast(err.message || '下载失败');
    }
}
async function handleRun(filename) {
    runningFile.value = filename;
    runOutput.value = null;
    runError.value = null;
    try {
        const code = await getGeneratedContent(filename);
        const logs = [];
        const writtenFiles = {};
        const mockConsole = {
            log: (...args) => logs.push(args.map(a => String(a)).join(' ')),
            warn: (...args) => logs.push('[warn] ' + args.map(a => String(a)).join(' ')),
            error: (...args) => logs.push('[error] ' + args.map(a => String(a)).join(' ')),
        };
        const mockRequire = (mod) => {
            if (mod === 'fs') {
                return {
                    writeFileSync: (path, data) => { writtenFiles[path] = data; logs.push(`[fs] wrote ${data.length} bytes to ${path}`); },
                    readFileSync: (path) => { logs.push(`[fs] read ${path} (returned empty mock)`); return ''; },
                    existsSync: () => true,
                    mkdirSync: () => { },
                };
            }
            if (mod === 'path') {
                return {
                    join: (...args) => args.join('/'),
                    resolve: (...args) => args.join('/'),
                    dirname: (p) => p.split('/').slice(0, -1).join('/'),
                    basename: (p) => p.split('/').pop() || p,
                };
            }
            throw new Error(`require('${mod}') is not supported in browser. Try running this file on the server side.`);
        };
        const fn = new Function('console', 'require', code);
        const result = fn(mockConsole, mockRequire);
        if (result !== undefined)
            logs.push('=> ' + String(result));
        const output = logs.join('\n') || '(no output)';
        runOutput.value = Object.keys(writtenFiles).length > 0
            ? output + '\n\n--- Files written ---\n' + Object.entries(writtenFiles).map(([p, d]) => `${p} (${d.length} bytes)`).join('\n')
            : output;
    }
    catch (err) {
        runError.value = err.message || String(err);
    }
}
function closeOutput() {
    runningFile.value = null;
    runOutput.value = null;
    runError.value = null;
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['run-body']} */ ;
/** @type {__VLS_StyleScopedClasses['file-list']} */ ;
/** @type {__VLS_StyleScopedClasses['count']} */ ;
/** @type {__VLS_StyleScopedClasses['m-file-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['m-file-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['van-cell__left-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['m-file-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['van-cell__left-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['m-file-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['m-file-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['m-generated']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-run']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-dl']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-del']} */ ;
/** @type {__VLS_StyleScopedClasses['run-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['run-head']} */ ;
/** @type {__VLS_StyleScopedClasses['run-head']} */ ;
/** @type {__VLS_StyleScopedClasses['run-title']} */ ;
/** @type {__VLS_StyleScopedClasses['run-head']} */ ;
/** @type {__VLS_StyleScopedClasses['run-body']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-generated" },
});
const __VLS_0 = {}.VanSearch;
/** @type {[typeof __VLS_components.VanSearch, typeof __VLS_components.vanSearch, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    modelValue: (__VLS_ctx.store.searchQuery),
    placeholder: "按文件名搜索...",
}));
const __VLS_2 = __VLS_1({
    modelValue: (__VLS_ctx.store.searchQuery),
    placeholder: "按文件名搜索...",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
if (__VLS_ctx.store.loading) {
    const __VLS_4 = {}.VanLoading;
    /** @type {[typeof __VLS_components.VanLoading, typeof __VLS_components.vanLoading, ]} */ ;
    // @ts-ignore
    const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
        ...{ class: "loading" },
    }));
    const __VLS_6 = __VLS_5({
        ...{ class: "loading" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_5));
}
else if (__VLS_ctx.store.filteredFiles.length === 0) {
    const __VLS_8 = {}.VanEmpty;
    /** @type {[typeof __VLS_components.VanEmpty, typeof __VLS_components.vanEmpty, ]} */ ;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent(__VLS_8, new __VLS_8({
        image: "file",
        description: (__VLS_ctx.store.searchQuery ? '没有匹配的文件' : '还没有生成文件'),
    }));
    const __VLS_10 = __VLS_9({
        image: "file",
        description: (__VLS_ctx.store.searchQuery ? '没有匹配的文件' : '还没有生成文件'),
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "file-list" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "count" },
    });
    (__VLS_ctx.store.filteredFiles.length);
    const __VLS_12 = {}.VanCellGroup;
    /** @type {[typeof __VLS_components.VanCellGroup, typeof __VLS_components.vanCellGroup, typeof __VLS_components.VanCellGroup, typeof __VLS_components.vanCellGroup, ]} */ ;
    // @ts-ignore
    const __VLS_13 = __VLS_asFunctionalComponent(__VLS_12, new __VLS_12({
        inset: true,
    }));
    const __VLS_14 = __VLS_13({
        inset: true,
    }, ...__VLS_functionalComponentArgsRest(__VLS_13));
    __VLS_15.slots.default;
    for (const [f] of __VLS_getVForSourceType((__VLS_ctx.store.filteredFiles))) {
        const __VLS_16 = {}.VanSwipeCell;
        /** @type {[typeof __VLS_components.VanSwipeCell, typeof __VLS_components.vanSwipeCell, typeof __VLS_components.VanSwipeCell, typeof __VLS_components.vanSwipeCell, ]} */ ;
        // @ts-ignore
        const __VLS_17 = __VLS_asFunctionalComponent(__VLS_16, new __VLS_16({
            key: (f.filename),
        }));
        const __VLS_18 = __VLS_17({
            key: (f.filename),
        }, ...__VLS_functionalComponentArgsRest(__VLS_17));
        __VLS_19.slots.default;
        const __VLS_20 = {}.VanCell;
        /** @type {[typeof __VLS_components.VanCell, typeof __VLS_components.vanCell, ]} */ ;
        // @ts-ignore
        const __VLS_21 = __VLS_asFunctionalComponent(__VLS_20, new __VLS_20({
            ...{ 'onClick': {} },
            title: (f.filename),
            label: (__VLS_ctx.store.formatSize(f.size)),
            icon: (__VLS_ctx.isJsFile(f.filename) ? 'lightning' : 'description'),
            ...{ class: "m-file-cell" },
            ...{ class: (__VLS_ctx.isJsFile(f.filename) ? 'ftype-js' : 'ftype-doc') },
            isLink: true,
        }));
        const __VLS_22 = __VLS_21({
            ...{ 'onClick': {} },
            title: (f.filename),
            label: (__VLS_ctx.store.formatSize(f.size)),
            icon: (__VLS_ctx.isJsFile(f.filename) ? 'lightning' : 'description'),
            ...{ class: "m-file-cell" },
            ...{ class: (__VLS_ctx.isJsFile(f.filename) ? 'ftype-js' : 'ftype-doc') },
            isLink: true,
        }, ...__VLS_functionalComponentArgsRest(__VLS_21));
        let __VLS_24;
        let __VLS_25;
        let __VLS_26;
        const __VLS_27 = {
            onClick: (...[$event]) => {
                if (!!(__VLS_ctx.store.loading))
                    return;
                if (!!(__VLS_ctx.store.filteredFiles.length === 0))
                    return;
                __VLS_ctx.isJsFile(f.filename) ? __VLS_ctx.handleRun(f.filename) : __VLS_ctx.handleDownload(f.filename);
            }
        };
        var __VLS_23;
        {
            const { right: __VLS_thisSlot } = __VLS_19.slots;
            if (__VLS_ctx.isJsFile(f.filename)) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                    ...{ onClick: (...[$event]) => {
                            if (!!(__VLS_ctx.store.loading))
                                return;
                            if (!!(__VLS_ctx.store.filteredFiles.length === 0))
                                return;
                            if (!(__VLS_ctx.isJsFile(f.filename)))
                                return;
                            __VLS_ctx.handleRun(f.filename);
                        } },
                    ...{ class: "swipe-btn swipe-run" },
                });
            }
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.store.loading))
                            return;
                        if (!!(__VLS_ctx.store.filteredFiles.length === 0))
                            return;
                        __VLS_ctx.handleDownload(f.filename);
                    } },
                ...{ class: "swipe-btn swipe-dl" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.store.loading))
                            return;
                        if (!!(__VLS_ctx.store.filteredFiles.length === 0))
                            return;
                        __VLS_ctx.handleDelete(f.filename);
                    } },
                ...{ class: "swipe-btn swipe-del" },
            });
        }
        var __VLS_19;
    }
    var __VLS_15;
}
const __VLS_28 = {}.VanPopup;
/** @type {[typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, ]} */ ;
// @ts-ignore
const __VLS_29 = __VLS_asFunctionalComponent(__VLS_28, new __VLS_28({
    ...{ 'onClose': {} },
    show: (!!__VLS_ctx.runningFile),
    round: true,
    position: "bottom",
    ...{ style: ({ height: '70%' }) },
}));
const __VLS_30 = __VLS_29({
    ...{ 'onClose': {} },
    show: (!!__VLS_ctx.runningFile),
    round: true,
    position: "bottom",
    ...{ style: ({ height: '70%' }) },
}, ...__VLS_functionalComponentArgsRest(__VLS_29));
let __VLS_32;
let __VLS_33;
let __VLS_34;
const __VLS_35 = {
    onClose: (__VLS_ctx.closeOutput)
};
__VLS_31.slots.default;
if (__VLS_ctx.runningFile) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "run-panel" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "run-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "run-title" },
    });
    (__VLS_ctx.runningFile);
    const __VLS_36 = {}.VanButton;
    /** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
    // @ts-ignore
    const __VLS_37 = __VLS_asFunctionalComponent(__VLS_36, new __VLS_36({
        ...{ 'onClick': {} },
        size: "small",
    }));
    const __VLS_38 = __VLS_37({
        ...{ 'onClick': {} },
        size: "small",
    }, ...__VLS_functionalComponentArgsRest(__VLS_37));
    let __VLS_40;
    let __VLS_41;
    let __VLS_42;
    const __VLS_43 = {
        onClick: (__VLS_ctx.closeOutput)
    };
    __VLS_39.slots.default;
    var __VLS_39;
    if (__VLS_ctx.runOutput) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
            ...{ class: "run-body" },
        });
        (__VLS_ctx.runOutput);
    }
    else if (__VLS_ctx.runError) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
            ...{ class: "run-body error" },
        });
        (__VLS_ctx.runError);
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "run-body" },
        });
    }
}
var __VLS_31;
/** @type {__VLS_StyleScopedClasses['m-generated']} */ ;
/** @type {__VLS_StyleScopedClasses['loading']} */ ;
/** @type {__VLS_StyleScopedClasses['file-list']} */ ;
/** @type {__VLS_StyleScopedClasses['count']} */ ;
/** @type {__VLS_StyleScopedClasses['m-file-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-run']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-dl']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-del']} */ ;
/** @type {__VLS_StyleScopedClasses['run-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['run-head']} */ ;
/** @type {__VLS_StyleScopedClasses['run-title']} */ ;
/** @type {__VLS_StyleScopedClasses['run-body']} */ ;
/** @type {__VLS_StyleScopedClasses['run-body']} */ ;
/** @type {__VLS_StyleScopedClasses['error']} */ ;
/** @type {__VLS_StyleScopedClasses['run-body']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            store: store,
            runningFile: runningFile,
            runOutput: runOutput,
            runError: runError,
            isJsFile: isJsFile,
            handleDelete: handleDelete,
            handleDownload: handleDownload,
            handleRun: handleRun,
            closeOutput: closeOutput,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
