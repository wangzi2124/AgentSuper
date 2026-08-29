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
// 仅当鉴权放行（auth 未启用 或 已登录）时拉取待审批权限请求；
// 未登录（登录页）不发请求，避免控制台 401 噪音；登录成功后自动补拉
watch(
  () => !auth.enabled || auth.isLoggedIn,
  (canFetch) => { if (canFetch) perm.pollPending() },
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
