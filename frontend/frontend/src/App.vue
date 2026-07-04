<script setup lang="ts">
import Sidebar from './components/Sidebar.vue'
</script>

<template>
  <div class="layout">
    <Sidebar />
    <main class="main">
      <router-view />
    </main>
  </div>
</template>
