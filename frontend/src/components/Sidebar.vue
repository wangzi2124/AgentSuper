<script setup lang="ts">
import { useRoute } from 'vue-router'
import ChatHistory from './ChatHistory.vue'

const route = useRoute()

const navItems = [
  { path: '/chat', label: 'Chat', icon: '💬' },
  { path: '/documents', label: 'Documents', icon: '📄' },
  { path: '/skills', label: 'Skills', icon: '🧠' },
  { path: '/plugins', label: 'Plugins', icon: '🔌' },
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
    <ChatHistory v-if="route.path === '/chat'" class="sidebar-history" />
    <div class="sidebar-footer">
      v0.1.0
    </div>
  </aside>
</template>
