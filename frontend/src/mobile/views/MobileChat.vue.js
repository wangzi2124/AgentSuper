/// <reference types="../../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
// 移动端聊天页：复用完整的多智能体视图（流式、附件、权限、工作目录、模型选择等）
// 由 Vant NavBar 提供顶栏标题、TabBar 提供底部导航；移动端头部细节由 mobile.css 精简。
// 异步引入：与路由表的 `/multi-agent` 懒加载共享同一 chunk，避免重复下载。
import { defineAsyncComponent } from 'vue';
import { useRoute } from 'vue-router';
const MultiAgentView = defineAsyncComponent(() => import('../../views/MultiAgentView.vue'));
const route = useRoute();
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
const __VLS_0 = {}.MultiAgentView;
/** @type {[typeof __VLS_components.MultiAgentView, ]} */ ;
// @ts-ignore
const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
    ...{ class: "m-chat" },
    key: (__VLS_ctx.route.fullPath),
}));
const __VLS_2 = __VLS_1({
    ...{ class: "m-chat" },
    key: (__VLS_ctx.route.fullPath),
}, ...__VLS_functionalComponentArgsRest(__VLS_1));
var __VLS_4 = {};
var __VLS_3;
/** @type {__VLS_StyleScopedClasses['m-chat']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            MultiAgentView: MultiAgentView,
            route: route,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
