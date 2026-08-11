<script setup lang="ts">
import { useRoute } from 'vue-router'
import ChatHistory from './ChatHistory.vue'
import MultiAgentChatHistory from './MultiAgentChatHistory.vue'
import PermissionDialog from './PermissionDialog.vue'

const route = useRoute()

const navItems = [
  { path: '/chat', label: 'Chat', icon: '💬' },
  { path: '/multi-agent', label: 'Multi-Agent', icon: '🤖' },
  { path: '/documents', label: 'Documents', icon: '📄' },
  { path: '/skills', label: 'Skills', icon: '🧠' },
  { path: '/plugins', label: 'Plugins', icon: '🔌' },
  { path: '/custom-tools', label: 'Custom Tools', icon: '🧰' },
  { path: '/vectors', label: 'Vectors', icon: '🔢' },
  { path: '/generated', label: 'Generated', icon: '📝' },
  { path: '/monitoring', label: 'Monitoring', icon: '📊' },
]
</script>

<template>
  <aside class="sidebar">
    <div class="sidebar-header">
      <h1>Knowledge Base</h1>
      <p>Agent + RAG System</p>
    </div>
    <nav class="sidebar-nav">
      <router-link
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: route.path === item.path }"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        {{ item.label }}
      </router-link>
    </nav>
    <ChatHistory v-if="route.path.startsWith('/chat')" class="sidebar-history" />
    <MultiAgentChatHistory v-else-if="route.path.startsWith('/multi-agent')" class="sidebar-history" />
    <div class="sidebar-bottom">
      <PermissionDialog />
      <div class="sidebar-footer">
        v0.1.0
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar-bottom {
  flex-shrink: 0;
}
</style>
