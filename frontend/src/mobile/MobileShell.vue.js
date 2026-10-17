/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, computed, defineAsyncComponent } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { showConfirmDialog, showToast } from 'vant';
import { useVectorStore } from '../stores/vectors';
// 7 个移动页改为异步组件：此前静态 import 会把全部移动页（且 MobileChat 静态引入
// 完整 MultiAgentView + ChatInput）打进 MobileShell chunk，导致进入外壳就要下载
// 所有页面代码。改为 defineAsyncComponent 后只有当前路由对应的页面才会被拉取。
const MobileChat = defineAsyncComponent(() => import('./views/MobileChat.vue'));
const MobileDocuments = defineAsyncComponent(() => import('./views/MobileDocuments.vue'));
const MobilePlugins = defineAsyncComponent(() => import('./views/MobilePlugins.vue'));
const MobileVectors = defineAsyncComponent(() => import('./views/MobileVectors.vue'));
const MobileGenerated = defineAsyncComponent(() => import('./views/MobileGenerated.vue'));
const MobileMonitoring = defineAsyncComponent(() => import('./views/MobileMonitoring.vue'));
const MobileCustomTools = defineAsyncComponent(() => import('./views/MobileCustomTools.vue'));
const route = useRoute();
const router = useRouter();
// 底部 TabBar 主入口（最多 5 个）
const tabs = [
    { name: 'MultiAgent', path: '/multi-agent', title: '聊天', icon: 'chat-o' },
    { name: 'Documents', path: '/documents', title: '文档', icon: 'description' },
    { name: 'Plugins', path: '/plugins', title: '插件', icon: 'apps-o' },
    { name: 'Vectors', path: '/vectors', title: '向量库', icon: 'cluster-o' },
];
// 全站导航（用于顶部菜单抽屉）：彩色语义图标体系（color = 图标色，soft = 容器底色）
const menu = [
    { name: 'MultiAgent', path: '/multi-agent', title: '多智能体', vanIcon: 'chat-o', color: '#6d5ef1', soft: 'rgba(109,94,241,.12)' },
    { name: 'Documents', path: '/documents', title: '文档管理', vanIcon: 'description', color: '#3b82f6', soft: 'rgba(59,130,246,.12)' },
    { name: 'Plugins', path: '/plugins', title: '插件', vanIcon: 'apps-o', color: '#06b6d4', soft: 'rgba(6,182,212,.12)' },
    { name: 'CustomTools', path: '/custom-tools', title: '自定义工具', vanIcon: 'setting-o', color: '#ec4899', soft: 'rgba(236,72,153,.12)' },
    { name: 'Skills', path: '/skills', title: '技能', vanIcon: 'fire-o', color: '#eab308', soft: 'rgba(234,179,8,.12)' },
    { name: 'Vectors', path: '/vectors', title: '向量库', vanIcon: 'cluster-o', color: '#10b981', soft: 'rgba(16,185,129,.12)' },
    { name: 'Generated', path: '/generated', title: '生成文件', vanIcon: 'file-o', color: '#f97316', soft: 'rgba(249,115,22,.12)' },
    { name: 'Monitoring', path: '/monitoring', title: '系统监控', vanIcon: 'chart-trending-o', color: '#f59e0b', soft: 'rgba(245,158,11,.12)' },
    { name: 'Logs', path: '/logs', title: '日志管理', vanIcon: 'records', color: '#0ea5e9', soft: 'rgba(14,165,233,.12)' },
];
// 当前激活的 TabBar 项
const activeTab = computed(() => tabs.find(t => route.path.startsWith(t.path))?.name || '');
// 当前页标题
const currentTitle = computed(() => menu.find(m => route.path.startsWith(m.path))?.title || '');
// 顶部菜单抽屉
const showMenu = ref(false);
function go(path) {
    showMenu.value = false;
    router.push(path);
}
// 移动版页面分发：按 route.name 返回对应移动组件，未覆盖的页面 fallback 到原视图
const mobileViews = {
    MultiAgent: MobileChat,
    MultiAgentConversation: MobileChat,
    Documents: MobileDocuments,
    Plugins: MobilePlugins,
    Vectors: MobileVectors,
    Generated: MobileGenerated,
    Monitoring: MobileMonitoring,
    CustomTools: MobileCustomTools,
};
/* @@CHAT_PANEL_SCRIPT@@ */
// ── 设置分组（移动端聊天对齐 ChatGPT：头部控件收纳进左侧抽屉） ──
import { useMultiAgentStore } from '../stores/multiAgent';
import { usePermissionStore } from '../stores/permission';
import { useThemeStore, BG_VARIANTS } from '../stores/theme';
import { useChatSettingsStore, TTS_LANGUAGES } from '../stores/chatSettings';
const agent = useMultiAgentStore();
const perm = usePermissionStore();
const theme = useThemeStore();
const chatSettings = useChatSettingsStore();
// 工作目录摘要（过长省略展示）
const workspacesText = computed(() => {
    const list = perm.workspaces;
    if (!list.length)
        return '未授权，点击刷新';
    const shown = list.slice(0, 2).join(' · ');
    return list.length > 2 ? shown + ' 等' + list.length + ' 个' : shown;
});
// 清空会话：二次确认（3 秒未确认自动复位）
const clearConfirm = ref(false);
let clearTimer;
function onClearClick() {
    if (!clearConfirm.value) {
        clearConfirm.value = true;
        clearTimer = setTimeout(() => { clearConfirm.value = false; }, 3000);
        return;
    }
    clearConfirm.value = false;
    if (clearTimer)
        clearTimeout(clearTimer);
    agent.deleteConversation();
}
// 清空向量库（危险操作归入设置）
const vs = useVectorStore();
async function onClearVectorsClick() {
    try {
        await showConfirmDialog({
            title: '清空向量库',
            message: '将删除向量库全部分块、章节库记录、BM25 索引和全部上传文件，此操作不可撤销！',
        });
        await vs.clearAll();
        showToast('已清空向量库');
    }
    catch (err) {
        if (err?.message && !String(err.message).includes('cancel'))
            showToast(err.message || '清空失败');
    }
}
function refreshWorkspaces() {
    perm.loadWorkspaces();
}
/* @@CHAT_SETTINGS_FORM_SCRIPT@@ */
// ── 设置表单：模型 / 向量库 / 工作目录 / 清空会话（弹层表单） ──
const showSettingsForm = ref(false);
const showModelPicker = ref(false);
const formModel = ref(agent.selectedModel);
const formDirectory = ref(agent.sessionDirectory);
const modelColumns = computed(() => agent.modelOptions.map(m => ({ text: m.label, value: m.value })));
const formModelText = computed(() => agent.modelOptions.find(m => m.value === formModel.value)?.label || formModel.value);
function openSettings() {
    formModel.value = agent.selectedModel;
    formDirectory.value = agent.sessionDirectory;
    showSettingsForm.value = true;
}
function onModelConfirm(params) {
    const opt = params?.selectedOptions?.[0];
    if (opt && opt.value)
        formModel.value = opt.value;
    agent.selectedModel = formModel.value;
    agent.persistSelectedModel();
    showModelPicker.value = false;
}
// 朗读语言
const showLangPicker = ref(false);
const langColumns = computed(() => TTS_LANGUAGES.map(l => ({ text: l.label, value: l.value })));
function onLangConfirm(params) {
    const opt = params?.selectedOptions?.[0];
    if (opt && opt.value)
        chatSettings.ttsLang = opt.value;
    showLangPicker.value = false;
}
function saveSettings() {
    // 直接写 store 状态（不依赖 setSessionDirectory 方法签名）
    agent.sessionDirectory = formDirectory.value.trim();
    showSettingsForm.value = false;
}
const currentView = computed(() => mobileViews[route.name] || null);
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item']} */ ;
/** @type {__VLS_StyleScopedClasses['current']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item-title']} */ ;
/** @type {__VLS_StyleScopedClasses['tab-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['model-opt']} */ ;
/** @type {__VLS_StyleScopedClasses['current']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item-title']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-swatch']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-swatch']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-swatch-label']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['form-label']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "mobile-shell" },
});
const __VLS_0 = {}.VanNavBar;
/** @type {[typeof __VLS_components.VanNavBar, typeof __VLS_components.vanNavBar, typeof __VLS_components.VanNavBar, typeof __VLS_components.vanNavBar, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    ...{ 'onClickLeft': {} },
    title: (__VLS_ctx.currentTitle),
    fixed: true,
    placeholder: true,
    safeAreaInsetTop: true,
    leftArrow: true,
}));
const __VLS_2 = __VLS_1({
    ...{ 'onClickLeft': {} },
    title: (__VLS_ctx.currentTitle),
    fixed: true,
    placeholder: true,
    safeAreaInsetTop: true,
    leftArrow: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
let __VLS_4;
let __VLS_5;
let __VLS_6;
const __VLS_7 = {
    onClickLeft: (...[$event]) => {
        __VLS_ctx.showMenu = true;
    }
};
__VLS_3.slots.default;
{
    const { left: __VLS_thisSlot } = __VLS_3.slots;
    const __VLS_8 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent(__VLS_8, new __VLS_8({
        name: "wap-nav",
    }));
    const __VLS_10 = __VLS_9({
        name: "wap-nav",
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
}
var __VLS_3;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "mobile-body" },
});
if (__VLS_ctx.currentView) {
    const __VLS_12 = ((__VLS_ctx.currentView));
    // @ts-ignore
    const __VLS_13 = __VLS_asFunctionalComponent(__VLS_12, new __VLS_12({
        key: (__VLS_ctx.route.fullPath),
    }));
    const __VLS_14 = __VLS_13({
        key: (__VLS_ctx.route.fullPath),
    }, ...__VLS_functionalComponentArgsRest(__VLS_13));
}
else {
    const __VLS_16 = {}.RouterView;
    /** @type {[typeof __VLS_components.RouterView, typeof __VLS_components.routerView, ]} */ ;
    // @ts-ignore
    const __VLS_17 = __VLS_asFunctionalComponent(__VLS_16, new __VLS_16({}));
    const __VLS_18 = __VLS_17({}, ...__VLS_functionalComponentArgsRest(__VLS_17));
}
const __VLS_20 = {}.VanPopup;
/** @type {[typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, ]} */ ;
// @ts-ignore
const __VLS_21 = __VLS_asFunctionalComponent(__VLS_20, new __VLS_20({
    show: (__VLS_ctx.showMenu),
    position: "left",
    ...{ style: ({ width: '78%', height: '100%' }) },
}));
const __VLS_22 = __VLS_21({
    show: (__VLS_ctx.showMenu),
    position: "left",
    ...{ style: ({ width: '78%', height: '100%' }) },
}, ...__VLS_functionalComponentArgsRest(__VLS_21));
__VLS_23.slots.default;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "mobile-drawer" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-head" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-logo" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-head-text" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-title" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-sub" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-body" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-group-label" },
});
for (const [m] of __VLS_getVForSourceType((__VLS_ctx.menu))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.go(m.path);
            } },
        key: (m.name),
        ...{ class: "drawer-item" },
        ...{ class: ({ current: __VLS_ctx.route.path.startsWith(m.path) }) },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "drawer-item-ico" },
        ...{ style: ({ background: m.soft, color: m.color }) },
    });
    const __VLS_24 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_25 = __VLS_asFunctionalComponent(__VLS_24, new __VLS_24({
        name: (m.vanIcon),
    }));
    const __VLS_26 = __VLS_25({
        name: (m.vanIcon),
    }, ...__VLS_functionalComponentArgsRest(__VLS_25));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "drawer-item-title" },
    });
    (m.title);
    const __VLS_28 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_29 = __VLS_asFunctionalComponent(__VLS_28, new __VLS_28({
        name: "arrow",
        ...{ class: "drawer-item-arrow" },
    }));
    const __VLS_30 = __VLS_29({
        name: "arrow",
        ...{ class: "drawer-item-arrow" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_29));
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ onClick: (__VLS_ctx.openSettings) },
    ...{ class: "drawer-item settings-item" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-item-ico" },
    ...{ style: {} },
});
const __VLS_32 = {}.VanIcon;
/** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
// @ts-ignore
const __VLS_33 = __VLS_asFunctionalComponent(__VLS_32, new __VLS_32({
    name: "setting-o",
}));
const __VLS_34 = __VLS_33({
    name: "setting-o",
}, ...__VLS_functionalComponentArgsRest(__VLS_33));
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "drawer-item-title" },
});
const __VLS_36 = {}.VanIcon;
/** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
// @ts-ignore
const __VLS_37 = __VLS_asFunctionalComponent(__VLS_36, new __VLS_36({
    name: "arrow",
    ...{ class: "drawer-item-arrow" },
}));
const __VLS_38 = __VLS_37({
    name: "arrow",
    ...{ class: "drawer-item-arrow" },
}, ...__VLS_functionalComponentArgsRest(__VLS_37));
var __VLS_23;
const __VLS_40 = {}.VanPopup;
/** @type {[typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, ]} */ ;
// @ts-ignore
const __VLS_41 = __VLS_asFunctionalComponent(__VLS_40, new __VLS_40({
    show: (__VLS_ctx.showSettingsForm),
    position: "bottom",
    round: true,
    ...{ style: ({ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }) },
}));
const __VLS_42 = __VLS_41({
    show: (__VLS_ctx.showSettingsForm),
    position: "bottom",
    round: true,
    ...{ style: ({ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }) },
}, ...__VLS_functionalComponentArgsRest(__VLS_41));
__VLS_43.slots.default;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "settings-form" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "settings-form-head" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "settings-form-title" },
});
const __VLS_44 = {}.VanIcon;
/** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
// @ts-ignore
const __VLS_45 = __VLS_asFunctionalComponent(__VLS_44, new __VLS_44({
    ...{ 'onClick': {} },
    name: "cross",
    ...{ class: "settings-form-close" },
}));
const __VLS_46 = __VLS_45({
    ...{ 'onClick': {} },
    name: "cross",
    ...{ class: "settings-form-close" },
}, ...__VLS_functionalComponentArgsRest(__VLS_45));
let __VLS_48;
let __VLS_49;
let __VLS_50;
const __VLS_51 = {
    onClick: (...[$event]) => {
        __VLS_ctx.showSettingsForm = false;
    }
};
var __VLS_47;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "settings-form-body" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-field" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "bg-picker" },
});
for (const [v] of __VLS_getVForSourceType((__VLS_ctx.BG_VARIANTS))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.theme.setBg(v.value);
            } },
        key: (v.value),
        ...{ class: "bg-swatch" },
        ...{ class: ({ active: __VLS_ctx.theme.bgVariant === v.value }) },
        ...{ style: ({ '--sw': v.swatch, '--sw-glow': v.glowSwatch || v.swatch }) },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "bg-swatch-dot" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "bg-swatch-label" },
    });
    (v.label);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-field form-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-label" },
});
const __VLS_52 = {}.VanSwitch;
/** @type {[typeof __VLS_components.VanSwitch, typeof __VLS_components.vanSwitch, ]} */ ;
// @ts-ignore
const __VLS_53 = __VLS_asFunctionalComponent(__VLS_52, new __VLS_52({
    modelValue: (__VLS_ctx.chatSettings.autoRead),
    size: "22",
}));
const __VLS_54 = __VLS_53({
    modelValue: (__VLS_ctx.chatSettings.autoRead),
    size: "22",
}, ...__VLS_functionalComponentArgsRest(__VLS_53));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-field" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-label" },
});
const __VLS_56 = {}.VanField;
/** @type {[typeof __VLS_components.VanField, typeof __VLS_components.vanField, ]} */ ;
// @ts-ignore
const __VLS_57 = __VLS_asFunctionalComponent(__VLS_56, new __VLS_56({
    ...{ 'onClick': {} },
    modelValue: ((__VLS_ctx.TTS_LANGUAGES.find(l => l.value === __VLS_ctx.chatSettings.ttsLang)?.label) || __VLS_ctx.chatSettings.ttsLang),
    readonly: true,
    isLink: true,
    placeholder: "选择语言",
}));
const __VLS_58 = __VLS_57({
    ...{ 'onClick': {} },
    modelValue: ((__VLS_ctx.TTS_LANGUAGES.find(l => l.value === __VLS_ctx.chatSettings.ttsLang)?.label) || __VLS_ctx.chatSettings.ttsLang),
    readonly: true,
    isLink: true,
    placeholder: "选择语言",
}, ...__VLS_functionalComponentArgsRest(__VLS_57));
let __VLS_60;
let __VLS_61;
let __VLS_62;
const __VLS_63 = {
    onClick: (...[$event]) => {
        __VLS_ctx.showLangPicker = true;
    }
};
var __VLS_59;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-field" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-label" },
});
const __VLS_64 = {}.VanField;
/** @type {[typeof __VLS_components.VanField, typeof __VLS_components.vanField, ]} */ ;
// @ts-ignore
const __VLS_65 = __VLS_asFunctionalComponent(__VLS_64, new __VLS_64({
    ...{ 'onClick': {} },
    modelValue: (__VLS_ctx.formModelText),
    readonly: true,
    isLink: true,
    placeholder: "选择模型",
}));
const __VLS_66 = __VLS_65({
    ...{ 'onClick': {} },
    modelValue: (__VLS_ctx.formModelText),
    readonly: true,
    isLink: true,
    placeholder: "选择模型",
}, ...__VLS_functionalComponentArgsRest(__VLS_65));
let __VLS_68;
let __VLS_69;
let __VLS_70;
const __VLS_71 = {
    onClick: (...[$event]) => {
        __VLS_ctx.showModelPicker = true;
    }
};
var __VLS_67;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-field form-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-label" },
});
const __VLS_72 = {}.VanSwitch;
/** @type {[typeof __VLS_components.VanSwitch, typeof __VLS_components.vanSwitch, ]} */ ;
// @ts-ignore
const __VLS_73 = __VLS_asFunctionalComponent(__VLS_72, new __VLS_72({
    modelValue: (__VLS_ctx.agent.useVectorDb),
    size: "22",
}));
const __VLS_74 = __VLS_73({
    modelValue: (__VLS_ctx.agent.useVectorDb),
    size: "22",
}, ...__VLS_functionalComponentArgsRest(__VLS_73));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-field" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-label" },
});
const __VLS_76 = {}.VanField;
/** @type {[typeof __VLS_components.VanField, typeof __VLS_components.vanField, ]} */ ;
// @ts-ignore
const __VLS_77 = __VLS_asFunctionalComponent(__VLS_76, new __VLS_76({
    modelValue: (__VLS_ctx.formDirectory),
    placeholder: "\u5982\u0020\u0046\u003a\u005c\u005c\u0074\u0065\u0074\u0072\u0069\u0073",
    clearable: true,
}));
const __VLS_78 = __VLS_77({
    modelValue: (__VLS_ctx.formDirectory),
    placeholder: "\u5982\u0020\u0046\u003a\u005c\u005c\u0074\u0065\u0074\u0072\u0069\u0073",
    clearable: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_77));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "form-tip" },
});
const __VLS_80 = {}.VanButton;
/** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
// @ts-ignore
const __VLS_81 = __VLS_asFunctionalComponent(__VLS_80, new __VLS_80({
    ...{ 'onClick': {} },
    type: "primary",
    block: true,
    round: true,
    ...{ class: "form-save" },
}));
const __VLS_82 = __VLS_81({
    ...{ 'onClick': {} },
    type: "primary",
    block: true,
    round: true,
    ...{ class: "form-save" },
}, ...__VLS_functionalComponentArgsRest(__VLS_81));
let __VLS_84;
let __VLS_85;
let __VLS_86;
const __VLS_87 = {
    onClick: (__VLS_ctx.saveSettings)
};
__VLS_83.slots.default;
var __VLS_83;
const __VLS_88 = {}.VanButton;
/** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
// @ts-ignore
const __VLS_89 = __VLS_asFunctionalComponent(__VLS_88, new __VLS_88({
    ...{ 'onClick': {} },
    type: "danger",
    plain: true,
    block: true,
    round: true,
    ...{ class: "form-clear" },
}));
const __VLS_90 = __VLS_89({
    ...{ 'onClick': {} },
    type: "danger",
    plain: true,
    block: true,
    round: true,
    ...{ class: "form-clear" },
}, ...__VLS_functionalComponentArgsRest(__VLS_89));
let __VLS_92;
let __VLS_93;
let __VLS_94;
const __VLS_95 = {
    onClick: (__VLS_ctx.onClearClick)
};
__VLS_91.slots.default;
(__VLS_ctx.clearConfirm ? '再点一次确认清空' : '清空当前会话');
var __VLS_91;
const __VLS_96 = {}.VanButton;
/** @type {[typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, typeof __VLS_components.VanButton, typeof __VLS_components.vanButton, ]} */ ;
// @ts-ignore
const __VLS_97 = __VLS_asFunctionalComponent(__VLS_96, new __VLS_96({
    ...{ 'onClick': {} },
    type: "danger",
    plain: true,
    block: true,
    round: true,
    ...{ class: "form-clear form-clear-vectors" },
}));
const __VLS_98 = __VLS_97({
    ...{ 'onClick': {} },
    type: "danger",
    plain: true,
    block: true,
    round: true,
    ...{ class: "form-clear form-clear-vectors" },
}, ...__VLS_functionalComponentArgsRest(__VLS_97));
let __VLS_100;
let __VLS_101;
let __VLS_102;
const __VLS_103 = {
    onClick: (__VLS_ctx.onClearVectorsClick)
};
__VLS_99.slots.default;
var __VLS_99;
var __VLS_43;
const __VLS_104 = {}.VanPopup;
/** @type {[typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, ]} */ ;
// @ts-ignore
const __VLS_105 = __VLS_asFunctionalComponent(__VLS_104, new __VLS_104({
    show: (__VLS_ctx.showModelPicker),
    position: "bottom",
    round: true,
}));
const __VLS_106 = __VLS_105({
    show: (__VLS_ctx.showModelPicker),
    position: "bottom",
    round: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_105));
