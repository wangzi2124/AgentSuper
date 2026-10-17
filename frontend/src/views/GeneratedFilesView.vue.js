/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { onMounted, ref } from 'vue';
import { useGeneratedStore } from '../stores/generated';
import { getGeneratedContent, downloadGenerated } from '../api/generated';
// 生成文件状态管理
const store = useGeneratedStore();
// 正在删除的文件名集合
const deleting = ref(new Set());
// 当前正在运行的文件名
const runningFile = ref(null);
// 运行输出内容
const runOutput = ref(null);
// 运行错误信息
const runError = ref(null);
// 组件挂载时加载所有生成文件
onMounted(() => {
    store.fetchAll();
});
// 删除指定生成文件
async function handleDelete(filename) {
    if (!confirm(`Delete "${filename}"?`))
        return;
    deleting.value.add(filename);
    try {
        await store.remove(filename);
    }
    catch (err) {
        alert(err.message);
    }
    finally {
        deleting.value.delete(filename);
    }
}
// 判断是否为JavaScript文件
function isJsFile(filename) {
    return filename.endsWith('.js');
}
// 下载文件（带鉴权头，通过 blob 触发浏览器下载）
async function handleDownload(filename) {
    try {
        await downloadGenerated(filename);
    }
    catch (err) {
        alert(err.message || '下载失败');
    }
}
// 在浏览器沙箱中运行JavaScript文件
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
                    writeFileSync: (path, data) => {
                        writtenFiles[path] = data;
                        logs.push(`[fs] wrote ${data.length} bytes to ${path}`);
                    },
                    readFileSync: (path) => {
                        logs.push(`[fs] read ${path} (returned empty mock)`);
                        return '';
                    },
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
        if (result !== undefined) {
            logs.push('=> ' + String(result));
        }
        const output = logs.join('\n') || '(no output)';
        if (Object.keys(writtenFiles).length > 0) {
            runOutput.value = output + '\n\n--- Files written ---\n' +
                Object.entries(writtenFiles).map(([p, d]) => `${p} (${d.length} bytes)`).join('\n');
        }
        else {
            runOutput.value = output;
        }
    }
    catch (err) {
        runError.value = err.message || String(err);
    }
}
// 关闭输出弹窗
function closeOutput() {
    runningFile.value = null;
    runOutput.value = null;
    runError.value = null;
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['search-input']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-thumb']} */ ;
/** @type {__VLS_StyleScopedClasses['file-thumb']} */ ;
/** @type {__VLS_StyleScopedClasses['file-name']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-run']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-run']} */ ;
/** @type {__VLS_StyleScopedClasses['output-body']} */ ;
/** @type {__VLS_StyleScopedClasses['output-body']} */ ;
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
    ...{ class: "search-bar" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    value: (__VLS_ctx.store.searchQuery),
    type: "text",
    placeholder: "按文件名搜索...",
    ...{ class: "search-input" },
});
if (__VLS_ctx.store.loading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "loading-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "spinner" },
    });
}
else if (__VLS_ctx.store.filteredFiles.length === 0) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "empty-state" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "icon" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
    (__VLS_ctx.store.searchQuery ? '没有匹配的文件' : '还没有生成文件');
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ style: {} },
    });
    (__VLS_ctx.store.searchQuery ? '换个搜索词试试。' : '请让 AI Agent 创建文件。');
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "file-list" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "file-count" },
    });
    (__VLS_ctx.store.filteredFiles.length);
    for (const [f] of __VLS_getVForSourceType((__VLS_ctx.store.filteredFiles))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "file-row" },
            key: (f.filename),
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "file-info" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "file-thumb" },
            ...{ class: (__VLS_ctx.isJsFile(f.filename) ? 'js' : 'doc') },
        });
        (__VLS_ctx.isJsFile(f.filename) ? 'JS' : 'DOC');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "file-details" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "file-name" },
        });
        (f.filename);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "file-meta" },
        });
        (__VLS_ctx.store.formatSize(f.size));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "file-actions" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.store.loading))
                        return;
                    if (!!(__VLS_ctx.store.filteredFiles.length === 0))
                        return;
                    __VLS_ctx.handleDownload(f.filename);
                } },
            ...{ class: "btn btn-sm" },
        });
        if (__VLS_ctx.isJsFile(f.filename)) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.store.loading))
                            return;
                        if (!!(__VLS_ctx.store.filteredFiles.length === 0))
                            return;
                        if (!(__VLS_ctx.isJsFile(f.filename)))
                            return;
                        __VLS_ctx.handleRun(f.filename);
                    } },
                ...{ class: "btn btn-run btn-sm" },
                disabled: (__VLS_ctx.runningFile === f.filename),
            });
            (__VLS_ctx.runningFile === f.filename ? '运行中...' : '运行');
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.store.loading))
                        return;
                    if (!!(__VLS_ctx.store.filteredFiles.length === 0))
                        return;
                    __VLS_ctx.handleDelete(f.filename);
                } },
            ...{ class: "btn btn-sm btn-danger" },
            disabled: (__VLS_ctx.deleting.has(f.filename)),
        });
        (__VLS_ctx.deleting.has(f.filename) ? '删除中...' : '删除');
    }
}
if (__VLS_ctx.runningFile) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (__VLS_ctx.closeOutput) },
        ...{ class: "output-overlay" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "output-modal" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "output-header" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "output-title" },
    });
    (__VLS_ctx.runningFile);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.closeOutput) },
        ...{ class: "btn btn-sm" },
    });
    if (__VLS_ctx.runOutput) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
            ...{ class: "output-body" },
        });
        (__VLS_ctx.runOutput);
    }
    else if (__VLS_ctx.runError) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
            ...{ class: "output-body error" },
        });
        (__VLS_ctx.runError);
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "output-body running" },
        });
    }
}
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
/** @type {__VLS_StyleScopedClasses['page-content']} */ ;
/** @type {__VLS_StyleScopedClasses['search-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['search-input']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['icon']} */ ;
/** @type {__VLS_StyleScopedClasses['file-list']} */ ;
/** @type {__VLS_StyleScopedClasses['file-count']} */ ;
/** @type {__VLS_StyleScopedClasses['file-row']} */ ;
/** @type {__VLS_StyleScopedClasses['file-info']} */ ;
/** @type {__VLS_StyleScopedClasses['file-thumb']} */ ;
/** @type {__VLS_StyleScopedClasses['file-details']} */ ;
/** @type {__VLS_StyleScopedClasses['file-name']} */ ;
/** @type {__VLS_StyleScopedClasses['file-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['file-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-run']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
/** @type {__VLS_StyleScopedClasses['output-overlay']} */ ;
/** @type {__VLS_StyleScopedClasses['output-modal']} */ ;
/** @type {__VLS_StyleScopedClasses['output-header']} */ ;
/** @type {__VLS_StyleScopedClasses['output-title']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['output-body']} */ ;
/** @type {__VLS_StyleScopedClasses['output-body']} */ ;
/** @type {__VLS_StyleScopedClasses['error']} */ ;
/** @type {__VLS_StyleScopedClasses['output-body']} */ ;
/** @type {__VLS_StyleScopedClasses['running']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            store: store,
            deleting: deleting,
            runningFile: runningFile,
            runOutput: runOutput,
            runError: runError,
            handleDelete: handleDelete,
            isJsFile: isJsFile,
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
