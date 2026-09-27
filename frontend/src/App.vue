<script setup lang="ts">
import { onMounted, ref, onBeforeUnmount, watch, defineAsyncComponent, getCurrentInstance } from 'vue'
import { useRoute } from 'vue-router'
import Sidebar from './components/Sidebar.vue'
import { usePermissionStore } from './stores/permission'
import { useThemeStore } from './stores/theme'
import { useAuthStore } from './stores/auth'
import { ensureVant } from './mobile/vant'

// 移动端外壳改为异步组件：此前 App.vue 静态 import MobileShell，会把 7 个移动页
// + MobileChat → 完整 MultiAgentView + ChatInput 全部拖进首屏 chunk（桌面端也照付），
// 连带 vant 558KB / highlight.js 369KB。改为 defineAsyncComponent 后这些独立成 chunk，
// 桌面端首屏完全不必下载。
const MobileShell = defineAsyncComponent(() => import('./mobile/MobileShell.vue'))

const perm = usePermissionStore()
const theme = useThemeStore()
const auth = useAuthStore()
const route = useRoute()
onMounted(() => {
  theme.init()
})
// 仅当鉴权放行（auth 未启用 或 已登录）时轮询待审批权限请求；
// 未登录（登录页）不发请求，避免控制台 401 噪音；登录成功后自动补拉
// startPolling：SSE 路径靠 permission_request 事件即时弹出；此轮询作为兜底，
// 使非流式调用（POST /api/chat/multi-agent）产生的 pending 请求也能弹出审批面板。
// 无待处理请求时自动停止轮询。
watch(
  () => !auth.enabled || auth.isLoggedIn,
  (canFetch) => { if (canFetch) perm.startPolling() },
  { immediate: true },
)

// 移动端（≤768px）渲染 MobileShell（Vant NavBar + TabBar），桌面保持 Sidebar 布局
const isMobile = ref(false)
// Vant 动态注册完成前不渲染 MobileShell，否则 van-* 组件解析不到会渲染成空白标签
const mobileUiReady = ref(false)
const mql = typeof window !== 'undefined' ? window.matchMedia('(max-width: 768px)') : null
async function syncMobile() {
  const mobile = !!mql?.matches
  isMobile.value = mobile
  if (mobile && !mobileUiReady.value) {
    const app = getCurrentInstance()?.appContext.app
    if (app) {
      await ensureVant(app)
      mobileUiReady.value = true
    }
  }
}
void syncMobile()
function onViewportChange() { void syncMobile() }
mql?.addEventListener?.('change', onViewportChange)
onBeforeUnmount(() => mql?.removeEventListener?.('change', onViewportChange))
</script>

<template>
  <MobileShell v-if="isMobile && mobileUiReady && route.name !== 'Login'" />
  <div v-else class="layout">
    <Sidebar v-if="route.name !== 'Login'" />
    <main class="main">
      <router-view />
    </main>
  </div>
</template>