__VLS_107.slots.default;
const __VLS_108 = {}.VanPicker;
/** @type {[typeof __VLS_components.VanPicker, typeof __VLS_components.vanPicker, ]} */ ;
// @ts-ignore
const __VLS_109 = __VLS_asFunctionalComponent(__VLS_108, new __VLS_108({
    ...{ 'onConfirm': {} },
    ...{ 'onCancel': {} },
    columns: (__VLS_ctx.modelColumns),
    title: "选择模型",
}));
const __VLS_110 = __VLS_109({
    ...{ 'onConfirm': {} },
    ...{ 'onCancel': {} },
    columns: (__VLS_ctx.modelColumns),
    title: "选择模型",
}, ...__VLS_functionalComponentArgsRest(__VLS_109));
let __VLS_112;
let __VLS_113;
let __VLS_114;
const __VLS_115 = {
    onConfirm: (__VLS_ctx.onModelConfirm)
};
const __VLS_116 = {
    onCancel: (...[$event]) => {
        __VLS_ctx.showModelPicker = false;
    }
};
var __VLS_111;
var __VLS_107;
const __VLS_117 = {}.VanPopup;
/** @type {[typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, typeof __VLS_components.VanPopup, typeof __VLS_components.vanPopup, ]} */ ;
// @ts-ignore
const __VLS_118 = __VLS_asFunctionalComponent(__VLS_117, new __VLS_117({
    show: (__VLS_ctx.showLangPicker),
    position: "bottom",
    round: true,
}));
const __VLS_119 = __VLS_118({
    show: (__VLS_ctx.showLangPicker),
    position: "bottom",
    round: true,
}, ...__VLS_functionalComponentArgsRest(__VLS_118));
__VLS_120.slots.default;
const __VLS_121 = {}.VanPicker;
/** @type {[typeof __VLS_components.VanPicker, typeof __VLS_components.vanPicker, ]} */ ;
// @ts-ignore
const __VLS_122 = __VLS_asFunctionalComponent(__VLS_121, new __VLS_121({
    ...{ 'onConfirm': {} },
    ...{ 'onCancel': {} },
    columns: (__VLS_ctx.langColumns),
    title: "选择朗读语言",
}));
const __VLS_123 = __VLS_122({
    ...{ 'onConfirm': {} },
    ...{ 'onCancel': {} },
    columns: (__VLS_ctx.langColumns),
    title: "选择朗读语言",
}, ...__VLS_functionalComponentArgsRest(__VLS_122));
let __VLS_125;
let __VLS_126;
let __VLS_127;
const __VLS_128 = {
    onConfirm: (__VLS_ctx.onLangConfirm)
};
const __VLS_129 = {
    onCancel: (...[$event]) => {
        __VLS_ctx.showLangPicker = false;
    }
};
var __VLS_124;
var __VLS_120;
/** @type {__VLS_StyleScopedClasses['mobile-shell']} */ ;
/** @type {__VLS_StyleScopedClasses['mobile-body']} */ ;
/** @type {__VLS_StyleScopedClasses['mobile-drawer']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-logo']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-head-text']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-title']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-sub']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-body']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-group-label']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item']} */ ;
/** @type {__VLS_StyleScopedClasses['current']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item-title']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item-arrow']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-item']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item-title']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-item-arrow']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-form']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-form-head']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-form-title']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-form-close']} */ ;
/** @type {__VLS_StyleScopedClasses['settings-form-body']} */ ;
/** @type {__VLS_StyleScopedClasses['form-field']} */ ;
/** @type {__VLS_StyleScopedClasses['form-label']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-picker']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-swatch']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-swatch-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['bg-swatch-label']} */ ;
/** @type {__VLS_StyleScopedClasses['form-field']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['form-label']} */ ;
/** @type {__VLS_StyleScopedClasses['form-field']} */ ;
/** @type {__VLS_StyleScopedClasses['form-label']} */ ;
/** @type {__VLS_StyleScopedClasses['form-field']} */ ;
/** @type {__VLS_StyleScopedClasses['form-label']} */ ;
/** @type {__VLS_StyleScopedClasses['form-field']} */ ;
/** @type {__VLS_StyleScopedClasses['form-row']} */ ;
/** @type {__VLS_StyleScopedClasses['form-label']} */ ;
/** @type {__VLS_StyleScopedClasses['form-field']} */ ;
/** @type {__VLS_StyleScopedClasses['form-label']} */ ;
/** @type {__VLS_StyleScopedClasses['form-tip']} */ ;
/** @type {__VLS_StyleScopedClasses['form-save']} */ ;
/** @type {__VLS_StyleScopedClasses['form-clear']} */ ;
/** @type {__VLS_StyleScopedClasses['form-clear']} */ ;
/** @type {__VLS_StyleScopedClasses['form-clear-vectors']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            route: route,
            menu: menu,
            currentTitle: currentTitle,
            showMenu: showMenu,
            go: go,
            BG_VARIANTS: BG_VARIANTS,
            TTS_LANGUAGES: TTS_LANGUAGES,
            agent: agent,
            theme: theme,
            chatSettings: chatSettings,
            clearConfirm: clearConfirm,
            onClearClick: onClearClick,
            onClearVectorsClick: onClearVectorsClick,
            showSettingsForm: showSettingsForm,
            showModelPicker: showModelPicker,
            formDirectory: formDirectory,
            modelColumns: modelColumns,
            formModelText: formModelText,
            openSettings: openSettings,
            onModelConfirm: onModelConfirm,
            showLangPicker: showLangPicker,
            langColumns: langColumns,
            onLangConfirm: onLangConfirm,
            saveSettings: saveSettings,
            currentView: currentView,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
