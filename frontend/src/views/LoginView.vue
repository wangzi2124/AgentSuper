<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()

const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirmPassword = ref('')
const error = ref('')
const submitting = ref(false)

watch(mode, () => {
  error.value = ''
})

async function submit() {
  const name = username.value.trim()
  const pw = password.value
  error.value = ''

  if (!name) { error.value = '请输入用户名'; return }
  if (!pw) { error.value = '请输入密码'; return }

  submitting.value = true
  try {
    if (mode.value === 'register') {
      if (pw !== confirmPassword.value) {
        error.value = '两次输入的密码不一致'
        return
      }
      await auth.register(name, pw)
    } else {
      await auth.login(name, pw)
    }
    router.push({ name: 'Chat' })
  } catch (e: any) {
    error.value = e?.message || '操作失败，请重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-view">
    <div class="login-card">
      <div class="login-logo">🧠</div>
      <h1 class="login-title">Knowledge Base</h1>
      <p class="login-subtitle">Agent + RAG System</p>

      <div class="tabs">
        <button class="tab" :class="{ active: mode === 'login' }" @click="mode = 'login'">登录</button>
        <button class="tab" :class="{ active: mode === 'register' }" @click="mode = 'register'">注册</button>
      </div>

      <form class="login-form" @submit.prevent="submit">
        <label class="field">
          <span class="field-label">用户名</span>
          <input
            v-model="username"
            class="field-input"
            placeholder="3-32 位，字母/数字/_/-"
            :disabled="submitting"
            autocomplete="username"
          />
        </label>
        <label class="field">
          <span class="field-label">密码</span>
          <input
            v-model="password"
            type="password"
            class="field-input"
            placeholder="至少 6 位"
            :disabled="submitting"
            autocomplete="current-password"
          />
        </label>
        <label v-if="mode === 'register'" class="field">
          <span class="field-label">确认密码</span>
          <input
            v-model="confirmPassword"
            type="password"
            class="field-input"
            placeholder="再次输入密码"
            :disabled="submitting"
            autocomplete="new-password"
          />
        </label>

        <p v-if="error" class="login-error">{{ error }}</p>

        <button class="login-btn" type="submit" :disabled="submitting">
          {{ submitting ? '请稍候...' : (mode === 'login' ? '登录' : '注册并登录') }}
        </button>
      </form>

      <p class="login-hint">
        {{ mode === 'login' ? '还没有账号？' : '已有账号？' }}
        <a href="#" @click.prevent="mode = mode === 'login' ? 'register' : 'login'">
          {{ mode === 'login' ? '去注册' : '去登录' }}
        </a>
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-view {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg);
  padding: 24px;
}
.login-card {
  width: 380px;
  max-width: 100%;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: 0 12px 40px rgba(0, 0, 0, 0.12);
  padding: 32px 28px;
}
.login-logo {
  text-align: center;
  font-size: 44px;
}
.login-title {
  text-align: center;
  margin: 12px 0 4px;
  font-size: 22px;
  color: var(--text);
}
.login-subtitle {
  text-align: center;
  margin: 0 0 24px;
  font-size: 13px;
  color: var(--text-secondary);
}
.tabs {
  display: flex;
  gap: 4px;
  padding: 4px;
  background: var(--bg);
  border-radius: 10px;
  margin-bottom: 20px;
}
.tab {
  flex: 1;
  padding: 8px 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.tab.active {
  background: var(--surface);
  color: var(--text);
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
}
.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-label {
  font-size: 12px;
  color: var(--text-secondary);
}
.field-input {
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  font-size: 14px;
  background: var(--surface);
  color: var(--text);
  outline: none;
  transition: border-color 0.15s;
}
.field-input:focus {
  border-color: var(--primary);
}
.login-error {
  margin: 0;
  font-size: 13px;
  color: var(--danger, #ef4444);
}
.login-btn {
  padding: 11px 0;
  border: none;
  border-radius: 8px;
  background: var(--primary, #4f46e5);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.15s;
}
.login-btn:hover { opacity: 0.9; }
.login-btn:disabled { opacity: 0.55; cursor: not-allowed; }
.login-hint {
  text-align: center;
  margin: 18px 0 0;
  font-size: 13px;
  color: var(--text-secondary);
}
.login-hint a {
  color: var(--primary, #4f46e5);
  text-decoration: none;
}
.login-hint a:hover { text-decoration: underline; }
</style>
