<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute } from 'vue-router'
import Sidebar from './components/Sidebar.vue'
import { usePermissionStore } from './stores/permission'

const perm = usePermissionStore()
const route = useRoute()
onMounted(() => {
  perm.pollPending()
})
</script>

<template>
  <div class="layout">
    <Sidebar v-if="route.name !== 'Login'" />
    <main class="main">
      <router-view />
    </main>
  </div>
</template>
