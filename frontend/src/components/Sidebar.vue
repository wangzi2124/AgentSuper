<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import MultiAgentChatHistory from './MultiAgentChatHistory.vue'
import PermissionDialog from './PermissionDialog.vue'

const route = useRoute()

const sidebarOpen = ref(false)

// 桌面收起（仅图标模式），记忆到 localStorage；移动端仍走抽屉，不受影响
const sidebarCollapsed = ref(false)
try { sidebarCollapsed.value = localStorage.getItem('agentsuper:sidebar-collapsed') === '1' } catch { /* ignore */ }
function toggleCollapsed() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  try { localStorage.setItem('agentsuper:sidebar-collapsed', sidebarCollapsed.value ? '1' : '0') } catch { /* ignore */ }
}

watch(() => route.path, () => { sidebarOpen.value = false })
</script>

<template>
  <button class="sidebar-toggle" @click="sidebarOpen = !sidebarOpen">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
  </button>
  <div v-if="sidebarOpen" class="sidebar-overlay" @click="sidebarOpen = false"></div>
  <aside class="sidebar" :class="{ open: sidebarOpen, collapsed: sidebarCollapsed }">
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
          <p>知识库工作台</p>
        </div>
        <button class="sidebar-collapse" :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" @click="toggleCollapsed">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
      </div>
    </div>

    <MultiAgentChatHistory class="sidebar-history" />

    <div class="sidebar-bottom">
      <PermissionDialog v-if="!route.path.startsWith('/multi-agent')" />
    </div>
  </aside>
</template>


<style scoped src="../styles/chat/sidebar.css"></style>
