import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  getAuthInitInfo,
  loginAccount,
  registerAccount,
  logout as logoutLocal,
} from '../api/auth'

// 登录态管理：auth 启用（AUTH_TOKEN_SECRET）时控制应用是否放行。
export const useAuthStore = defineStore('auth', () => {
  const enabled = ref(false)
  const ready = ref(false)
  const user_id = ref('')
  const username = ref('')
  const accountType = ref<'account' | 'device' | ''>('')
  const busy = ref(false)

  const isLoggedIn = computed(() => enabled.value && !!user_id.value)

  // 启动初始化：探测后端是否启用身份签名；启用时校验/恢复本地会话。
  // 与 api/auth.ts 共享同一初始化 Promise（doInit），避免重复请求 /me。
  async function init() {
    if (ready.value) return
    const info = await getAuthInitInfo()
    enabled.value = info.enabled
    if (info.session) {
      user_id.value = info.session.user_id
      username.value = info.session.username || info.session.user_id
      accountType.value = info.session.account_type
    } else {
      // 无有效会话 → 保持未登录（不回落 anonymous），强制走登录页
      user_id.value = ''
      username.value = ''
      accountType.value = ''
    }
    ready.value = true
  }

  async function login(u: string, p: string) {
    busy.value = true
    try {
      const data = await loginAccount(u, p)
      user_id.value = data.user_id
      username.value = data.username || data.user_id
      accountType.value = 'account'
      return data
    } finally {
      busy.value = false
    }
  }

  async function register(u: string, p: string) {
    busy.value = true
    try {
      const data = await registerAccount(u, p)
      user_id.value = data.user_id
      username.value = data.username || data.user_id
      accountType.value = 'account'
      return data
    } finally {
      busy.value = false
    }
  }

  function logout() {
    logoutLocal()
    user_id.value = ''
    username.value = ''
    accountType.value = ''
  }

  return {
    enabled, ready, user_id, username, accountType, busy, isLoggedIn,
    init, login, register, logout,
  }
})
