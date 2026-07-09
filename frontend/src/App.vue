<script setup lang="ts">
import { onMounted } from 'vue'
import Sidebar from './components/Sidebar.vue'
import PermissionDialog from './components/PermissionDialog.vue'
import { usePermissionStore } from './stores/permission'

const perm = usePermissionStore()
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
