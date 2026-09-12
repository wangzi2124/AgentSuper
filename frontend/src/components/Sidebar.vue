<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MultiAgentChatHistory from './MultiAgentChatHistory.vue'
import PermissionDialog from './PermissionDialog.vue'
import { useAuthStore } from '../stores/auth'
import { fetchStats, fetchUsage, type UserUsage } from '../api/monitor'
import type { MonitorStats } from '../types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const sidebarOpen = ref(false)

// 桌面收起（仅图标模式），记忆到 localStorage；移动端仍走抽屉，不受影响
const sidebarCollapsed = ref(false)
try { sidebarCollapsed.value = localStorage.getItem('agentsuper:sidebar-collapsed') === '1' } catch { /* ignore */ }
function toggleCollapsed() {
  sidebarCollapsed.value = !sidebarCollapsed.value
  try { localStorage.setItem('agentsuper:sidebar-collapsed', sidebarCollapsed.value ? '1' : '0') } catch { /* ignore */ }
}

const navItems = [
  { path: '/multi-agent', label: '多智能体', icon: '🤖' },
  { path: '/documents', label: '文档管理', icon: '📄' },
  { path: '/skills', label: '技能', icon: '🧠' },
  { path: '/plugins', label: '插件', icon: '🔌' },
  { path: '/custom-tools', label: '自定义工具', icon: '🧰' },
  { path: '/vectors', label: '向量库', icon: '🔢' },
  { path: '/generated', label: '生成文件', icon: '📝' },
  { path: '/monitoring', label: '系统监控', icon: '📊' },
  { path: '/model-manager', label: '模型管理', icon: '🧬' },
]

// ── 用户卡：本地未启用登录也展示（匿名本地用户），登录后展示账号 ──
const showUserCard = computed(() => (auth.enabled ? auth.isLoggedIn : true))

const usage = ref<UserUsage | null>(null)
const stats = ref<MonitorStats | null>(null)
const statsLoading = ref(false)
const profileOpen = ref(false)
const nickDraft = ref('')

function fmt(n: number): string {
  const v = n || 0
  if (v >= 1e6) return (v / 1e6).toFixed(1) + 'M'
  if (v >= 1e3) return (v / 1e3).toFixed(1) + 'k'
  return String(v)
}

