<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import SettingsPanel from './SettingsPanel.vue'
import { useAuthStore } from '../stores/auth'
import { useThemeStore, BG_VARIANTS } from '../stores/theme'
import { applyCustomTheme, checkContrast, saveCustomTheme, validateTheme } from '../themes/custom'
import { fetchStats, fetchUsage, type UserUsage } from '../api/monitor'
import type { MonitorStats } from '../types'

const router = useRouter()
const auth = useAuthStore()
const theme = useThemeStore()

const root = ref<HTMLElement | null>(null)
const open = ref(false)
const nickDraft = ref('')
const usage = ref<UserUsage | null>(null)
const stats = ref<MonitorStats | null>(null)
const statsLoading = ref(false)
const usageLoaded = ref(false)

const showUser = auth.enabled ? auth.isLoggedIn : true

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

const AVATAR_PRESETS = ['🧑‍💻', '🤖', '🦊', '🐱', '🌟', '🎨']

async function refreshUsage() {
  statsLoading.value = true
  try {
    const [u, s] = await Promise.all([fetchUsage(), fetchStats()])
    usage.value = u
    stats.value = s
    usageLoaded.value = true
  } catch {
    /* 接口暂不可用时静默 */
  } finally {
    statsLoading.value = false
  }
}

function toggle() {
  open.value = !open.value
  if (open.value) {
    nickDraft.value = auth.nickname
    if (!usageLoaded.value) void refreshUsage()
  }
}

function close() {
  open.value = false
}

function saveNick() {
  auth.setNickname(nickDraft.value)
  close()
}

function resetAvatar() {
  auth.setAvatar('')
}

async function onThemeFile(e: Event) {
  const input = e.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  try {
    const parsed = JSON.parse(await file.text())
    const result = validateTheme(parsed)
    if (!result.theme) {
      window.alert(result.error || '主题格式无效')
      return
    }
    applyCustomTheme(result.theme)
    saveCustomTheme(result.theme)
    const warnings = checkContrast(result.theme)
    if (warnings.length) window.alert(warnings.join('\n'))
  } catch {
    window.alert('主题 JSON 解析失败')
  }
}

function onAvatarFile(e: Event) {  const input = e.target as HTMLInputElement
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
  close()
  router.push('/monitoring')
}

function handleLogout() {
  close()
  auth.logout()
  router.push({ name: 'Login' })
}

function onDocClick(e: MouseEvent) {
  if (!open.value) return
  if (!root.value?.contains(e.target as Node)) close()
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') close()
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <div v-if="showUser" ref="root" class="user-menu">
    <button class="um-trigger" :class="{ on: open }" title="系统设置" aria-label="系统设置" @click.stop="toggle">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.6 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>
      </svg>
    </button>

    <div v-if="open" class="um-panel" @click.stop>
      <div class="um-head">
        <div class="um-identity">
          <img v-if="isImgAvatar(auth.avatar)" class="um-avatar lg" :src="auth.avatar" alt="avatar" />
          <span v-else class="um-avatar lg">{{ auth.avatar || initialOf() }}</span>
          <div class="um-identity-text">
            <span class="um-name" :title="auth.displayName">{{ auth.displayName }}</span>
            <span class="um-id" :title="auth.user_id || 'local'">{{ auth.user_id || 'local' }}</span>
          </div>
        </div>
        <div class="um-head-actions">
          <button class="um-icon-btn" title="关闭" @click="close">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M4 4l8 8M12 4l-8 8"/></svg>
          </button>
          <button v-if="auth.enabled && auth.isLoggedIn" class="um-icon-btn danger" title="退出登录" @click="handleLogout">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 14H3a1 1 0 0 1-1-1V3a1 1 0 0 1 1-1h3"/><path d="M10.5 11 14 8l-3.5-3"/><path d="M14 8H6"/></svg>
          </button>
        </div>
      </div>

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
        <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M2 2v12h12"/><path d="M4.5 9.5 7 7l2 2 3.5-4"/></svg>
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

      <div class="pp-sep"><span>偏好设置</span></div>
      <div class="pp-field">
        <span class="pp-label">背景颜色</span>
        <div class="pp-bg-row">
          <button
            v-for="v in BG_VARIANTS"
            :key="v.value"
            class="pp-bg-swatch"
            :class="{ on: theme.bgVariant === v.value }"
            :title="v.label"
            :style="{ '--sw': v.swatch, '--sw-glow': v.glowSwatch || v.swatch }"
            @click="theme.setBg(v.value)"
          ><span></span></button>
        </div>
        <label class="pp-preset pp-upload" title="导入主题 JSON" style="margin-top: 8px">
          导入主题
          <input type="file" accept="application/json,.json" style="display:none" @change="onThemeFile" />
        </label>
      </div>

      <SettingsPanel @close="close" />

      <div class="pp-actions">
        <button class="pp-btn" @click="resetAvatar">重置</button>
        <button class="pp-btn pp-btn-primary" @click="saveNick">保存</button>
      </div>
    </div>
  </div>
</template>

<style scoped src="../styles/userMenu.css"></style>
