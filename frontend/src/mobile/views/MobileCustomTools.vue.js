/// <reference types="../../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { computed, onMounted, ref } from 'vue';
import { useCustomToolsStore } from '../../stores/customTools';
import { useSkillStore } from '../../stores/skills';
import { showConfirmDialog, showToast } from 'vant';
const store = useCustomToolsStore();
const skillStore = useSkillStore();
const searchText = ref('');
const filteredItems = computed(() => {
    const q = searchText.value.toLowerCase().trim();
    if (!q)
        return store.items;
    return store.items.filter((item) => item.name.toLowerCase().includes(q) || (item.description && item.description.toLowerCase().includes(q)));
});
onMounted(async () => {
    await store.fetchAll();
    try {
        await store.fetchCatalog();
    }
    catch (e) {
        console.warn('加载工具目录失败:', e?.message || e);
    }
    try {
        await skillStore.fetchAll();
    }
    catch (e) {
        console.warn('加载技能计数失败:', e?.message || e);
    }
});
// ── 创建脚本型 ──
const showScriptForm = ref(false);
const creating = ref(false);
const scriptForm = ref({ name: '', description: '', script: '', enabled: true });
async function handleCreateScript() {
    if (!scriptForm.value.name.trim() || !scriptForm.value.script.trim()) {
        showToast('请填写工具名与 Python 源码（需包含 tool_* 函数）');
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
        showToast('创建成功');
    }
    catch (err) {
        showToast(err?.message || '创建失败');
    }
    finally {
        creating.value = false;
    }
}
// ── 固定型 ──
const showPinForm = ref(false);
const pinForm = ref({ tool_name: '', description: '' });
const pinnedNames = computed(() => new Set(store.items.filter((i) => i.type === 'pin').map((i) => i.name)));
const pinCandidates = computed(() => store.catalog.filter((t) => !pinnedNames.value.has(t.name)));
async function handlePin() {
    if (!pinForm.value.tool_name) {
        showToast('请选择要固定的工具');
        return;
    }
    try {
        await store.pin({ tool_name: pinForm.value.tool_name, description: pinForm.value.description.trim() });
        pinForm.value = { tool_name: '', description: '' };
        showPinForm.value = false;
        showToast('已固定');
    }
    catch (err) {
        showToast(err?.message || '固定失败');
    }
}
// ── 切换 / 删除 ──
async function handleToggle(item, enabled) {
    try {
        await store.toggle(item.name, !enabled);
    }
    catch (err) {
        showToast(err.message);
    }
}
async function handleDelete(item) {
    try {
        await showConfirmDialog({ title: '删除工具', message: `确认删除自定义工具「${item.name}」？` });
        await store.remove(item.name);
        showToast('已删除');
    }
    catch (err) {
        if (err?.message && !String(err.message).includes('cancel'))
            showToast(err.message);
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['form-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['pin-options']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['cta']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['cta']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-list']} */ ;
/** @type {__VLS_StyleScopedClasses['count']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['van-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['van-cell__value']} */ ;
/** @type {__VLS_StyleScopedClasses['van-switch']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['t-script']} */ ;
/** @type {__VLS_StyleScopedClasses['van-cell__title']} */ ;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['t-pin']} */ ;
/** @type {__VLS_StyleScopedClasses['van-cell__title']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-del']} */ ;
/** @type {__VLS_StyleScopedClasses['form-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['form-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['pin-options']} */ ;
/** @type {__VLS_StyleScopedClasses['van-cell']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['pin-options']} */ ;
/** @type {__VLS_StyleScopedClasses['van-cell']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-tools" },
});
const __VLS_0 = {}.VanSearch;
/** @type {[typeof __VLS_components.VanSearch, typeof __VLS_components.vanSearch, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    modelValue: (__VLS_ctx.searchText),
    placeholder: "搜索工具名称或描述...",
}));
const __VLS_2 = __VLS_1({
    modelValue: (__VLS_ctx.searchText),
    placeholder: "搜索工具名称或描述...",
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "cta" },
});
const __VLS_4 = {}.VanButton;
/** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
// @ts-ignore
const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
    ...{ 'onClick': {} },
    size: "small",
    plain: true,
    type: "primary",
    icon: "plus",
}));
const __VLS_6 = __VLS_5({
    ...{ 'onClick': {} },
    size: "small",
    plain: true,
    type: "primary",
    icon: "plus",
}, ...__VLS_functionalComponentArgsRest(__VLS_5));
let __VLS_8;
let __VLS_9;
let __VLS_10;
const __VLS_11 = {
    onClick: (...[$event]) => {
        __VLS_ctx.showScriptForm = true;
    }
};
__VLS_7.slots.default;
var __VLS_7;
const __VLS_12 = {}.VanButton;
/** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
// @ts-ignore
const __VLS_13 = __VLS_asFunctionalComponent(__VLS_12, new __VLS_12({
    ...{ 'onClick': {} },
    size: "small",
    plain: true,
    type: "primary",
    icon: "bookmark-o",
}));
const __VLS_14 = __VLS_13({
    ...{ 'onClick': {} },
    size: "small",
    plain: true,
    type: "primary",
    icon: "bookmark-o",
}, ...__VLS_functionalComponentArgsRest(__VLS_13));
let __VLS_16;
let __VLS_17;
let __VLS_18;
const __VLS_19 = {
    onClick: (...[$event]) => {
        __VLS_ctx.showPinForm = true;
    }
};
__VLS_15.slots.default;
var __VLS_15;
if (__VLS_ctx.store.loading) {
    const __VLS_20 = {}.VanLoading;
    /** @type {[typeof __VLS_components.VanLoading, typeof __VLS_components.vanLoading, ]} */ ;
    // @ts-ignore
    const __VLS_21 = __VLS_asFunctionalComponent(__VLS_20, new __VLS_20({
        ...{ class: "loading" },
    }));
    const __VLS_22 = __VLS_21({
        ...{ class: "loading" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_21));
}
else if (__VLS_ctx.store.items.length === 0) {
    const __VLS_24 = {}.VanEmpty;
    /** @type {[typeof __VLS_components.VanEmpty, typeof __VLS_components.vanEmpty, ]} */ ;
    // @ts-ignore
    const __VLS_25 = __VLS_asFunctionalComponent(__VLS_24, new __VLS_24({
        image: "search",
        description: "暂无自定义工具",
    }));
    const __VLS_26 = __VLS_25({
        image: "search",
        description: "暂无自定义工具",
    }, ...__VLS_functionalComponentArgsRest(__VLS_25));
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "tool-list" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "count" },
    });
    (__VLS_ctx.filteredItems.length);
    for (const [item] of __VLS_getVForSourceType((__VLS_ctx.filteredItems))) {
        const __VLS_28 = {}.VanSwipeCell;
        /** @type {[typeof __VLS_components.VanSwipeCell, typeof __VLS_components.vanSwipeCell, typeof __VLS_components.VanSwipeCell, typeof __VLS_components.vanSwipeCell, ]} */ ;
        // @ts-ignore
        const __VLS_29 = __VLS_asFunctionalComponent(__VLS_28, new __VLS_28({
            key: (item.type + ':' + item.name),
        }));
        const __VLS_30 = __VLS_29({
            key: (item.type + ':' + item.name),
        }, ...__VLS_functionalComponentArgsRest(__VLS_29));
        __VLS_31.slots.default;
        const __VLS_32 = {}.VanCell;
        /** @type {[typeof __VLS_components.VanCell, typeof __VLS_components.vanCell, typeof __VLS_components.VanCell, typeof __VLS_components.vanCell, ]} */ ;
        // @ts-ignore
        const __VLS_33 = __VLS_asFunctionalComponent(__VLS_32, new __VLS_32({
            ...{ class: ('t-' + item.type) },
            title: (item.name),
            label: (`${item.type === 'script' ? '🧩' : '📌'} ${item.description || ''} · ${item.tools.join(', ')}`),
            center: true,
        }));
        const __VLS_34 = __VLS_33({
            ...{ class: ('t-' + item.type) },
            title: (item.name),
            label: (`${item.type === 'script' ? '🧩' : '📌'} ${item.description || ''} · ${item.tools.join(', ')}`),
            center: true,
        }, ...__VLS_functionalComponentArgsRest(__VLS_33));
        __VLS_35.slots.default;
        {
            const { 'right-icon': __VLS_thisSlot } = __VLS_35.slots;
            const __VLS_36 = {}.VanSwitch;
            /** @type {[typeof __VLS_components.VanSwitch, typeof __VLS_components.vanSwitch, ]} */ ;
            // @ts-ignore
            const __VLS_37 = __VLS_asFunctionalComponent(__VLS_36, new __VLS_36({
                ...{ 'onUpdate:modelValue': {} },
                modelValue: (item.enabled),
                size: "20px",
            }));
            const __VLS_38 = __VLS_37({
                ...{ 'onUpdate:modelValue': {} },
                modelValue: (item.enabled),
                size: "20px",
            }, ...__VLS_functionalComponentArgsRest(__VLS_37));
            let __VLS_40;
            let __VLS_41;
            let __VLS_42;
            const __VLS_43 = {
                'onUpdate:modelValue': (...[$event]) => {
                    if (!!(__VLS_ctx.store.loading))
                        return;
                    if (!!(__VLS_ctx.store.items.length === 0))
                        return;
                    __VLS_ctx.handleToggle(item, item.enabled);
                }
            };
            var __VLS_39;
        }
        var __VLS_35;
        {
            const { right: __VLS_thisSlot } = __VLS_31.slots;
            __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
                ...{ onClick: (...[$event]) => {
                        if (!!(__VLS_ctx.store.loading))
                            return;
                        if (!!(__VLS_ctx.store.items.length === 0))
                            return;
                        __VLS_ctx.handleDelete(item);
                    } },
                ...{ class: "swipe-del" },
            });
        }
        var __VLS_31;
    }
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "skills-m" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "skills-m-title" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "skills-m-dir" },
});
(__VLS_ctx.skillStore.skills.length);
const __VLS_44 = {}.VanButton;
/** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
// @ts-ignore
const __VLS_45 = __VLS_asFunctionalComponent(__VLS_44, new __VLS_44({
    size: "small",
    plain: true,
    type: "primary",
    icon: "fire-o",
    to: "/skills",
}));
const __VLS_46 = __VLS_45({
    size: "small",
    plain: true,
    type: "primary",
    icon: "fire-o",
    to: "/skills",
}, ...__VLS_functionalComponentArgsRest(__VLS_45));
__VLS_47.slots.default;
var __VLS_47;
const __VLS_48 = {}.VanPopup;
/** @type {[typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, ]} */ ;
// @ts-ignore
const __VLS_49 = __VLS_asFunctionalComponent(__VLS_48, new __VLS_48({
    show: (__VLS_ctx.showScriptForm),
    round: true,
    position: "bottom",
    ...{ style: ({ height: '80%' }) },
}));
const __VLS_50 = __VLS_49({
    show: (__VLS_ctx.showScriptForm),
    round: true,
    position: "bottom",
    ...{ style: ({ height: '80%' }) },
}, ...__VLS_functionalComponentArgsRest(__VLS_49));
__VLS_51.slots.default;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-panel" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-title" },
});
const __VLS_52 = {}.VanField;
/** @type {[typeof __VLS_components.VanField, typeof __VLS_components.vanField, ]} */ ;
// @ts-ignore
const __VLS_53 = __VLS_asFunctionalComponent(__VLS_52, new __VLS_52({
    modelValue: (__VLS_ctx.scriptForm.name),
    label: "工具名",
    placeholder: "如 weather_plus",
}));
const __VLS_54 = __VLS_53({
    modelValue: (__VLS_ctx.scriptForm.name),
    label: "工具名",
    placeholder: "如 weather_plus",
}, ...__VLS_functionalComponentArgsRest(__VLS_53));
const __VLS_56 = {}.VanField;
/** @type {[typeof __VLS_components.VanField, typeof __VLS_components.vanField, ]} */ ;
// @ts-ignore
const __VLS_57 = __VLS_asFunctionalComponent(__VLS_56, new __VLS_56({
    modelValue: (__VLS_ctx.scriptForm.description),
    label: "描述",
    placeholder: "可选",
}));
const __VLS_58 = __VLS_57({
    modelValue: (__VLS_ctx.scriptForm.description),
    label: "描述",
    placeholder: "可选",
}, ...__VLS_functionalComponentArgsRest(__VLS_57));
const __VLS_60 = {}.VanField;
/** @type {[typeof __VLS_components.VanField, typeof __VLS_components.vanField, ]} */ ;
// @ts-ignore
const __VLS_61 = __VLS_asFunctionalComponent(__VLS_60, new __VLS_60({
    modelValue: (__VLS_ctx.scriptForm.script),
    label: "源码",
    type: "textarea",
    rows: "8",
    autosize: true,
    placeholder: "粘贴 Python 源码（需包含 tool_* 函数）",
}));
const __VLS_62 = __VLS_61({
    modelValue: (__VLS_ctx.scriptForm.script),
    label: "源码",
    type: "textarea",
    rows: "8",
    autosize: true,
    placeholder: "粘贴 Python 源码（需包含 tool_* 函数）",
}, ...__VLS_functionalComponentArgsRest(__VLS_61));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-row" },
});
const __VLS_64 = {}.VanSwitch;
/** @type {[typeof __VLS_components.VanSwitch, typeof __VLS_components.vanSwitch, ]} */ ;
// @ts-ignore
const __VLS_65 = __VLS_asFunctionalComponent(__VLS_64, new __VLS_64({
    modelValue: (__VLS_ctx.scriptForm.enabled),
    size: "20px",
}));
const __VLS_66 = __VLS_65({
    modelValue: (__VLS_ctx.scriptForm.enabled),
    size: "20px",
}, ...__VLS_functionalComponentArgsRest(__VLS_65));
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "row-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ style: {} },
});
const __VLS_68 = {}.VanButton;
/** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
// @ts-ignore
const __VLS_69 = __VLS_asFunctionalComponent(__VLS_68, new __VLS_68({
    ...{ 'onClick': {} },
    type: "primary",
    size: "small",
    loading: (__VLS_ctx.creating),
}));
const __VLS_70 = __VLS_69({
    ...{ 'onClick': {} },
    type: "primary",
    size: "small",
    loading: (__VLS_ctx.creating),
}, ...__VLS_functionalComponentArgsRest(__VLS_69));
let __VLS_72;
let __VLS_73;
let __VLS_74;
const __VLS_75 = {
    onClick: (__VLS_ctx.handleCreateScript)
};
__VLS_71.slots.default;
var __VLS_71;
var __VLS_51;
const __VLS_76 = {}.VanPopup;
/** @type {[typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, ]} */ ;
// @ts-ignore
const __VLS_77 = __VLS_asFunctionalComponent(__VLS_76, new __VLS_76({
    show: (__VLS_ctx.showPinForm),
    round: true,
    position: "bottom",
    ...{ style: ({ height: '70%' }) },
}));
const __VLS_78 = __VLS_77({
    show: (__VLS_ctx.showPinForm),
    round: true,
    position: "bottom",
    ...{ style: ({ height: '70%' }) },
}, ...__VLS_functionalComponentArgsRest(__VLS_77));
__VLS_79.slots.default;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-panel" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-title" },
});
const __VLS_80 = {}.VanField;
/** @type {[typeof __VLS_components.VanField, typeof __VLS_components.vanField, ]} */ ;
// @ts-ignore
const __VLS_81 = __VLS_asFunctionalComponent(__VLS_80, new __VLS_80({
    modelValue: (__VLS_ctx.pinForm.description),
    label: "备注",
    placeholder: "选填",
}));
const __VLS_82 = __VLS_81({
    modelValue: (__VLS_ctx.pinForm.description),
    label: "备注",
    placeholder: "选填",
}, ...__VLS_functionalComponentArgsRest(__VLS_81));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "pin-options" },
});
for (const [t] of __VLS_getVForSourceType((__VLS_ctx.pinCandidates))) {
    const __VLS_84 = {}.VanCell;
    /** @type {[typeof __VLS_components.VanCell, typeof __VLS_components.vanCell, ]} */ ;
    // @ts-ignore
    const __VLS_85 = __VLS_asFunctionalComponent(__VLS_84, new __VLS_84({
        ...{ 'onClick': {} },
        key: (t.name),
        title: (t.name),
        label: (t.description || ''),
        clickable: true,
        ...{ class: ({ active: __VLS_ctx.pinForm.tool_name === t.name }) },
    }));
    const __VLS_86 = __VLS_85({
        ...{ 'onClick': {} },
        key: (t.name),
        title: (t.name),
        label: (t.description || ''),
        clickable: true,
        ...{ class: ({ active: __VLS_ctx.pinForm.tool_name === t.name }) },
    }, ...__VLS_functionalComponentArgsRest(__VLS_85));
    let __VLS_88;
    let __VLS_89;
    let __VLS_90;
    const __VLS_91 = {
        onClick: (...[$event]) => {
            __VLS_ctx.pinForm.tool_name = t.name;
        }
    };
    var __VLS_87;
}
if (__VLS_ctx.pinCandidates.length === 0) {
    const __VLS_92 = {}.VanEmpty;
    /** @type {[typeof __VLS_components.VanEmpty, typeof __VLS_components.vanEmpty, ]} */ ;
    // @ts-ignore
    const __VLS_93 = __VLS_asFunctionalComponent(__VLS_92, new __VLS_92({
        description: "没有可固定的工具",
    }));
    const __VLS_94 = __VLS_93({
        description: "没有可固定的工具",
    }, ...__VLS_functionalComponentArgsRest(__VLS_93));
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ style: {} },
});
const __VLS_96 = {}.VanButton;
/** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
// @ts-ignore
const __VLS_97 = __VLS_asFunctionalComponent(__VLS_96, new __VLS_96({
    ...{ 'onClick': {} },
    type: "primary",
    size: "small",
}));
const __VLS_98 = __VLS_97({
    ...{ 'onClick': {} },
    type: "primary",
    size: "small",
}, ...__VLS_functionalComponentArgsRest(__VLS_97));
let __VLS_100;
let __VLS_101;
let __VLS_102;
const __VLS_103 = {
    onClick: (__VLS_ctx.handlePin)
};
__VLS_99.slots.default;
var __VLS_99;
var __VLS_79;
/** @type {__VLS_StyleScopedClasses['m-tools']} */ ;
/** @type {__VLS_StyleScopedClasses['cta']} */ ;
/** @type {__VLS_StyleScopedClasses['loading']} */ ;
/** @type {__VLS_StyleScopedClasses['tool-list']} */ ;
/** @type {__VLS_StyleScopedClasses['count']} */ ;
/** @type {__VLS_StyleScopedClasses['swipe-del']} */ ;
/** @type {__VLS_StyleScopedClasses['skills-m']} */ ;
/** @type {__VLS_StyleScopedClasses['skills-m-title']} */ ;
/** @type {__VLS_StyleScopedClasses['skills-m-dir']} */ ;
/** @type {__VLS_StyleScopedClasses['form-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['row-label']} */ ;
/** @type {__VLS_StyleScopedClasses['form-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['pin-options']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            store: store,
            skillStore: skillStore,
            searchText: searchText,
            filteredItems: filteredItems,
            showScriptForm: showScriptForm,
            creating: creating,
            scriptForm: scriptForm,
            handleCreateScript: handleCreateScript,
            showPinForm: showPinForm,
            pinForm: pinForm,
            pinCandidates: pinCandidates,
            handlePin: handlePin,
            handleToggle: handleToggle,
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
