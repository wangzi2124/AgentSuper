<script setup lang="ts">
import { onMounted, ref, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import Sidebar from './components/Sidebar.vue'
import MobileShell from './mobile/MobileShell.vue'
import { usePermissionStore } from './stores/permission'
import { useThemeStore } from './stores/theme'
import { useAuthStore } from './stores/auth'

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
const mql = typeof window !== 'undefined' ? window.matchMedia('(max-width: 768px)') : null
function syncMobile() {
  isMobile.value = !!mql?.matches
}
syncMobile()
mql?.addEventListener?.('change', syncMobile)
onBeforeUnmount(() => mql?.removeEventListener?.('change', syncMobile))
</script>

<template>
  <MobileShell v-if="isMobile && route.name !== 'Login'" />
  <div v-else class="layout">
    <Sidebar v-if="route.name !== 'Login'" />
    <main class="main">
      <router-view />
    </main>
  </div>
</template>
