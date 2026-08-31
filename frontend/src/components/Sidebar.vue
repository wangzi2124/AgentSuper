<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MultiAgentChatHistory from './MultiAgentChatHistory.vue'
import PermissionDialog from './PermissionDialog.vue'
import { useAuthStore } from '../stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const sidebarOpen = ref(false)

const navItems = [
  { path: '/multi-agent', label: '多智能体', icon: '🤖' },
  { path: '/documents', label: '文档管理', icon: '📄' },
  { path: '/skills', label: '技能', icon: '🧠' },
  { path: '/plugins', label: '插件', icon: '🔌' },
  { path: '/custom-tools', label: '自定义工具', icon: '🧰' },
  { path: '/vectors', label: '向量库', icon: '🔢' },
  { path: '/generated', label: '生成文件', icon: '📝' },
  { path: '/monitoring', label: '系统监控', icon: '📊' },
]

watch(() => route.path, () => { sidebarOpen.value = false })

function handleLogout() {
  auth.logout()
  router.push({ name: 'Login' })
}
</script>

<template>
  <button class="sidebar-toggle" @click="sidebarOpen = !sidebarOpen">
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
  </button>
  <div v-if="sidebarOpen" class="sidebar-overlay" @click="sidebarOpen = false"></div>
  <aside class="sidebar" :class="{ open: sidebarOpen }">
    <!-- Brand Header -->
    <div class="sidebar-header">
      <div class="brand-row">
        <div class="brand-mark">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2L2 7l10 5 10-5-10-5z"/>
            <path d="M2 17l10 5 10-5"/>
            <path d="M2 12l10 5 10-5"/>
          </svg>
        </div>
        <div class="brand-text">
          <h1>AgentSuper</h1>
          <p>RAG · 多智能体</p>
        </div>
      </div>
    </div>

    <!-- Navigation -->
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

    <!-- Chat History -->
    <MultiAgentChatHistory class="sidebar-history" />

    <!-- Bottom Section -->
    <div class="sidebar-bottom">
      <PermissionDialog />

      <!-- User Card -->
      <div v-if="auth.enabled && auth.isLoggedIn" class="user-card">
        <div class="user-avatar">
          {{ (auth.username || '?').slice(0, 1).toUpperCase() }}
        </div>
        <div class="user-meta">
          <span class="user-name" :title="auth.username">{{ auth.username }}</span>
          <span class="user-id" :title="auth.user_id">{{ auth.user_id }}</span>
        </div>
        <button class="logout-btn" title="退出登录" @click="handleLogout">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        </button>
      </div>

      <div class="sidebar-footer">
        v0.1.0
      </div>
    </div>
  </aside>
</template>


<style scoped src="../styles/chat/sidebar.css"></style>
