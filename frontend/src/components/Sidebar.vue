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
    <div class="sidebar-header">
      <h1>知识库</h1>
      <p>Agent + RAG 系统</p>
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
    <MultiAgentChatHistory class="sidebar-history" />
    <div class="sidebar-bottom">
      <PermissionDialog />
      <div v-if="auth.enabled && auth.isLoggedIn" class="user-card">
        <span class="user-avatar">{{ (auth.username || '?').slice(0, 1).toUpperCase() }}</span>
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

<style scoped>
.sidebar-toggle {
  display: none;
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: 101;
  width: 40px;
  height: 40px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
  cursor: pointer;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow);
}
@media (max-width: 768px) {
  .sidebar-toggle { display: flex; }
}
.sidebar-bottom {
  flex-shrink: 0;
}
.user-card {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
}
.user-avatar {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--primary, #4f46e5);
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.user-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}
.user-name {
  font-size: 13px;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-id {
  font-size: 10px;
  color: var(--text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'Cascadia Code', Consolas, monospace;
}
.logout-btn {
  width: 26px;
  height: 26px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all 0.15s;
}
.logout-btn:hover {
  background: rgba(239, 68, 68, 0.1);
  color: #ef4444;
}
</style>