function fmtCost(v?: number): string {
  if (!v || v <= 0) return '免费'
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`
}

function isImgAvatar(v: string): boolean {
  return !!v && (v.startsWith('data:') || v.startsWith('http'))
}

function initialOf(): string {
  return (auth.displayName || '?').slice(0, 1).toUpperCase()
}

async function refreshUsage() {
  statsLoading.value = true
  try {
    const [u, s] = await Promise.all([fetchUsage(), fetchStats()])
    usage.value = u
    stats.value = s
  } catch {
    /* 忽略：接口暂不可用时静默 */
  } finally {
    statsLoading.value = false
  }
}

function toggleProfile() {
  profileOpen.value = !profileOpen.value
  if (profileOpen.value) {
    nickDraft.value = auth.nickname
    void refreshUsage()
  }
}

function saveNick() {
  auth.setNickname(nickDraft.value)
  profileOpen.value = false
}

function resetAvatar() {
  auth.setAvatar('')
}

const AVATAR_PRESETS = ['🧑‍💻', '🤖', '🦊', '🐱', '🌟', '🎨']

function onAvatarFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  if (file.size > 2 * 1024 * 1024) { window.alert('图片过大，请选择 2MB 以内的图片'); return }
  const reader = new FileReader()
  reader.onload = () => {
    const img = new Image()
    img.onload = () => {
      const max = 256
      let w = img.width
      let h = img.height
      if (w > max || h > max) {
        const s = Math.min(max / w, max / h)
        w = Math.round(w * s)
        h = Math.round(h * s)
      }
      const cv = document.createElement('canvas')
      cv.width = w
      cv.height = h
      const ctx = cv.getContext('2d')
      if (ctx) ctx.drawImage(img, 0, 0, w, h)
      auth.setAvatar(cv.toDataURL('image/png'))
    }
    img.src = String(reader.result)
  }
  reader.readAsDataURL(file)
}

function goMonitor() {
  profileOpen.value = false
  router.push('/monitoring')
}

onMounted(() => { void refreshUsage() })

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
  <aside class="sidebar" :class="{ open: sidebarOpen, collapsed: sidebarCollapsed }">
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
        <button class="sidebar-collapse" :title="sidebarCollapsed ? '展开侧边栏' : '收起侧边栏'" @click="toggleCollapsed">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
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
        :title="item.label"
      >
        <span class="nav-icon">{{ item.icon }}</span>
        <span class="nav-label">{{ item.label }}</span>
      </router-link>
    </nav>

    <!-- Chat History -->
    <MultiAgentChatHistory class="sidebar-history" />

    <!-- Bottom Section -->
    <div class="sidebar-bottom">
      <PermissionDialog />

      <!-- User Card：头像/昵称（用量/监控信息已移入个人设置面板） -->
      <div v-if="showUserCard" class="user-card">
        <div class="user-avatar-wrap">
          <img v-if="isImgAvatar(auth.avatar)" class="user-avatar user-avatar-img" :src="auth.avatar" alt="avatar" />
          <div v-else class="user-avatar">{{ auth.avatar || initialOf() }}</div>
        </div>
        <div class="user-meta">
          <span class="user-name" :title="auth.displayName">{{ auth.displayName }}</span>
          <span v-if="auth.enabled && auth.user_id" class="user-id" :title="auth.user_id">{{ auth.user_id }}</span>
          <span v-else class="user-id">local</span>
        </div>
        <button class="user-edit" title="编辑资料 / 用量与监控" @click="toggleProfile">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z"/></svg>
        </button>
        <button v-if="auth.enabled && auth.isLoggedIn" class="logout-btn" title="退出登录" @click="handleLogout">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
        </button>
      </div>

      <!-- 资料编辑弹层（含监控信息：不再在侧栏单独显示） -->
      <div v-if="showUserCard && profileOpen" class="profile-panel">
        <div class="pp-stats">
          <button class="pp-stat" type="button" title="我的累计输入 Token（跨会话）· 点击查看系统监控" @click="goMonitor">
            <span class="pp-stat-ico">↑</span>
            <span class="pp-stat-v">{{ usage ? fmt(usage.tokens_input) : (statsLoading ? '…' : '—') }}</span>
            <span class="pp-stat-k">输入 Token</span>
          </button>
          <button class="pp-stat" type="button" title="我的累计输出 Token（跨会话）· 点击查看系统监控" @click="goMonitor">
            <span class="pp-stat-ico">↓</span>
            <span class="pp-stat-v">{{ usage ? fmt(usage.tokens_output) : (statsLoading ? '…' : '—') }}</span>
            <span class="pp-stat-k">输出 Token</span>
          </button>
          <button class="pp-stat" type="button" title="我的累计对话轮数 · 点击查看系统监控" @click="goMonitor">
            <span class="pp-stat-ico">💬</span>
            <span class="pp-stat-v">{{ usage ? fmt(usage.requests) : (statsLoading ? '…' : '—') }}</span>
            <span class="pp-stat-k">对话轮数</span>
          </button>
          <button class="pp-stat" type="button" title="系统累计 HTTP 请求数 · 点击查看系统监控" @click="goMonitor">
            <span class="pp-stat-ico">📡</span>
            <span class="pp-stat-v">{{ stats ? fmt(stats.requests.total) : (statsLoading ? '…' : '—') }}</span>
            <span class="pp-stat-k">系统请求</span>
          </button>
          <button class="pp-stat" type="button" title="系统累计 LLM 调用数 · 点击查看系统监控" @click="goMonitor">
            <span class="pp-stat-ico">🧠</span>
            <span class="pp-stat-v">{{ stats ? fmt(stats.model_calls.total) : (statsLoading ? '…' : '—') }}</span>
            <span class="pp-stat-k">LLM 调用</span>
          </button>
          <button class="pp-stat" type="button" title="系统累计 LLM 成本 · 点击查看系统监控" @click="goMonitor">
            <span class="pp-stat-ico">💎</span>
            <span class="pp-stat-v">{{ stats ? fmtCost(stats.total_cost) : (statsLoading ? '…' : '—') }}</span>
            <span class="pp-stat-k">总成本</span>
          </button>
        </div>
        <button class="pp-monitor-link" @click="goMonitor">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="M7 14l3-3 3 3 5-6"/></svg>
          查看完整系统监控
        </button>
        <label class="pp-field">
          <span class="pp-label">昵称</span>
          <input v-model="nickDraft" class="pp-input" maxlength="30" placeholder="输入昵称" @keyup.enter="saveNick" />
        </label>
        <div class="pp-field">
          <span class="pp-label">头像</span>
          <div class="pp-avatar-row">
            <button v-for="p in AVATAR_PRESETS" :key="p" class="pp-preset" :class="{ on: auth.avatar === p }" @click="auth.setAvatar(p)">{{ p }}</button>
            <label class="pp-preset pp-upload" title="上传图片">
              图片
              <input type="file" accept="image/*" style="display:none" @change="onAvatarFile" />
            </label>
          </div>
        </div>
        <div class="pp-actions">
          <button class="pp-btn" @click="resetAvatar">重置</button>
          <button class="pp-btn pp-btn-primary" @click="saveNick">保存</button>
        </div>
      </div>

      <div class="sidebar-footer">
        v0.1.0
      </div>
    </div>
  </aside>
</template>


<style scoped src="../styles/chat/sidebar.css"></style>

<style scoped>
.user-card { display: flex; align-items: center; gap: 8px; padding: 10px 12px; }
.user-avatar-wrap { position: relative; flex-shrink: 0; }
.user-avatar-img { object-fit: cover; width: 32px; height: 32px; border-radius: 50%; }
.user-edit {
  margin-left: auto; display: inline-flex; align-items: center; justify-content: center;
  width: 24px; height: 24px; border: 1px solid var(--border-subtle); border-radius: 6px;
  background: transparent; color: var(--text-secondary); cursor: pointer;
}
.user-edit:hover { color: var(--primary); border-color: color-mix(in srgb, var(--primary) 40%, var(--border)); }
.logout-btn { flex-shrink: 0; }

.user-stats { display: flex; flex-direction: column; gap: 4px; padding: 6px 14px 8px; border-top: 1px dashed var(--border-subtle); }
.us-row {
  display: flex; justify-content: space-between; align-items: center; gap: 8px;
  font-size: 11px; line-height: 1.5; color: var(--text-secondary);
}
.us-row.clickable, .us-row:hover { cursor: pointer; }
.us-k { font-weight: 600; flex-shrink: 0; }
.us-v { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 11px; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.us-sub { font-style: normal; color: var(--text-secondary); margin-left: 4px; }

.profile-panel {
  position: absolute;
  left: 10px; right: 10px; bottom: 10px;
  z-index: 40;
  margin: 0;
  max-height: min(72vh, 560px);
  overflow-y: auto;
  padding: 10px; border: 1px solid var(--border-subtle);
  border-radius: 10px; background: var(--surface); box-shadow: var(--shadow-lg, 0 10px 40px rgba(0, 0, 0, 0.25));
  display: flex; flex-direction: column; gap: 10px;
}
.pp-field { display: flex; flex-direction: column; gap: 6px; }
.pp-label { font-size: 11px; font-weight: 600; color: var(--text-secondary); }
.pp-input {
  width: 100%; padding: 6px 8px; font-size: 12px; border: 1px solid var(--border-subtle);
  border-radius: 6px; background: var(--bg-subtle); color: var(--text); outline: none;
}
.pp-input:focus { border-color: color-mix(in srgb, var(--primary) 50%, var(--border)); }
.pp-avatar-row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.pp-preset {
  position: relative; width: 30px; height: 30px; border: 1px solid var(--border-subtle);
  border-radius: 8px; background: var(--bg-subtle); font-size: 16px; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; overflow: hidden;
}
.pp-preset.on { border-color: var(--primary); box-shadow: 0 0 0 1px var(--primary); }
.pp-upload { font-size: 11px; color: var(--text-secondary); width: auto; padding: 0 8px; }
.pp-upload:hover { color: var(--primary); }
.pp-actions { display: flex; justify-content: flex-end; gap: 8px; }
.pp-btn {
  font-size: 12px; padding: 5px 12px; border: 1px solid var(--border-subtle); border-radius: 6px;
  background: var(--bg-subtle); color: var(--text); cursor: pointer;
}
.pp-btn-primary { background: var(--primary); border-color: var(--primary); color: #fff; }
.pp-btn-primary:hover { filter: brightness(1.08); }

/* ── 个人设置内的监控信息方块（酷炫方块） ── */
.pp-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.pp-stat {
  position: relative;
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  padding: 8px 9px;
  border-radius: 10px;
  border: 1px solid color-mix(in srgb, var(--primary) 22%, var(--border-subtle));
  background:
    linear-gradient(135deg, color-mix(in srgb, var(--primary) 12%, transparent), transparent 70%),
    var(--bg-subtle);
  overflow: hidden;
  cursor: pointer;
  font: inherit;
  color: inherit;
  text-align: left;
  appearance: none;
  -webkit-appearance: none;
  transition: transform 0.18s var(--ease), box-shadow 0.18s var(--ease), border-color 0.18s var(--ease);
}
.pp-stat::after {
  content: '';
  position: absolute; inset: 0;
  background: radial-gradient(circle at 100% 0, var(--primary-glow), transparent 60%);
  opacity: 0.55; pointer-events: none;
}
.pp-stat:hover {
  transform: translateY(-2px);
  border-color: color-mix(in srgb, var(--primary) 45%, var(--border));
  box-shadow: 0 6px 16px var(--primary-glow);
}
.pp-stat-ico { font-size: 12px; line-height: 1; color: var(--primary); opacity: 0.9; }
.pp-stat-v {
  font-size: 15px; font-weight: 800; letter-spacing: -0.02em;
  color: var(--text); font-variant-numeric: tabular-nums;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%;
}
.pp-stat-k { font-size: 9.5px; font-weight: 600; color: var(--text-secondary); letter-spacing: 0.02em; }
.pp-monitor-link {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  padding: 7px 10px; border-radius: 9px; cursor: pointer;
  border: 1px solid color-mix(in srgb, var(--primary) 30%, var(--border-subtle));
  background: color-mix(in srgb, var(--primary) 8%, var(--surface));
  color: var(--primary); font-size: 12px; font-weight: 600;
  transition: all var(--duration) var(--ease);
}
.pp-monitor-link:hover { background: color-mix(in srgb, var(--primary) 16%, var(--surface)); box-shadow: 0 4px 14px var(--primary-glow); }
</style>
