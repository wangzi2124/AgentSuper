/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { computed, onMounted, ref } from 'vue';
import { useCustomToolsStore } from '../stores/customTools';
const store = useCustomToolsStore();
const searchText = ref('');
const filteredItems = computed(() => {
    const q = searchText.value.toLowerCase().trim();
    if (!q)
        return store.items;
    return store.items.filter((item) => item.name.toLowerCase().includes(q) || (item.description && item.description.toLowerCase().includes(q)));
});
onMounted(async () => {
    // 两个接口互不依赖，并行发起（原先串行 await = 2×RTT 才渲染完）
    const [listRes, catalogRes] = await Promise.allSettled([
        store.fetchAll(),
        store.fetchCatalog(),
    ]);
    if (catalogRes.status === 'rejected') {
        console.warn('加载工具目录失败:', catalogRes.reason?.message || catalogRes.reason);
    }
    if (listRes.status === 'rejected') {
        throw listRes.reason;
    }
});
const scriptForm = ref({ name: '', description: '', script: '', enabled: true });
const showScriptForm = ref(false);
const creating = ref(false);
const scriptError = ref('');
async function handleCreateScript() {
    scriptError.value = '';
    if (!scriptForm.value.name.trim() || !scriptForm.value.script.trim()) {
        scriptError.value = '请填写工具名与 Python 源码（需包含 tool_* 函数）';
        return;
    }
    creating.value = true;
    try {
        await store.createScript({
            name: scriptForm.value.name.trim(),
            description: scriptForm.value.description.trim(),
            script: scriptForm.value.script,
            enabled: scriptForm.value.enabled,
        });
        scriptForm.value = { name: '', description: '', script: '', enabled: true };
        showScriptForm.value = false;
    }
    catch (err) {
        scriptError.value = err?.message || '创建失败';
    }
    finally {
        creating.value = false;
    }
}
const pinForm = ref({ tool_name: '', description: '' });
const pinError = ref('');
async function handlePin() {
    pinError.value = '';
    if (!pinForm.value.tool_name) {
        pinError.value = '请选择要固定的工具';
        return;
    }
    try {
        await store.pin({
            tool_name: pinForm.value.tool_name,
            description: pinForm.value.description.trim(),
        });
        pinForm.value = { tool_name: '', description: '' };
    }
    catch (err) {
        pinError.value = err?.message || '固定失败';
    }
}
async function handleToggle(item, enabled) {
    try {
        await store.toggle(item.name, !enabled);
    }
    catch (err) {
        alert(err.message);
    }
}
async function handleDelete(item) {
    if (!confirm(`确认删除自定义工具「${item.name}」？`))
        return;
    try {
        await store.remove(item.name);
    }
    catch (err) {
        alert(err.message);
    }
}
const pinnedNames = () => new Set(store.items.filter((i) => i.type === 'pin').map((i) => i.name));
const pinCandidates = () => {
    const q = searchText.value.toLowerCase().trim();
    const candidates = store.catalog.filter((t) => !pinnedNames().has(t.name));
    if (!q)
        return candidates;
    return candidates.filter((t) => t.name.toLowerCase().includes(q) || (t.description && t.description.toLowerCase().includes(q)));
};
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['cross-link']} */ ;
/** @type {__VLS_StyleScopedClasses['cross-link']} */ ;
/** @type {__VLS_StyleScopedClasses['pin-group']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-card']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-sub']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-card']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-actions']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h2, __VLS_intrinsicElements.h2)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
    ...{ class: "cross-link" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.code, __VLS_intrinsicElements.code)({});
const __VLS_0 = {}.RouterLink;
/** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.RouterLink, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    to: "/skills",
}));
const __VLS_2 = __VLS_1({
    to: "/skills",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_3.slots.default;
var __VLS_3;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "page-content tools-wrap" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "card create-bar" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.showScriptForm = !__VLS_ctx.showScriptForm;
        } },
    ...{ class: "btn btn-primary" },
});
(__VLS_ctx.showScriptForm ? '收起' : '+');
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "pin-group" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    placeholder: "搜索工具名称或描述...",
    ...{ class: "ctrl" },
});
(__VLS_ctx.searchText);
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    value: (__VLS_ctx.pinForm.tool_name),
    ...{ class: "ctrl" },
    title: "选择已有工具固定",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
    value: "",
    disabled: true,
});
for (const [t] of __VLS_getVForSourceType((__VLS_ctx.pinCandidates()))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        key: (t.name),
        value: (t.name),
        title: (t.name + ' — ' + (t.description || '无描述')),
    });
    (t.name.length > 12 ? t.name.slice(0, 12) + '…' : t.name);
    (t.description ? ' — ' + (t.description.length > 16 ? t.description.slice(0, 16) + '…' : t.description) : '');
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    placeholder: "固定备注（可选）",
    ...{ class: "ctrl desc-input" },
});
(__VLS_ctx.pinForm.description);
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.handlePin) },
    ...{ class: "btn btn-primary" },
});
if (__VLS_ctx.showScriptForm) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "card script-form" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-row" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        placeholder: "工具名（如 weather_plus）",
        ...{ class: "ctrl" },
    });
    (__VLS_ctx.scriptForm.name);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        placeholder: "描述（可选）",
        ...{ class: "ctrl" },
    });
    (__VLS_ctx.scriptForm.description);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.textarea, __VLS_intrinsicElements.textarea)({
        value: (__VLS_ctx.scriptForm.script),
        placeholder: "粘贴 Python 源码（需包含 tool_* 函数）&#10;def tool_echo(msg: str) -> str:&#10;    return 'echo: ' + msg",
        rows: "10",
        ...{ class: "ctrl code-area" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "form-footer" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "check-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        type: "checkbox",
    });
    (__VLS_ctx.scriptForm.enabled);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.handleCreateScript) },
        ...{ class: "btn btn-primary" },
        disabled: (__VLS_ctx.creating),
    });
    (__VLS_ctx.creating ? '创建中…' : '创建并热加载');
    if (__VLS_ctx.scriptError) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "form-error" },
        });
        (__VLS_ctx.scriptError);
    }
}
if (__VLS_ctx.pinError) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "form-error" },
    });
    (__VLS_ctx.pinError);
}
if (__VLS_ctx.store.loading) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "loading-wrap" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "spinner" },
    });
}
else if (__VLS_ctx.store.items.length === 0) {
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
        ...{ class: "tool-list" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "list-meta" },
    });
    (__VLS_ctx.filteredItems.length);
    for (const [item] of __VLS_getVForSourceType((__VLS_ctx.filteredItems))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (item.type + ':' + item.name),
            ...{ class: "card tool-card" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "tool-icon" },
        });
        (item.type === 'script' ? '🧩' : '📌');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "tool-info" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "tool-title" },
        });
        (item.name);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "badge type-badge" },
        });
        (item.type === 'script' ? 'script' : 'pin');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "tool-desc" },
        });
        (item.description);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "tool-sub" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
        (item.tools.join(', '));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "path" },
        });
        (item.path);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "badge" },
            ...{ class: (item.enabled ? 'badge-enabled' : 'badge-disabled') },
        });
        (item.enabled ? '已启用' : '已禁用');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "tool-actions" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.store.loading))
                        return;
                    if (!!(__VLS_ctx.store.items.length === 0))
                        return;
                    __VLS_ctx.handleToggle(item, item.enabled);
                } },
            ...{ class: "btn btn-sm" },
            ...{ class: (item.enabled ? 'btn-danger' : 'btn-primary') },
        });
        (item.enabled ? '禁用' : '启用');
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!!(__VLS_ctx.store.loading))
                        return;
                    if (!!(__VLS_ctx.store.items.length === 0))
                        return;
                    __VLS_ctx.handleDelete(item);
                } },
            ...{ class: "btn btn-sm btn-danger" },
        });
    }
}
/** @type {__VLS_StyleScopedClasses['page-header']} */ ;
/** @type {__VLS_StyleScopedClasses['cross-link']} */ ;
/** @type {__VLS_StyleScopedClasses['page-content']} */ ;
/** @type {__VLS_StyleScopedClasses['tools-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['create-bar']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['pin-group']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['desc-input']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['script-form']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['ctrl']} */ ;
/** @type {__VLS_StyleScopedClasses['code-area']} */ ;
/** @type {__VLS_StyleScopedClasses['form-footer']} */ ;
/** @type {__VLS_StyleScopedClasses['check-label']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['form-error']} */ ;
/** @type {__VLS_StyleScopedClasses['form-error']} */ ;
/** @type {__VLS_StyleScopedClasses['loading-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['spinner']} */ ;
/** @type {__VLS_StyleScopedClasses['empty-state']} */ ;
/** @type {__VLS_StyleScopedClasses['icon']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-list']} */ ;
/** @type {__VLS_StyleScopedClasses['list-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['card']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-card']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-info']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-title']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['type-badge']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-desc']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-sub']} */ ;
/** @type {__VLS_StyleScopedClasses['path']} */ ;
/** @type {__VLS_StyleScopedClasses['badge']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-sm']} */ ;
/** @type {__VLS_StyleScopedClasses['btn-danger']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            store: store,
            searchText: searchText,
            filteredItems: filteredItems,
            scriptForm: scriptForm,
            showScriptForm: showScriptForm,
            creating: creating,
            scriptError: scriptError,
            handleCreateScript: handleCreateScript,
            pinForm: pinForm,
            pinError: pinError,
            handlePin: handlePin,
            handleToggle: handleToggle,
            handleDelete: handleDelete,
            pinCandidates: pinCandidates,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
