import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import {
  isAuthEnabled,
  loginAccount,
  registerAccount,
  fetchMe,
  logout as logoutLocal,
  hasStoredSession,
  getUserId,
  getUsername,
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

  // 启动初始化：探测后端是否启用身份签名；启用时校验已有本地会话。
  async function init() {
    if (ready.value) return
    try {
      enabled.value = await isAuthEnabled()
    } catch {
      enabled.value = false
    }
    if (!enabled.value) {
      ready.value = true
      return
    }
    if (hasStoredSession()) {
      user_id.value = getUserId()
      username.value = getUsername() || user_id.value
      try {
        const me = await fetchMe()
        user_id.value = me.user_id
        username.value = me.username || me.user_id
        accountType.value = me.account_type
      } catch {
        // token 失效/用户不存在 → 清空本地会话，引导重新登录
        logoutLocal()
        user_id.value = ''
        username.value = ''
        accountType.value = ''
      }
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
