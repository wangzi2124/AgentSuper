/// <reference types="../../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { onMounted } from 'vue';
import { usePluginStore } from '../../stores/plugins';
import { showToast } from 'vant';
const plugins = usePluginStore();
onMounted(() => {
    plugins.fetchAll();
});
async function handleToggle(name, enabled) {
    try {
        await plugins.toggle(name, enabled);
        showToast(enabled ? '已禁用' : '已启用');
    }
    catch (err) {
        showToast(err.message || '操作失败');
    }
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "m-plugins" },
});
if (__VLS_ctx.plugins.loading) {
    const __VLS_0 = {}.VanLoading;
    /** @type {[typeof __VLS_components.VanLoading, typeof __VLS_components.vanLoading, ]} */ ;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
        ...{ class: "loading" },
    }));
    const __VLS_2 = __VLS_1({
        ...{ class: "loading" },
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
}
else if (__VLS_ctx.plugins.plugins.length === 0) {
    const __VLS_4 = {}.VanEmpty;
    /** @type {[typeof __VLS_components.VanEmpty, typeof __VLS_components.vanEmpty, ]} */ ;
    // @ts-ignore
    const __VLS_5 = __VLS_asFunctionalComponent(__VLS_4, new __VLS_4({
        image: "search",
        description: "未加载任何插件",
    }));
    const __VLS_6 = __VLS_5({
        image: "search",
        description: "未加载任何插件",
    }, ...__VLS_functionalComponentArgsRest(__VLS_5));
}
else {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "plugin-list" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "m-count-pill" },
    });
    const __VLS_8 = {}.VanIcon;
    /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
    // @ts-ignore
    const __VLS_9 = __VLS_asFunctionalComponent(__VLS_8, new __VLS_8({
        name: "apps-o",
    }));
    const __VLS_10 = __VLS_9({
        name: "apps-o",
    }, ...__VLS_functionalComponentArgsRest(__VLS_9));
    (__VLS_ctx.plugins.plugins.length);
    for (const [plugin] of __VLS_getVForSourceType((__VLS_ctx.plugins.plugins))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            key: (plugin.name),
            ...{ class: "m-card plugin-item" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "plugin-ico" },
        });
        const __VLS_12 = {}.VanIcon;
        /** @type {[typeof __VLS_components.VanIcon, typeof __VLS_components.vanIcon, ]} */ ;
        // @ts-ignore
        const __VLS_13 = __VLS_asFunctionalComponent(__VLS_12, new __VLS_12({
            name: "apps-o",
        }));
        const __VLS_14 = __VLS_13({
            name: "apps-o",
        }, ...__VLS_functionalComponentArgsRest(__VLS_13));
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "plugin-main" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "plugin-name" },
        });
        (plugin.name);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "plugin-desc" },
        });
        (plugin.description || plugin.version);
        const __VLS_16 = {}.VanSwitch;
        /** @type {[typeof __VLS_components.VanSwitch, typeof __VLS_components.vanSwitch, ]} */ ;
        // @ts-ignore
        const __VLS_17 = __VLS_asFunctionalComponent(__VLS_16, new __VLS_16({
            ...{ 'onUpdate:modelValue': {} },
            modelValue: (plugin.enabled),
            size: "20px",
        }));
        const __VLS_18 = __VLS_17({
            ...{ 'onUpdate:modelValue': {} },
            modelValue: (plugin.enabled),
            size: "20px",
        }, ...__VLS_functionalComponentArgsRest(__VLS_17));
        let __VLS_20;
        let __VLS_21;
        let __VLS_22;
        const __VLS_23 = {
            'onUpdate:modelValue': (...[$event]) => {
                if (!!(__VLS_ctx.plugins.loading))
                    return;
                if (!!(__VLS_ctx.plugins.plugins.length === 0))
                    return;
                __VLS_ctx.handleToggle(plugin.name, plugin.enabled);
            }
        };
        var __VLS_19;
    }
}
/** @type {__VLS_StyleScopedClasses['m-plugins']} */ ;
/** @type {__VLS_StyleScopedClasses['loading']} */ ;
/** @type {__VLS_StyleScopedClasses['plugin-list']} */ ;
/** @type {__VLS_StyleScopedClasses['m-count-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['m-card']} */ ;
/** @type {__VLS_StyleScopedClasses['plugin-item']} */ ;
/** @type {__VLS_StyleScopedClasses['plugin-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['plugin-main']} */ ;
/** @type {__VLS_StyleScopedClasses['plugin-name']} */ ;
/** @type {__VLS_StyleScopedClasses['plugin-desc']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            plugins: plugins,
            handleToggle: handleToggle,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
