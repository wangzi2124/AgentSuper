/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, computed, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import MultiAgentChatHistory from './MultiAgentChatHistory.vue';
import PermissionDialog from './PermissionDialog.vue';
import SettingsPanel from './SettingsPanel.vue';
import { useAuthStore } from '../stores/auth';
import { useThemeStore, BG_VARIANTS } from '../stores/theme';
import { fetchStats, fetchUsage } from '../api/monitor';
const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const theme = useThemeStore();
const sidebarOpen = ref(false);
// 桌面收起（仅图标模式），记忆到 localStorage；移动端仍走抽屉，不受影响
const sidebarCollapsed = ref(false);
try {
    sidebarCollapsed.value = localStorage.getItem('agentsuper:sidebar-collapsed') === '1';
}
catch { /* ignore */ }
function toggleCollapsed() {
    sidebarCollapsed.value = !sidebarCollapsed.value;
    try {
        localStorage.setItem('agentsuper:sidebar-collapsed', sidebarCollapsed.value ? '1' : '0');
    }
    catch { /* ignore */ }
}
const navItems = [
    { path: '/multi-agent', label: '多智能体', icon: '🤖' },
    { path: '/documents', label: '文档管理', icon: '📄' },
    { path: '/plugins', label: '插件', icon: '🔌' },
    { path: '/custom-tools', label: '自定义工具', icon: '🧰' },
    { path: '/skills', label: '技能', icon: '⚡' },
    { path: '/vectors', label: '向量库', icon: '🔢' },
    { path: '/generated', label: '生成文件', icon: '📝' },
    { path: '/monitoring', label: '系统监控', icon: '📊' },
    { path: '/logs', label: '日志管理', icon: '🧾' },
    { path: '/model-manager', label: '模型管理', icon: '🧬' },
];
// ── 用户卡：本地未启用登录也展示（匿名本地用户），登录后展示账号 ──
const showUserCard = computed(() => (auth.enabled ? auth.isLoggedIn : true));
const usage = ref(null);
const stats = ref(null);
const statsLoading = ref(false);
const usageLoaded = ref(false);
const profileOpen = ref(false);
const nickDraft = ref('');
function fmt(n) {
    const v = n || 0;
    if (v >= 1e6)
        return (v / 1e6).toFixed(1) + 'M';
    if (v >= 1e3)
        return (v / 1e3).toFixed(1) + 'k';
    return String(v);
}
function fmtCost(v) {
    if (!v || v <= 0)
        return '免费';
    return `$${v.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`;
}
function isImgAvatar(v) {
    return !!v && (v.startsWith('data:') || v.startsWith('http'));
}
function initialOf() {
    return (auth.displayName || '?').slice(0, 1).toUpperCase();
}
async function refreshUsage() {
    statsLoading.value = true;
    try {
        const [u, s] = await Promise.all([fetchUsage(), fetchStats()]);
        usage.value = u;
        stats.value = s;
        usageLoaded.value = true;
    }
    catch {
        /* 忽略：接口暂不可用时静默 */
    }
    finally {
        statsLoading.value = false;
    }
}
function toggleProfile() {
    profileOpen.value = !profileOpen.value;
    if (profileOpen.value) {
        nickDraft.value = auth.nickname;
        // 用量/统计只在面板打开时拉取：原先 onMounted 无条件发这两个请求，
        // 而它们仅在面板内渲染 → 每次进任意页面都白付 2 个请求。
        if (!usageLoaded.value)
            void refreshUsage();
    }
}
function saveNick() {
    auth.setNickname(nickDraft.value);
    profileOpen.value = false;
}
function resetAvatar() {
    auth.setAvatar('');
}
const AVATAR_PRESETS = ['🧑‍💻', '🤖', '🦊', '🐱', '🌟', '🎨'];
function onAvatarFile(e) {
    const input = e.target;
    const file = input.files?.[0];
    input.value = '';
    if (!file)
        return;
    if (file.size > 2 * 1024 * 1024) {
        window.alert('图片过大，请选择 2MB 以内的图片');
        return;
    }
    const reader = new FileReader();
    reader.onload = () => {
        const img = new Image();
        img.onload = () => {
            const max = 256;
            let w = img.width;
            let h = img.height;
            if (w > max || h > max) {
                const s = Math.min(max / w, max / h);
                w = Math.round(w * s);
                h = Math.round(h * s);
            }
            const cv = document.createElement('canvas');
            cv.width = w;
            cv.height = h;
            const ctx = cv.getContext('2d');
            if (ctx)
                ctx.drawImage(img, 0, 0, w, h);
            auth.setAvatar(cv.toDataURL('image/png'));
        };
        img.src = String(reader.result);
    };
    reader.readAsDataURL(file);
}
function goMonitor() {
    profileOpen.value = false;
    router.push('/monitoring');
}
// 不在 onMounted 拉用量/统计（见 toggleProfile 注释）——改为首次打开用户设置面板时按需拉取
watch(() => route.path, () => { sidebarOpen.value = false; });
function handleLogout() {
    auth.logout();
    router.push({ name: 'Login' });
}
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
/** @type {__VLS_StyleScopedClasses['user-edit']} */ ;
/** @type {__VLS_StyleScopedClasses['us-row']} */ ;
/** @type {__VLS_StyleScopedClasses['us-row']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-close']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-input']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-preset']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-upload']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-monitor-link']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-sep']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-sep']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-sep']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-bg-swatch']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-bg-swatch']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-bg-swatch']} */ ;
/** @type {__VLS_StyleScopedClasses['on']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-bg-swatch']} */ ;
/** @type {__VLS_StyleScopedClasses['on']} */ ;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (...[$event]) => {
            __VLS_ctx.sidebarOpen = !__VLS_ctx.sidebarOpen;
        } },
    ...{ class: "sidebar-toggle" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
    x1: "3",
    y1: "6",
    x2: "21",
    y2: "6",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
    x1: "3",
    y1: "12",
    x2: "21",
    y2: "12",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
    x1: "3",
    y1: "18",
    x2: "21",
    y2: "18",
});
if (__VLS_ctx.sidebarOpen) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.sidebarOpen))
                    return;
                __VLS_ctx.sidebarOpen = false;
            } },
        ...{ class: "sidebar-overlay" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.aside, __VLS_intrinsicElements.aside)({
    ...{ class: "sidebar" },
    ...{ class: ({ open: __VLS_ctx.sidebarOpen, collapsed: __VLS_ctx.sidebarCollapsed }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "sidebar-header" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "brand-row" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "brand-mark" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "22",
    height: "22",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M12 2L2 7l10 5 10-5-10-5z",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M2 17l10 5 10-5",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M2 12l10 5 10-5",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "brand-text" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.h1, __VLS_intrinsicElements.h1)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.toggleCollapsed) },
    ...{ class: "sidebar-collapse" },
    title: (__VLS_ctx.sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'),
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
__VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
    points: "15 18 9 12 15 6",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.nav, __VLS_intrinsicElements.nav)({
    ...{ class: "sidebar-nav" },
});
for (const [item] of __VLS_getVForSourceType((__VLS_ctx.navItems))) {
    const __VLS_0 = {}.RouterLink;
    /** @type {[typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, typeof __VLS_components.RouterLink, typeof __VLS_components.routerLink, ]} */ ;
    // @ts-ignore
    const __VLS_1 = __VLS_asFunctionalComponent(__VLS_0, new __VLS_0({
        key: (item.path),
        to: (item.path),
        ...{ class: "nav-item" },
        ...{ class: ({ active: __VLS_ctx.route.path === item.path }) },
        title: (item.label),
    }));
    const __VLS_2 = __VLS_1({
        key: (item.path),
        to: (item.path),
        ...{ class: "nav-item" },
        ...{ class: ({ active: __VLS_ctx.route.path === item.path }) },
        title: (item.label),
    }, ...__VLS_functionalComponentArgsRest(__VLS_1));
    __VLS_3.slots.default;
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "nav-icon" },
    });
    (item.icon);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "nav-label" },
    });
    (item.label);
    var __VLS_3;
}
/** @type {[typeof MultiAgentChatHistory, ]} */ ;
// @ts-ignore
const __VLS_4 = __VLS_asFunctionalComponent(MultiAgentChatHistory, new MultiAgentChatHistory({
    ...{ class: "sidebar-history" },
}));
const __VLS_5 = __VLS_4({
    ...{ class: "sidebar-history" },
}, ...__VLS_functionalComponentArgsRest(__VLS_4));
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "sidebar-bottom" },
});
/** @type {[typeof PermissionDialog, ]} */ ;
// @ts-ignore
const __VLS_7 = __VLS_asFunctionalComponent(PermissionDialog, new PermissionDialog({}));
const __VLS_8 = __VLS_7({}, ...__VLS_functionalComponentArgsRest(__VLS_7));
if (__VLS_ctx.showUserCard) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "user-card" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "user-avatar-wrap" },
    });
    if (__VLS_ctx.isImgAvatar(__VLS_ctx.auth.avatar)) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.img)({
            ...{ class: "user-avatar user-avatar-img" },
            src: (__VLS_ctx.auth.avatar),
            alt: "avatar",
        });
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "user-avatar" },
        });
        (__VLS_ctx.auth.avatar || __VLS_ctx.initialOf());
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "user-meta" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "user-name" },
        title: (__VLS_ctx.auth.displayName),
    });
    (__VLS_ctx.auth.displayName);
    if (__VLS_ctx.auth.enabled && __VLS_ctx.auth.user_id) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "user-id" },
            title: (__VLS_ctx.auth.user_id),
        });
        (__VLS_ctx.auth.user_id);
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "user-id" },
        });
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.toggleProfile) },
        ...{ class: "user-edit" },
        title: "编辑资料 / 用量与监控",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "13",
        height: "13",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
        'stroke-linecap': "round",
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z",
    });
    if (__VLS_ctx.auth.enabled && __VLS_ctx.auth.isLoggedIn) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (__VLS_ctx.handleLogout) },
            ...{ class: "logout-btn" },
            title: "退出登录",
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
            d: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
            points: "16 17 21 12 16 7",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
            x1: "21",
            y1: "12",
            x2: "9",
            y2: "12",
        });
    }
}
if (__VLS_ctx.showUserCard && __VLS_ctx.profileOpen) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "profile-panel" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "pp-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-head-title" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.showUserCard && __VLS_ctx.profileOpen))
                    return;
                __VLS_ctx.profileOpen = false;
            } },
        ...{ class: "pp-close" },
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
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
        x1: "18",
        y1: "6",
        x2: "6",
        y2: "18",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
        x1: "6",
        y1: "6",
        x2: "18",
        y2: "18",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "pp-stats" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.goMonitor) },
        ...{ class: "pp-stat" },
        type: "button",
        title: "我的累计输入 Token（跨会话）· 点击查看系统监控",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-ico" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-v" },
    });
    (__VLS_ctx.usage ? __VLS_ctx.fmt(__VLS_ctx.usage.tokens_input) : (__VLS_ctx.statsLoading ? '…' : '—'));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-k" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.goMonitor) },
        ...{ class: "pp-stat" },
        type: "button",
        title: "我的累计输出 Token（跨会话）· 点击查看系统监控",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-ico" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-v" },
    });
    (__VLS_ctx.usage ? __VLS_ctx.fmt(__VLS_ctx.usage.tokens_output) : (__VLS_ctx.statsLoading ? '…' : '—'));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-k" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.goMonitor) },
        ...{ class: "pp-stat" },
        type: "button",
        title: "我的累计对话轮数 · 点击查看系统监控",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-ico" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-v" },
    });
    (__VLS_ctx.usage ? __VLS_ctx.fmt(__VLS_ctx.usage.requests) : (__VLS_ctx.statsLoading ? '…' : '—'));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-k" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.goMonitor) },
        ...{ class: "pp-stat" },
        type: "button",
        title: "系统累计 HTTP 请求数 · 点击查看系统监控",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-ico" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-v" },
    });
    (__VLS_ctx.stats ? __VLS_ctx.fmt(__VLS_ctx.stats.requests.total) : (__VLS_ctx.statsLoading ? '…' : '—'));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-k" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.goMonitor) },
        ...{ class: "pp-stat" },
        type: "button",
        title: "系统累计 LLM 调用数 · 点击查看系统监控",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-ico" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-v" },
    });
    (__VLS_ctx.stats ? __VLS_ctx.fmt(__VLS_ctx.stats.model_calls.total) : (__VLS_ctx.statsLoading ? '…' : '—'));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-k" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.goMonitor) },
        ...{ class: "pp-stat" },
        type: "button",
        title: "系统累计 LLM 成本 · 点击查看系统监控",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-ico" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-v" },
    });
    (__VLS_ctx.stats ? __VLS_ctx.fmtCost(__VLS_ctx.stats.total_cost) : (__VLS_ctx.statsLoading ? '…' : '—'));
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-stat-k" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.goMonitor) },
        ...{ class: "pp-monitor-link" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "13",
        height: "13",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2",
        'stroke-linecap': "round",
        'stroke-linejoin': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M3 3v18h18",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M7 14l3-3 3 3 5-6",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "pp-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ onKeyup: (__VLS_ctx.saveNick) },
        ...{ class: "pp-input" },
        maxlength: "30",
        placeholder: "输入昵称",
    });
    (__VLS_ctx.nickDraft);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "pp-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "pp-avatar-row" },
    });
    for (const [p] of __VLS_getVForSourceType((__VLS_ctx.AVATAR_PRESETS))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.showUserCard && __VLS_ctx.profileOpen))
                        return;
                    __VLS_ctx.auth.setAvatar(p);
                } },
            key: (p),
            ...{ class: "pp-preset" },
            ...{ class: ({ on: __VLS_ctx.auth.avatar === p }) },
        });
        (p);
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.label, __VLS_intrinsicElements.label)({
        ...{ class: "pp-preset pp-upload" },
        title: "上传图片",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
        ...{ onChange: (__VLS_ctx.onAvatarFile) },
        type: "file",
        accept: "image/*",
        ...{ style: {} },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "pp-sep" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "pp-field" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "pp-label" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "pp-bg-row" },
    });
    for (const [v] of __VLS_getVForSourceType((__VLS_ctx.BG_VARIANTS))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.showUserCard && __VLS_ctx.profileOpen))
                        return;
                    __VLS_ctx.theme.setBg(v.value);
                } },
            key: (v.value),
            ...{ class: "pp-bg-swatch" },
            ...{ class: ({ on: __VLS_ctx.theme.bgVariant === v.value }) },
            title: (v.label),
            ...{ style: ({ '--sw': v.swatch, '--sw-glow': v.glowSwatch || v.swatch }) },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({});
    }
    /** @type {[typeof SettingsPanel, ]} */ ;
    // @ts-ignore
    const __VLS_10 = __VLS_asFunctionalComponent(SettingsPanel, new SettingsPanel({
        ...{ 'onClose': {} },
    }));
    const __VLS_11 = __VLS_10({
        ...{ 'onClose': {} },
    }, ...__VLS_functionalComponentArgsRest(__VLS_10));
    let __VLS_13;
    let __VLS_14;
    let __VLS_15;
    const __VLS_16 = {
        onClose: (...[$event]) => {
            if (!(__VLS_ctx.showUserCard && __VLS_ctx.profileOpen))
                return;
            __VLS_ctx.profileOpen = false;
        }
    };
    var __VLS_12;
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "pp-actions" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.resetAvatar) },
        ...{ class: "pp-btn" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.saveNick) },
        ...{ class: "pp-btn pp-btn-primary" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "sidebar-footer" },
});
/** @type {__VLS_StyleScopedClasses['sidebar-toggle']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-overlay']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar']} */ ;
/** @type {__VLS_StyleScopedClasses['open']} */ ;
/** @type {__VLS_StyleScopedClasses['collapsed']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-header']} */ ;
/** @type {__VLS_StyleScopedClasses['brand-row']} */ ;
/** @type {__VLS_StyleScopedClasses['brand-mark']} */ ;
/** @type {__VLS_StyleScopedClasses['brand-text']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-collapse']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-nav']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-item']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['nav-label']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-history']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-bottom']} */ ;
/** @type {__VLS_StyleScopedClasses['user-card']} */ ;
/** @type {__VLS_StyleScopedClasses['user-avatar-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['user-avatar']} */ ;
/** @type {__VLS_StyleScopedClasses['user-avatar-img']} */ ;
/** @type {__VLS_StyleScopedClasses['user-avatar']} */ ;
/** @type {__VLS_StyleScopedClasses['user-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['user-name']} */ ;
/** @type {__VLS_StyleScopedClasses['user-id']} */ ;
/** @type {__VLS_StyleScopedClasses['user-id']} */ ;
/** @type {__VLS_StyleScopedClasses['user-edit']} */ ;
/** @type {__VLS_StyleScopedClasses['logout-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['profile-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-head']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-head-title']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-close']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stats']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-v']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-k']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-v']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-k']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-v']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-k']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-v']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-k']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-v']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-k']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-ico']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-v']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-stat-k']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-monitor-link']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-field']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-label']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-input']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-field']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-label']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-avatar-row']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-preset']} */ ;
/** @type {__VLS_StyleScopedClasses['on']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-preset']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-upload']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-sep']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-field']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-label']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-bg-row']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-bg-swatch']} */ ;
/** @type {__VLS_StyleScopedClasses['on']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['pp-btn-primary']} */ ;
/** @type {__VLS_StyleScopedClasses['sidebar-footer']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            MultiAgentChatHistory: MultiAgentChatHistory,
            PermissionDialog: PermissionDialog,
            SettingsPanel: SettingsPanel,
            BG_VARIANTS: BG_VARIANTS,
            route: route,
            auth: auth,
            theme: theme,
            sidebarOpen: sidebarOpen,
            sidebarCollapsed: sidebarCollapsed,
            toggleCollapsed: toggleCollapsed,
            navItems: navItems,
            showUserCard: showUserCard,
            usage: usage,
            stats: stats,
            statsLoading: statsLoading,
            profileOpen: profileOpen,
            nickDraft: nickDraft,
            fmt: fmt,
            fmtCost: fmtCost,
            isImgAvatar: isImgAvatar,
            initialOf: initialOf,
            toggleProfile: toggleProfile,
            saveNick: saveNick,
            resetAvatar: resetAvatar,
            AVATAR_PRESETS: AVATAR_PRESETS,
            onAvatarFile: onAvatarFile,
            goMonitor: goMonitor,
            handleLogout: handleLogout,
        };
    },
});
export default (await import('vue')).defineComponent({
    setup() {
        return {};
    },
});
; /* PartiallyEnd: #4569/main.vue */
