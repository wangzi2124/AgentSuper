<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MultiAgentChatHistory from './MultiAgentChatHistory.vue'
import PermissionDialog from './PermissionDialog.vue'
import { useAuthStore } from '../stores/auth'
import { useThemeStore } from '../stores/theme'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()

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

      <!-- Theme Toggle -->
      <div class="theme-row">
        <button class="theme-toggle" @click="theme.toggle()" :title="theme.isDark ? '切换到浅色模式' : '切换到深色模式'">
          <svg v-if="theme.isDark" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
          </svg>
          <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
          </svg>
          <span>{{ theme.isDark ? '浅色模式' : '深色模式' }}</span>
        </button>
      </div>

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

<style scoped>
.sidebar-toggle {
  display: none;
  position: fixed;
  top: 14px;
  left: 14px;
  z-index: 101;
  width: 42px;
  height: 42px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface-glass);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  color: var(--text);
  cursor: pointer;
  align-items: center;
  justify-content: center;
  box-shadow: var(--shadow-md);
  transition: all var(--duration) var(--ease);
}
.sidebar-toggle:hover {
  box-shadow: var(--shadow-lg);
  border-color: var(--primary);
}
@media (max-width: 768px) {
  .sidebar-toggle { display: flex; }
}

.sidebar-bottom {
  flex-shrink: 0;
}

/* Brand */
.brand-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand-mark {
  width: 40px;
  height: 40px;
  border-radius: var(--radius);
  background: linear-gradient(135deg, var(--primary), color-mix(in srgb, var(--primary) 60%, var(--accent)));
  display: flex;
  align-items: center;
  justify-content: center;
  color: white;
  box-shadow: 0 4px 12px var(--primary-glow);
  flex-shrink: 0;
}
.brand-text h1 {
  font-size: 17px;
  font-weight: 800;
  letter-spacing: -0.02em;
}
.brand-text p {
  font-size: 10px;
  color: var(--text-muted);
  margin-top: 2px;
  font-weight: 500;
  letter-spacing: 0.04em;
}

/* Theme Toggle */
.theme-row {
  padding: 6px 10px;
}
.theme-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-subtle);
  color: var(--text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--duration) var(--ease);
}
.theme-toggle:hover {
  background: var(--surface);
  color: var(--text);
  border-color: color-mix(in srgb, var(--primary) 30%, var(--border));
}

/* User Card */
.user-card {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 6px 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  background: var(--bg-subtle);
  transition: all var(--duration) var(--ease);
}
.user-card:hover {
  background: var(--surface);
  border-color: color-mix(in srgb, var(--primary) 20%, var(--border));
}
.user-avatar {
  width: 32px;
  height: 32px;
  border-radius: var(--radius);
  background: linear-gradient(135deg, var(--primary), var(--accent));
  color: #fff;
  font-size: 13px;
  font-weight: 700;
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
  font-weight: 600;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.user-id {
  font-size: 10px;
  color: var(--text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: 'JetBrains Mono', Consolas, monospace;
}
.logout-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: all var(--duration) var(--ease);
}
.logout-btn:hover {
  background: var(--danger-soft);
  color: var(--danger);
}
</style>
