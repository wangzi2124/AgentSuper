/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, computed, onMounted } from 'vue';
import { usePermissionStore } from '../stores/permission';
import { useMultiAgentStore } from '../stores/multiAgent';
import { useChatSettingsStore, TTS_LANGUAGES } from '../stores/chatSettings';
import DirPickerModal from './DirPickerModal.vue';
import WeatherAlert from './WeatherAlert.vue';
const emit = defineEmits();
const perm = usePermissionStore();
const agent = useMultiAgentStore();
const chatSettings = useChatSettingsStore();
const wsInput = ref('');
const wsBusy = ref(false);
const wsError = ref('');
const showDirPicker = ref(false);
const isWeatherEnabled = ref(false);
const showWeather = ref(false);
const messages = computed(() => agent.messages);
const extraWorkspaces = computed(() => (perm.workspaces.length > 1 ? perm.workspaces.slice(1) : []));
const autoRead = computed(() => chatSettings.autoRead);
async function checkWeatherPlugin() {
    try {
        const { addAuthHeaders } = await import('../api/fetch');
        const response = await fetch('/api/plugins/weather-alert/status', { headers: await addAuthHeaders() });
        if (response.ok) {
            const data = await response.json();
            isWeatherEnabled.value = data.enabled;
        }
    }
    catch (e) {
        console.error('Failed to check weather plugin status:', e);
    }
}
async function handleAddWorkspace() {
    const path = wsInput.value.trim();
    if (!path) {
        wsError.value = '请输入绝对路径，如 F:\\tetris';
        return;
    }
    wsBusy.value = true;
    wsError.value = '';
    try {
        await perm.addWorkspace(path);
        wsInput.value = '';
    }
    catch (e) {
        wsError.value = e?.message || '添加失败';
    }
    finally {
        wsBusy.value = false;
    }
}
async function handleRemoveWorkspace(path) {
    try {
        await perm.removeWorkspace(path);
    }
    catch (e) {
        wsError.value = e?.message || '移除失败';
    }
}
function handleDirPick(path) {
    wsInput.value = path;
    showDirPicker.value = false;
    wsError.value = '';
}
function handleClearConversation() {
    if (!confirm('确定清空当前对话？此操作不可撤销。'))
        return;
    agent.deleteConversation();
    emit('close');
}
onMounted(() => {
    perm.loadWorkspaces();
    checkWeatherPlugin();
});
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "settings-panel" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-section" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "ws-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.showDirPicker = true;
        } },
    ...{ class: "ws-pick-btn" },
    title: "选择目录",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "14",
    height: "14",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ onKeyup: (__VLS_ctx.handleAddWorkspace) },
    ...{ class: "ws-input" },
    placeholder: "\u0046\u003a\u005c\u0074\u0065\u0074\u0072\u0069\u0073",
});
(__VLS_ctx.wsInput);
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.handleAddWorkspace) },
    ...{ class: "ws-add" },
    disabled: (__VLS_ctx.wsBusy),
});
(__VLS_ctx.wsBusy ? '添加中...' : '添加');
if (__VLS_ctx.wsError) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "ws-error" },
    });
    (__VLS_ctx.wsError);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "ws-list" },
});
for (const [w] of __VLS_getVForSourceType((__VLS_ctx.extraWorkspaces))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        key: (w),
        ...{ class: "ws-item" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "ws-dot" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "ws-path" },
    });
    (w);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                __VLS_ctx.handleRemoveWorkspace(w);
            } },
        ...{ class: "ws-remove" },
        title: "移除",
    });
}
if (__VLS_ctx.extraWorkspaces.length === 0) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "ws-empty" },
    });
}
if (__VLS_ctx.agent.sessionDirectory) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
        ...{ class: "drawer-session-dir" },
        title: (__VLS_ctx.agent.sessionDirectory),
    });
    (__VLS_ctx.agent.sessionDirectory);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-section" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "toggle-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "toggle-row-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
    ...{ class: "toggle" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    type: "checkbox",
    disabled: (__VLS_ctx.agent.loading),
});
(__VLS_ctx.agent.useVectorDb);
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "toggle-slider" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "toggle-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "toggle-row-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
    ...{ class: "toggle" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ onChange: (...[$event]) => {
            __VLS_ctx.chatSettings.autoRead = $event.target.checked;
        } },
    type: "checkbox",
    checked: (__VLS_ctx.autoRead),
    disabled: (__VLS_ctx.agent.loading),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "toggle-slider" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "toggle-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "toggle-row-label" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.select, __VLS_intrinsicElements.select)({
    value: (__VLS_ctx.chatSettings.ttsLang),
    ...{ class: "drawer-select tts-lang-select" },
    disabled: (__VLS_ctx.agent.loading),
});
for (const [l] of __VLS_getVForSourceType((__VLS_ctx.TTS_LANGUAGES))) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.option, __VLS_intrinsicElements.option)({
        key: (l.value),
        value: (l.value),
    });
    (l.label);
}
if (__VLS_ctx.isWeatherEnabled) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "drawer-section" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "drawer-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.isWeatherEnabled))
                    return;
                __VLS_ctx.showWeather = true;
            } },
        ...{ class: "drawer-row-btn" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "drawer-row-icon" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "drawer-row-text" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "drawer-row-chevron" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "drawer-section" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.handleClearConversation) },
    ...{ class: "drawer-danger" },
    disabled: (__VLS_ctx.agent.loading || __VLS_ctx.messages.length === 0),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "15",
    height: "15",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
    points: "3 6 5 6 21 6",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2",
});
/** @type {[typeof DirPickerModal, ]} */ ;
// @ts-ignore
const __VLS_0 = __VLS_asFunctionalComponent(DirPickerModal, new DirPickerModal({
    ...{ 'onClose': {} },
    ...{ 'onSelect': {} },
    show: (__VLS_ctx.showDirPicker),
}));
const __VLS_1 = __VLS_0({
    ...{ 'onClose': {} },
    ...{ 'onSelect': {} },
    show: (__VLS_ctx.showDirPicker),
}, ...__VLS_functionalComponentArgsRest(__VLS_0));
let __VLS_3;
let __VLS_4;
let __VLS_5;
const __VLS_6 = {
    onClose: (...[$event]) => {
        __VLS_ctx.showDirPicker = false;
    }
};
const __VLS_7 = {
    onSelect: (__VLS_ctx.handleDirPick)
};
var __VLS_2;
if (__VLS_ctx.isWeatherEnabled) {
    /** @type {[typeof WeatherAlert, ]} */ ;
    // @ts-ignore
    const __VLS_8 = __VLS_asFunctionalComponent(WeatherAlert, new WeatherAlert({
        ...{ 'onUpdate:show': {} },
        show: (__VLS_ctx.showWeather),
    }));
    const __VLS_9 = __VLS_8({
        ...{ 'onUpdate:show': {} },
        show: (__VLS_ctx.showWeather),
    }, ...__VLS_functionalComponentArgsRest(__VLS_8));
    let __VLS_11;
    let __VLS_12;
    let __VLS_13;
    const __VLS_14 = {
        'onUpdate:show': (...[$event]) => {
            if (!(__VLS_ctx.isWeatherEnabled))
                return;
            __VLS_ctx.showWeather = $event;
        }
    };
    var __VLS_10;
}
/** @type {__VLS_StyleScopedClasses['settings-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-section']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-label']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-row']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-pick-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-input']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-add']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-error']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-list']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-item']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-dot']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-path']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-remove']} */ ;
/** @type {__VLS_StyleScopedClasses['ws-empty']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-session-dir']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-section']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-label']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle-row']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle-row-label']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle-slider']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle-row']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle-row-label']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle-slider']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle-row']} */ ;
/** @type {__VLS_StyleScopedClasses['toggle-row-label']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-select']} */ ;
/** @type {__VLS_StyleScopedClasses['tts-lang-select']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-section']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-label']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-row-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-row-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-row-text']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-row-chevron']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-section']} */ ;
/** @type {__VLS_StyleScopedClasses['drawer-danger']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            TTS_LANGUAGES: TTS_LANGUAGES,
            DirPickerModal: DirPickerModal,
            WeatherAlert: WeatherAlert,
            agent: agent,
            chatSettings: chatSettings,
            wsInput: wsInput,
            wsBusy: wsBusy,
            wsError: wsError,
            showDirPicker: showDirPicker,
            isWeatherEnabled: isWeatherEnabled,
            showWeather: showWeather,
            messages: messages,
            extraWorkspaces: extraWorkspaces,
            autoRead: autoRead,
            handleAddWorkspace: handleAddWorkspace,
            handleRemoveWorkspace: handleRemoveWorkspace,
            handleDirPick: handleDirPick,
            handleClearConversation: handleClearConversation,
        };
    },
    __typeEmits: {},
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
    __typeEmits: {},
});
; /* PartiallyEnd: #4569/main.vue */
