<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import MultiAgentChatHistory from './MultiAgentChatHistory.vue'
import PermissionDialog from './PermissionDialog.vue'
import { useSidebarStore } from '../stores/sidebar'

const route = useRoute()
const sidebar = useSidebarStore()

const sidebarOpen = ref(false)

watch(() => route.path, () => { sidebarOpen.value = false })
</script>

<template>
  <button class="sidebar-toggle" @click="sidebarOpen = !sidebarOpen">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
  </button>
  <div v-if="sidebarOpen" class="sidebar-overlay" @click="sidebarOpen = false"></div>
  <aside class="sidebar" :class="{ open: sidebarOpen, collapsed: sidebar.collapsed }">
    <div class="sidebar-header">
      <div class="brand-row">
        <div class="brand-mark">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2 2 7l10 5 10-5-10-5z"/>
            <path d="M2 17l10 5 10-5"/>
            <path d="M2 12l10 5 10-5"/>
          </svg>
        </div>
        <div class="brand-text">
          <h1>AgentSuper</h1>
        </div>
      </div>
    </div>

    <MultiAgentChatHistory class="sidebar-history" />

    <div class="sidebar-bottom">
      <PermissionDialog v-if="!route.path.startsWith('/multi-agent')" />
    </div>
  </aside>
</template>


<style scoped src="../styles/chat/sidebar.css"></style>
