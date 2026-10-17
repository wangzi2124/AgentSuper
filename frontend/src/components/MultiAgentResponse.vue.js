/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { computed, ref } from 'vue';
import MarkdownContent from './MarkdownContent.vue';
const props = defineProps();
const emit = defineEmits();
// [查看改动] diff 按需拉取（体积大不进 IndexedDB）：点「查看 diff」才请求，
// 拉回来缓存在 message.diffFiles 上，重复展开不再请求。
const diffOpen = ref(false);
const diffLoading = ref(false);
const diffError = ref('');
async function toggleDiff() {
    diffOpen.value = !diffOpen.value;
    if (!diffOpen.value)
        return;
    if (props.message.diffFiles?.length || props.message.diffReason)
        return;
    if (!props.loadDiff)
        return;
    await loadStepDiff();
}
// [逐步快照] 切到第 N 步（opencode 语义：每个写工具调用各一个 tree）。
// 步骤数由整轮 diff 响应带回，只有 1 步时不必显示选择器。
async function loadStepDiff(step) {
    if (!props.loadDiff)
        return;
    diffLoading.value = true;
    diffError.value = '';
    try {
        await props.loadDiff(props.message.id, step);
    }
    catch (e) {
        diffError.value = e instanceof Error ? e.message : String(e);
    }
    finally {
        diffLoading.value = false;
    }
}
const showStepPicker = computed(() => (props.message.diffSteps ?? 0) > 1);
const stepLabels = computed(() => {
    const n = props.message.diffSteps ?? 0;
    return Array.from({ length: n }, (_, i) => i);
});
// 把 unified diff 拆成行；行首 +/-/空格决定着色（@@ 与 diff/index/mode 头不着色）
function splitDiff(diff) {
    if (!diff)
        return [];
    return diff.split('\n');
}
function diffLineClass(line) {
    if (line.startsWith('@@'))
        return 'hunk';
    // --- / +++ 是文件头（路径行），不是增删内容
    if (line.startsWith('---') || line.startsWith('+++'))
        return 'meta';
    if (line.startsWith('+'))
        return 'add';
    if (line.startsWith('-'))
        return 'del';
    if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('new file')
        || line.startsWith('deleted file') || line.startsWith('similarity ')
        || line.startsWith('rename '))
        return 'meta';
    return 'ctx';
}
// [C8] 计划文件路径：整块可点击（点一下即复制，便于粘到编辑器/资源管理器打开），
// 浏览器无法直接打开后端本地文件，故用「点击复制 + 显式提示」而非伪链接
// （href="file://" 在 http(s) 页面会被浏览器拦截）。navigator.clipboard 在非安全
// 上下文可能不可用 → 降级为 prompt 让用户手动复制。
const copied = ref(false);
async function copyPlanPath() {
    const path = props.message.plan_path;
    if (!path)
        return;
    try {
        await navigator.clipboard.writeText(path);
        copied.value = true;
        setTimeout(() => { copied.value = false; }, 1500);
    }
    catch {
        copied.value = false;
        window.prompt('请复制计划文件路径：', path);
    }
}
// 单 Agent 路由时，最终答案与 Agent 面板内容完全一致，
// 再渲染一遍会造成"两条最终答案"的重复。仅在内容有新增信息时才展示。
const showFinalAnswer = computed(() => {
    const content = props.message.content;
    if (!content)
        return false;
    const agents = props.message.agents;
    if (agents.length === 1 && agents[0].content && agents[0].content.trim() === content.trim()) {
        return false;
    }
    return true;
});
// opencode 风格状态符号
function getStatusIcon(status) {
    if (status === 'completed')
        return '✓';
    if (status === 'failed')
        return '✕';
    return '•';
}
function formatDuration(ms) {
    if (ms == null)
        return '';
    if (ms < 1000)
        return `${ms.toFixed(0)}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}
// 工具/步骤卡片标题（对齐 opencode BasicTool trigger 的子标题文案）
function stepTitle(step) {
    if (step.detail)
        return step.detail;
    if (step.tool_name)
        return step.tool_name;
    return step.name;
}
// 极简工具名展示（去掉 tool_ 前缀，对齐 opencode 工具名可读性）
function shortToolName(name) {
    if (!name)
        return '工具';
    return name.replace(/^plugin_[^_]+_/, '').replace(/^tool_/, '');
}
// 卡片标签：真实工具调用（带 tool_name）显示工具名；其余是模型的阶段步骤
// （检索/重排/生成/探索…），显示「思考」，避免把生成/思考阶段误标成「工具」。
function cardLabel(step) {
    return step.tool_name ? shortToolName(step.tool_name) : '思考';
}
// 按输出顺序排列的展示部件：优先 parts（真实交错），否则组装 steps（含最终正文）
function orderedParts(agent) {
    if (agent.parts && agent.parts.length > 0)
        return agent.parts;
    // 回退：历史回放（服务端只存 steps+content）——
    // 组装 [工具卡片…, 最终正文]，最大程度贴近 opencode 的"工具在前、答案收尾"。
    const parts = (agent.steps || []).map((s, i) => ({
        seq: i,
        kind: 'tool',
        step: s,
    }));
    if (agent.content) {
        parts.push({ seq: parts.length, kind: 'text', text: agent.content });
    }
    return parts;
}
// 文件改动状态 → 中文徽标文案
function statusLabel(status) {
    if (status === 'added')
        return '新增';
    if (status === 'deleted')
        return '删除';
    return '修改';
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['plan-file']} */ ;
/** @type {__VLS_StyleScopedClasses['plan-file']} */ ;
/** @type {__VLS_StyleScopedClasses['pf-copy']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-restore']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-restore']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-restore']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-lines']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-lines']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-lines']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-step']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-file']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-body']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-dl']} */ ;
/** @type {__VLS_StyleScopedClasses['add']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-dl']} */ ;
/** @type {__VLS_StyleScopedClasses['del']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-dl']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-dl']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-dl']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "response" },
    ...{ class: ({ loading: __VLS_ctx.isLast && !!__VLS_ctx.routingStatus, error: __VLS_ctx.message.isError }) },
});
if (__VLS_ctx.isLast && !__VLS_ctx.routingStatus && !__VLS_ctx.message.agents.length && !__VLS_ctx.message.content && !__VLS_ctx.message.isError) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "routing" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "spinner" },
    });
}
if (__VLS_ctx.isLast && __VLS_ctx.routingStatus) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "routing" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "spinner" },
    });
    (__VLS_ctx.routingStatus);
}
if (__VLS_ctx.message.agents.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "agents" },
    });
    for (const [a] of __VLS_getVForSourceType((__VLS_ctx.message.agents))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (a.agent_id),
            ...{ class: "agent" },
            ...{ class: (a.status) },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "agent-h" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "avatar" },
        });
        (a.agent_avatar || '🤖');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "name" },
        });
        (a.agent_name);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "badge" },
            ...{ class: (a.status) },
        });
        (a.status === 'running' ? '● 运行中' : a.status === 'completed' ? '✓ 完成' : '✗ 失败');
        if (__VLS_ctx.orderedParts(a).length) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "o-parts" },
            });
            for (const [p] of __VLS_getVForSourceType((__VLS_ctx.orderedParts(a)))) {
                (p.seq);
                if (p.kind === 'text') {
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                        ...{ class: "text o-text" },
                    });
                    /** @type {[typeof MarkdownContent, ]} */ ;
                    // @ts-ignore
                    const __VLS_0 = __VLS_asFunctionalComponent(MarkdownContent, new MarkdownContent({
                        text: (p.text || ''),
                    }));
                    const __VLS_1 = __VLS_0({
                        text: (p.text || ''),
                    }, ...__VLS_functionalComponentArgsRest(__VLS_0));
                }
                else if (p.kind === 'tool' && p.step) {
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                        ...{ class: "o-tool" },
                        ...{ class: ([p.step.status, { 'is-step': !p.step.tool_name }]) },
                    });
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                        ...{ class: "o-tool-icon" },
                        ...{ class: (p.step.status) },
                    });
                    (__VLS_ctx.getStatusIcon(p.step.status));
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                        ...{ class: "o-tool-name" },
                    });
                    (__VLS_ctx.cardLabel(p.step));
                    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                        ...{ class: "o-tool-title" },
                    });
                    (__VLS_ctx.stepTitle(p.step));
                    if (p.step.duration_ms != null) {
                        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                            ...{ class: "o-tool-time" },
                        });
                        (__VLS_ctx.formatDuration(p.step.duration_ms));
                    }
                    else if (p.step.status === 'running') {
                        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                            ...{ class: "o-tool-spin" },
                        });
                    }
                }
            }
        }
        else if (a.status === 'running') {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "steps" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "step-item running" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "step-row" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "step-icon" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "step-name" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "step-time spinning" },
            });
        }
        if (a.error) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "err" },
            });
            (a.error);
        }
    }
}
if (__VLS_ctx.showFinalAnswer) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "text" },
    });
    /** @type {[typeof MarkdownContent, ]} */ ;
    // @ts-ignore
    const __VLS_3 = __VLS_asFunctionalComponent(MarkdownContent, new MarkdownContent({
        text: (__VLS_ctx.message.content),
    }));
    const __VLS_4 = __VLS_3({
        text: (__VLS_ctx.message.content),
    }, ...__VLS_functionalComponentArgsRest(__VLS_3));
}
if (__VLS_ctx.message.plan_path) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (__VLS_ctx.copyPlanPath) },
        ...{ onKeyup: (__VLS_ctx.copyPlanPath) },
        ...{ class: "plan-file" },
        role: "button",
        tabindex: "0",
        title: "点击复制计划文件路径",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pf-icon" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pf-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({
        ...{ class: "pf-path" },
    });
    (__VLS_ctx.message.plan_path);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pf-copy" },
    });
    (__VLS_ctx.copied ? '已复制' : '点击复制路径');
}
if (__VLS_ctx.message.files_changed?.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "files-changed" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "fc-title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    (__VLS_ctx.message.files_changed.length);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "fc-actions" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.toggleDiff) },
        ...{ class: "fc-toggle" },
    });
    (__VLS_ctx.diffOpen ? '收起 diff' : '查看 diff');
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.message.files_changed?.length))
                    return;
                __VLS_ctx.emit('restore');
            } },
        ...{ class: "fc-restore" },
        disabled: (__VLS_ctx.message.snapshotRestored),
        ...{ class: ({ restored: __VLS_ctx.message.snapshotRestored }) },
    });
    (__VLS_ctx.message.snapshotRestored ? '已撤回' : '撤回本轮改动');
    for (const [fc] of __VLS_getVForSourceType((__VLS_ctx.message.files_changed))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (fc.file),
            ...{ class: "fc-row" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "fc-badge" },
            ...{ class: (fc.status) },
        });
        (__VLS_ctx.statusLabel(fc.status));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "fc-file" },
            title: (fc.file),
        });
        (fc.file);
        if (fc.external) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "fc-lines binary" },
            });
        }
        else if (fc.binary) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "fc-lines binary" },
            });
        }
        else {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "fc-lines" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "add" },
            });
            (fc.additions ?? 0);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "del" },
            });
            (fc.deletions ?? 0);
        }
    }
    if (__VLS_ctx.diffOpen) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "fc-diff" },
        });
        if (__VLS_ctx.showStepPicker) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "fc-steps" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                ...{ onClick: (...[$event]) => {
                        if (!(__VLS_ctx.message.files_changed?.length))
                            return;
                        if (!(__VLS_ctx.diffOpen))
                            return;
                        if (!(__VLS_ctx.showStepPicker))
                            return;
                        __VLS_ctx.loadStepDiff(undefined);
                    } },
                ...{ class: "fc-step" },
                ...{ class: ({ active: __VLS_ctx.message.diffStep == null }) },
            });
            for (const [s] of __VLS_getVForSourceType((__VLS_ctx.stepLabels))) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
                    ...{ onClick: (...[$event]) => {
                            if (!(__VLS_ctx.message.files_changed?.length))
                                return;
                            if (!(__VLS_ctx.diffOpen))
                                return;
                            if (!(__VLS_ctx.showStepPicker))
                                return;
                            __VLS_ctx.loadStepDiff(s);
                        } },
                    key: (s),
                    ...{ class: "fc-step" },
                    ...{ class: ({ active: __VLS_ctx.message.diffStep === s }) },
                });
                (s + 1);
            }
        }
        if (__VLS_ctx.diffLoading) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "fc-diff-hint" },
            });
        }
        else if (__VLS_ctx.diffError) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "fc-diff-hint" },
            });
            (__VLS_ctx.diffError);
        }
        else if (__VLS_ctx.message.diffReason) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "fc-diff-hint" },
            });
            (__VLS_ctx.message.diffReason);
        }
        else if (!__VLS_ctx.message.diffFiles?.length) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "fc-diff-hint" },
            });
        }
        for (const [d] of __VLS_getVForSourceType((__VLS_ctx.message.diffFiles))) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                key: (d.file),
                ...{ class: "fc-diff-file" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "fc-diff-path" },
            });
            (d.file);
            __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
                ...{ class: "fc-diff-body" },
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
            for (const [line, i] of __VLS_getVForSourceType((__VLS_ctx.splitDiff(d.diff)))) {
                __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                    key: (i),
                    ...{ class: "fc-dl" },
                    ...{ class: (__VLS_ctx.diffLineClass(line)) },
                });
                (line);
                ('\n');
            }
        }
        if (__VLS_ctx.message.diffTruncated) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ class: "fc-diff-hint" },
            });
        }
    }
}
if (__VLS_ctx.message.isError && __VLS_ctx.message.errorInfo) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "err" },
    });
    (__VLS_ctx.message.errorInfo.message);
}
/** @type {__VLS_StyleScopedClasses['response']} */ ;
/** @type {__VLS_StyleScopedClasses['loading']} */ ;
/** @type {__VLS_StyleScopedClasses['error']} */ ;
/** @type {__VLS_StyleScopedClasses['routing']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['routing']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['agents']} */ ;
/** @type {__VLS_StyleScopedClasses['agent']} */ ;
/** @type {__VLS_StyleScopedClasses['agent-h']} */ ;
/** @type {__VLS_StyleScopedClasses['avatar']} */ ;
/** @type {__VLS_StyleScopedClasses['name']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['o-parts']} */ ;
/** @type {__VLS_StyleScopedClasses['text']} */ ;
/** @type {__VLS_StyleScopedClasses['o-text']} */ ;
/** @type {__VLS_StyleScopedClasses['o-tool']} */ ;
/** @type {__VLS_StyleScopedClasses['is-step']} */ ;
/** @type {__VLS_StyleScopedClasses['o-tool-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['o-tool-name']} */ ;
/** @type {__VLS_StyleScopedClasses['o-tool-title']} */ ;
/** @type {__VLS_StyleScopedClasses['o-tool-time']} */ ;
/** @type {__VLS_StyleScopedClasses['o-tool-spin']} */ ;
/** @type {__VLS_StyleScopedClasses['steps']} */ ;
/** @type {__VLS_StyleScopedClasses['step-item']} */ ;
/** @type {__VLS_StyleScopedClasses['running']} */ ;
/** @type {__VLS_StyleScopedClasses['step-row']} */ ;
/** @type {__VLS_StyleScopedClasses['step-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['step-name']} */ ;
/** @type {__VLS_StyleScopedClasses['step-time']} */ ;
/** @type {__VLS_StyleScopedClasses['spinning']} */ ;
/** @type {__VLS_StyleScopedClasses['err']} */ ;
/** @type {__VLS_StyleScopedClasses['text']} */ ;
/** @type {__VLS_StyleScopedClasses['plan-file']} */ ;
/** @type {__VLS_StyleScopedClasses['pf-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['pf-label']} */ ;
/** @type {__VLS_StyleScopedClasses['pf-path']} */ ;
/** @type {__VLS_StyleScopedClasses['pf-copy']} */ ;
/** @type {__VLS_StyleScopedClasses['files-changed']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-title']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-restore']} */ ;
/** @type {__VLS_StyleScopedClasses['restored']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-row']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-file']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-lines']} */ ;
/** @type {__VLS_StyleScopedClasses['binary']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-lines']} */ ;
/** @type {__VLS_StyleScopedClasses['binary']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-lines']} */ ;
/** @type {__VLS_StyleScopedClasses['add']} */ ;
/** @type {__VLS_StyleScopedClasses['del']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-steps']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-step']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-step']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-file']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-path']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-body']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-dl']} */ ;
/** @type {__VLS_StyleScopedClasses['fc-diff-hint']} */ ;
/** @type {__VLS_StyleScopedClasses['err']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            MarkdownContent: MarkdownContent,
            emit: emit,
            diffOpen: diffOpen,
            diffLoading: diffLoading,
            diffError: diffError,
            toggleDiff: toggleDiff,
            loadStepDiff: loadStepDiff,
            showStepPicker: showStepPicker,
            stepLabels: stepLabels,
            splitDiff: splitDiff,
            diffLineClass: diffLineClass,
            copied: copied,
            copyPlanPath: copyPlanPath,
            showFinalAnswer: showFinalAnswer,
            getStatusIcon: getStatusIcon,
            formatDuration: formatDuration,
            stepTitle: stepTitle,
            cardLabel: cardLabel,
            orderedParts: orderedParts,
            statusLabel: statusLabel,
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
