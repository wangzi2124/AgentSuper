<script setup lang="ts">
import { onMounted, ref, onBeforeUnmount } from 'vue'
import { useRoute } from 'vue-router'
import Sidebar from './components/Sidebar.vue'
import MobileShell from './mobile/MobileShell.vue'
import { usePermissionStore } from './stores/permission'
import { useThemeStore } from './stores/theme'

const perm = usePermissionStore()
const theme = useThemeStore()
const route = useRoute()
onMounted(() => {
  perm.pollPending()
  theme.init()
})

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
