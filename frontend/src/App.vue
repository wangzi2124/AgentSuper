<script setup lang="ts">
import { onMounted } from 'vue'
import Sidebar from './components/Sidebar.vue'
import PermissionDialog from './components/PermissionDialog.vue'
import { usePermissionStore } from './stores/permission'

// 权限 store，用于管理工具调用审批
const perm = usePermissionStore()
// 启动时轮询待审批的权限请求
onMounted(() => {
  perm.pollPending()
})
</script>

<template>
  <div class="layout">
    <Sidebar />
    <main class="main">
      <router-view />
    </main>
  </div>
  <PermissionDialog />
</template>
