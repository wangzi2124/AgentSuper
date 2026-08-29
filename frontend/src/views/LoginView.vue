<script setup lang="ts">
import { ref, watch, computed } from 'vue'
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
const showPassword = ref(false)
const showConfirm = ref(false)

watch(mode, () => {
  error.value = ''
})

const title = computed(() => (mode.value === 'login' ? '欢迎回来' : '创建账号'))
const subtitle = computed(() =>
  mode.value === 'login' ? '登录以继续使用你的知识库' : '注册一个账号，开始搭建智能知识库'
)

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
    router.push({ name: 'MultiAgent' })
  } catch (e: any) {
    error.value = e?.message || '操作失败，请重试'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login-view">
    <div class="login-shell">
      <!-- 品牌区（成熟产品分屏风格，移动端隐藏） -->
      <aside class="brand-panel">
        <div class="brand-bg brand-bg-1"></div>
        <div class="brand-bg brand-bg-2"></div>
        <div class="brand-bg brand-bg-3"></div>
        <div class="brand-inner">
          <div class="brand-logo">
            <div class="brand-logo-mark">🧠</div>
            <div>
              <div class="brand-logo-name">知识库</div>
              <div class="brand-logo-en">Agent + RAG SYSTEM</div>
            </div>
          </div>
          <div class="brand-block">
            <h2 class="brand-title">让智能体读懂你的知识库</h2>
            <p class="brand-desc">
              多智能体协作 · 本地向量检索 · 技能与插件编排，一站式构建你的 AI 知识工作台。
            </p>
          </div>
          <ul class="brand-points">
            <li>
              <span class="brand-check">✓</span>
              <span>多智能体分工路由，Web 检索 / 代码 / RAG 协同工作</span>
            </li>
            <li>
              <span class="brand-check">✓</span>
              <span>文档上传即用，混合检索 + 重排序保证答案质量</span>
            </li>
            <li>
              <span class="brand-check">✓</span>
              <span>对话记录云端保存，随时继续之前的工作</span>
            </li>
          </ul>
        </div>
        <div class="brand-footer">© {{ new Date().getFullYear() }} AgentSuper</div>
      </aside>

      <!-- 表单区 -->
      <div class="form-panel">
        <div class="form-inner">
          <div class="form-logo-mobile">
            <span class="form-logo-mark">🧠</span>
            <span class="form-logo-name">知识库</span>
          </div>

          <div class="form-head">
            <h1 class="form-title">{{ title }}</h1>
            <p class="form-subtitle">{{ subtitle }}</p>
          </div>

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
              <span class="field-input-wrap">
                <input
                  v-model="password"
                  :type="showPassword ? 'text' : 'password'"
                  class="field-input"
                  placeholder="至少 6 位"
                  :disabled="submitting"
                  autocomplete="current-password"
                />
                <button
                  type="button"
                  class="field-eye"
                  :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                  @click="showPassword = !showPassword"
                >
                  <svg v-if="!showPassword" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                  <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
                </button>
              </span>
            </label>
            <label v-if="mode === 'register'" class="field">
              <span class="field-label">确认密码</span>
              <span class="field-input-wrap">
                <input
                  v-model="confirmPassword"
                  :type="showConfirm ? 'text' : 'password'"
                  class="field-input"
                  placeholder="再次输入密码"
                  :disabled="submitting"
                  autocomplete="new-password"
                />
                <button
                  type="button"
                  class="field-eye"
                  :aria-label="showConfirm ? '隐藏密码' : '显示密码'"
                  @click="showConfirm = !showConfirm"
                >
                  <svg v-if="!showConfirm" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                  <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
                </button>
              </span>
            </label>

            <div v-if="error" class="login-error">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
              {{ error }}
            </div>

            <button class="login-btn" type="submit" :disabled="submitting">
              <span v-if="submitting" class="spinner"></span>
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
    </div>
  </div>
</template>

<style scoped>
.login-view {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background:
    url('/login-bg.svg') no-repeat center / cover,
    linear-gradient(160deg, #1e1b4b, #312e81);
}

.login-shell {
  width: 920px;
  max-width: 100%;
  display: grid;
  grid-template-columns: 1.05fr 1fr;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  overflow: hidden;
  box-shadow: 0 24px 70px rgba(15, 23, 42, 0.16);
}

/* ── 品牌区 ── */
.brand-panel {
  position: relative;
  overflow: hidden;
  padding: 40px 36px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  color: #fff;
  background: linear-gradient(160deg, #4338ca 0%, #4f46e5 45%, #6d5ef1 100%);
}
.brand-bg {
  position: absolute;
  border-radius: 50%;
  filter: blur(30px);
  opacity: 0.5;
}
.brand-bg-1 { width: 320px; height: 320px; top: -90px; left: -70px; background: #8b5cf6; }
.brand-bg-2 { width: 260px; height: 260px; bottom: -60px; right: -50px; background: #38bdf8; opacity: 0.4; }
.brand-bg-3 { width: 160px; height: 160px; top: 40%; right: 12%; background: #a78bfa; opacity: 0.35; }
.brand-inner { position: relative; z-index: 1; }
.brand-logo { display: flex; align-items: center; gap: 12px; }
.brand-logo-mark {
  width: 44px;
  height: 44px;
  border-radius: 13px;
  background: rgba(255, 255, 255, 0.18);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.18);
}
.brand-logo-name { font-size: 20px; font-weight: 700; letter-spacing: 0.5px; }
.brand-logo-en { font-size: 10px; opacity: 0.7; letter-spacing: 2px; margin-top: 2px; }
.brand-block { margin-top: 56px; }
.brand-title { font-size: 30px; line-height: 1.25; font-weight: 700; max-width: 300px; }
.brand-desc { margin-top: 14px; font-size: 14px; line-height: 1.7; opacity: 0.85; max-width: 320px; }
.brand-points { list-style: none; margin-top: 32px; display: flex; flex-direction: column; gap: 14px; padding: 0; }
.brand-points li { display: flex; align-items: flex-start; gap: 10px; font-size: 13px; line-height: 1.6; opacity: 0.92; }
.brand-check {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  margin-top: 1px;
}
.brand-footer { position: relative; z-index: 1; font-size: 11px; opacity: 0.6; margin-top: 32px; }

/* ── 表单区 ── */
.form-panel { display: flex; align-items: center; justify-content: center; padding: 40px 32px; }
.form-inner { width: 100%; max-width: 340px; }
.form-logo-mobile { display: none; }
.form-head { margin-bottom: 22px; }
.form-title { font-size: 24px; font-weight: 700; color: var(--text); }
.form-subtitle { margin-top: 6px; font-size: 13px; color: var(--text-secondary); }

.tabs {
  display: flex;
  gap: 4px;
  padding: 4px;
  background: var(--bg);
  border-radius: 11px;
  margin-bottom: 22px;
}
.tab {
  flex: 1;
  padding: 9px 0;
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
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.12);
}

.login-form { display: flex; flex-direction: column; gap: 14px; }
.field { display: flex; flex-direction: column; gap: 6px; }
.field-label { font-size: 12px; font-weight: 500; color: var(--text-secondary); }
.field-input-wrap { position: relative; display: block; }
.field-input {
  width: 100%;
  padding: 11px 38px 11px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  font-size: 14px;
  background: var(--surface);
  color: var(--text);
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.field-input:focus {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary, #4f46e5) 14%, transparent);
}
.field-input:disabled { opacity: 0.6; cursor: not-allowed; }
.field-eye {
  position: absolute;
  right: 6px;
  top: 50%;
  transform: translateY(-50%);
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: color 0.15s, background 0.15s;
}
.field-eye:hover { color: var(--text); background: var(--bg); }

.login-error {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 0;
  padding: 9px 12px;
  font-size: 13px;
  color: var(--danger, #ef4444);
  background: color-mix(in srgb, var(--danger, #ef4444) 8%, var(--surface));
  border: 1px solid color-mix(in srgb, var(--danger, #ef4444) 25%, var(--border));
  border-radius: 9px;
}
.login-error svg { flex-shrink: 0; }

.login-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  margin-top: 2px;
  padding: 12px 0;
  border: none;
  border-radius: 10px;
  background: linear-gradient(135deg, #6d5ef1 0%, #8b5cf6 55%, #38bdf8);
  background-size: 150% 150%;
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(109, 94, 241, 0.32);
  transition: background-position 0.3s, opacity 0.15s, transform 0.1s;
}
.login-btn:hover { background-position: 100% 100%; }
.login-btn:active { transform: translateY(1px); }
.login-btn:disabled { opacity: 0.6; cursor: not-allowed; box-shadow: none; }
.spinner {
  width: 15px;
  height: 15px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: login-spin 0.7s linear infinite;
}
@keyframes login-spin { to { transform: rotate(360deg); } }

.login-hint {
  text-align: center;
  margin: 20px 0 0;
  font-size: 13px;
  color: var(--text-secondary);
}
.login-hint a { color: var(--primary, #4f46e5); font-weight: 500; }
.login-hint a:hover { text-decoration: underline; }

/* ── 移动端（≤768px）：单栏，品牌区收为顶部小标 ── */
@media (max-width: 768px) {
  .login-view { padding: 0; }
  .login-shell {
    grid-template-columns: 1fr;
    width: 100%;
    height: 100%;
    min-height: 100vh;
    border: none;
    border-radius: 0;
    box-shadow: none;
    background: var(--bg);
    display: flex;
    flex-direction: column;
  }
  .brand-panel { display: none; }
  .form-panel {
    flex: 1;
    align-items: flex-start;
    justify-content: center;
    padding: 48px 24px 32px;
  }
  .form-inner { max-width: 400px; margin: 0 auto; }
  .form-logo-mobile {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    margin-bottom: 28px;
  }
  .form-logo-mark {
    width: 40px;
    height: 40px;
    border-radius: 12px;
    background: linear-gradient(135deg, #6d5ef1, #8b5cf6);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
  }
  .form-logo-name { font-size: 18px; font-weight: 700; color: var(--text); }
  .form-head { text-align: center; }
  .form-title { font-size: 22px; }
  .login-btn { padding: 13px 0; }
}
</style>
