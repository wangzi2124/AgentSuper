<script setup lang="ts">
import { ref, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import MobileChat from './views/MobileChat.vue'
import MobileDocuments from './views/MobileDocuments.vue'
import MobileSkills from './views/MobileSkills.vue'
import MobilePlugins from './views/MobilePlugins.vue'
import MobileVectors from './views/MobileVectors.vue'
import MobileGenerated from './views/MobileGenerated.vue'
import MobileMonitoring from './views/MobileMonitoring.vue'
import MobileCustomTools from './views/MobileCustomTools.vue'

const route = useRoute()
const router = useRouter()

// 底部 TabBar 主入口（最多 5 个）
const tabs = [
  { name: 'MultiAgent', path: '/multi-agent', title: '聊天', icon: 'chat-o' },
  { name: 'Documents', path: '/documents', title: '文档', icon: 'description' },
  { name: 'Skills', path: '/skills', title: '技能', icon: 'star-o' },
  { name: 'Plugins', path: '/plugins', title: '插件', icon: 'apps-o' },
  { name: 'Vectors', path: '/vectors', title: '向量库', icon: 'cluster-o' },
]

// 全站导航（用于顶部菜单抽屉）：彩色语义图标体系（color = 图标色，soft = 容器底色）
const menu = [
  { name: 'MultiAgent', path: '/multi-agent', title: '多智能体', vanIcon: 'chat-o', color: '#6d5ef1', soft: 'rgba(109,94,241,.12)' },
  { name: 'Documents', path: '/documents', title: '文档管理', vanIcon: 'description', color: '#3b82f6', soft: 'rgba(59,130,246,.12)' },
  { name: 'Skills', path: '/skills', title: '技能', vanIcon: 'star-o', color: '#8b5cf6', soft: 'rgba(139,92,246,.12)' },
  { name: 'Plugins', path: '/plugins', title: '插件', vanIcon: 'apps-o', color: '#06b6d4', soft: 'rgba(6,182,212,.12)' },
  { name: 'CustomTools', path: '/custom-tools', title: '自定义工具', vanIcon: 'setting-o', color: '#ec4899', soft: 'rgba(236,72,153,.12)' },
  { name: 'Vectors', path: '/vectors', title: '向量库', vanIcon: 'cluster-o', color: '#10b981', soft: 'rgba(16,185,129,.12)' },
  { name: 'Generated', path: '/generated', title: '生成文件', vanIcon: 'file-o', color: '#f97316', soft: 'rgba(249,115,22,.12)' },
  { name: 'Monitoring', path: '/monitoring', title: '系统监控', vanIcon: 'chart-trending-o', color: '#f59e0b', soft: 'rgba(245,158,11,.12)' },
]

// 当前激活的 TabBar 项
const activeTab = computed(() => tabs.find(t => route.path.startsWith(t.path))?.name || '')

// 当前页标题
const currentTitle = computed(() => menu.find(m => route.path.startsWith(m.path))?.title || '')

// 顶部菜单抽屉
const showMenu = ref(false)
function go(path: string) {
  showMenu.value = false
  router.push(path)
}

// 移动版页面分发：按 route.name 返回对应移动组件，未覆盖的页面 fallback 到原视图
const mobileViews: Record<string, unknown> = {
  MultiAgent: MobileChat,
  MultiAgentConversation: MobileChat,
  Documents: MobileDocuments,
  Skills: MobileSkills,
  Plugins: MobilePlugins,
  Vectors: MobileVectors,
  Generated: MobileGenerated,
  Monitoring: MobileMonitoring,
  CustomTools: MobileCustomTools,
}
  /* @@CHAT_PANEL_SCRIPT@@ */
  // ── 设置分组（移动端聊天对齐 ChatGPT：头部控件收纳进左侧抽屉） ──
  import { useMultiAgentStore } from '../stores/multiAgent'
  import { usePermissionStore } from '../stores/permission'
  import { SUPPORTED_MODELS } from '../config/models'

  const agent = useMultiAgentStore()
  const perm = usePermissionStore()

  // 工作目录摘要（过长省略展示）
  const workspacesText = computed(() => {
    const list = perm.workspaces
    if (!list.length) return '未授权，点击刷新'
    const shown = list.slice(0, 2).join(' · ')
    return list.length > 2 ? shown + ' 等' + list.length + ' 个' : shown
  })

  // 清空会话：二次确认（3 秒未确认自动复位）
  const clearConfirm = ref(false)
  let clearTimer: ReturnType<typeof setTimeout> | undefined
  function onClearClick() {
    if (!clearConfirm.value) {
      clearConfirm.value = true
      clearTimer = setTimeout(() => { clearConfirm.value = false }, 3000)
      return
    }
    clearConfirm.value = false
    if (clearTimer) clearTimeout(clearTimer)
    agent.deleteConversation()
  }
  function refreshWorkspaces() {
    perm.loadWorkspaces()
  }
  /* @@CHAT_SETTINGS_FORM_SCRIPT@@ */
  // ── 设置表单：模型 / 向量库 / 工作目录 / 清空会话（弹层表单） ──
  const showSettingsForm = ref(false)
  const showModelPicker = ref(false)
  const formModel = ref(agent.selectedModel)
  const formDirectory = ref(agent.sessionDirectory)
  const modelColumns = computed(() => SUPPORTED_MODELS.map(m => ({ text: m.label, value: m.value })))
  const formModelText = computed(
    () => SUPPORTED_MODELS.find(m => m.value === formModel.value)?.label || formModel.value
  )

  function openSettings() {
    formModel.value = agent.selectedModel
    formDirectory.value = agent.sessionDirectory
    showSettingsForm.value = true
  }

  function onModelConfirm(params: any) {
    const opt = params?.selectedOptions?.[0]
    if (opt && opt.value) formModel.value = opt.value
    agent.selectedModel = formModel.value
    showModelPicker.value = false
  }

  function saveSettings() {
    // 直接写 store 状态（不依赖 setSessionDirectory 方法签名）
    agent.sessionDirectory = formDirectory.value.trim()
    showSettingsForm.value = false
  }
const currentView = computed(() => mobileViews[route.name as string] || null)
</script>

<template>
  <div class="mobile-shell">
    <van-nav-bar
      :title="currentTitle"
      fixed
      placeholder
      safe-area-inset-top
      left-arrow
      @click-left="showMenu = true"
    >
      <template #left>
        <van-icon name="wap-nav" />
      </template>
    </van-nav-bar>

    <div class="mobile-body">
      <component :is="currentView" v-if="currentView" :key="route.fullPath" />
      <router-view v-else />
    </div>

    <!-- <van-tabbar v-model="activeTab" fixed placeholder safe-area-inset-bottom route>
      <van-tabbar-item
        v-for="t in tabs"
        :key="t.name"
        :name="t.name"
        :to="t.path"
      >
        <template #icon="{ active }">
          <div class="tab-ico" :class="{ active }">
            <van-icon :name="t.icon" />
          </div>
        </template>
        {{ t.title }}
      </van-tabbar-item>
    </van-tabbar> -->

    <van-popup
      v-model:show="showMenu"
      position="left"
      :style="{ width: '78%', height: '100%' }"
    >
      <div class="mobile-drawer">
        <div class="drawer-head">
          <div class="drawer-logo"><span>🧠</span></div>
          <div class="drawer-head-text">
            <div class="drawer-title">知识库 · Agent + RAG</div>
            <div class="drawer-sub">多智能体协作工作台</div>
          </div>
        </div>
        <div class="drawer-body">
          <div class="drawer-group-label">功能导航</div>
          <div
            v-for="m in menu"
            :key="m.name"
            class="drawer-item"
            :class="{ current: route.path.startsWith(m.path) }"
            @click="go(m.path)"
          >
            <div class="drawer-item-ico" :style="{ background: m.soft, color: m.color }">
              <van-icon :name="m.vanIcon" />
            </div>
            <span class="drawer-item-title">{{ m.title }}</span>
            <van-icon name="arrow" class="drawer-item-arrow" />
          </div>
                    <!-- @@CHAT_SETTINGS_ENTRY@@ -->
          <!-- ── 设置：与「功能导航」同款 drawer-item，点开弹出参数表单 ── -->
          <div class="drawer-group-label" style="margin-top: 16px">设置</div>

          <div class="drawer-item settings-item" @click="openSettings">
            <div class="drawer-item-ico" style="background: rgba(109,94,241,.12); color: #6d5ef1;">
              <van-icon name="setting-o" />
            </div>
            <span class="drawer-item-title">参数设置</span>
            <van-icon name="arrow" class="drawer-item-arrow" />
          </div>

        </div>
      </div>
    </van-popup>
  <!-- @@CHAT_SETTINGS_FORM_TEMPLATE@@ -->
  <!-- ── 设置表单弹层：模型 / 向量库 / 工作目录 / 清空会话 ── -->
  <van-popup
    v-model:show="showSettingsForm"
    position="bottom"
    round
    :style="{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }"
  >
    <div class="settings-form">
      <div class="settings-form-head">
        <span class="settings-form-title">参数设置</span>
        <van-icon name="cross" class="settings-form-close" @click="showSettingsForm = false" />
      </div>
      <div class="settings-form-body">
        <div class="form-field">
          <div class="form-label">模型</div>
          <van-field
            :model-value="formModelText"
            readonly
            is-link
            placeholder="选择模型"
            @click="showModelPicker = true"
          />
        </div>
        <div class="form-field form-row">
          <div class="form-label">向量库检索</div>
          <van-switch v-model="agent.useVectorDb" size="22" />
        </div>
        <div class="form-field">
          <div class="form-label">工作目录</div>
          <van-field v-model="formDirectory" placeholder="如 F:\\tetris" clearable />
          <div class="form-tip">作用于当前聊天会话（随消息发送到 Agent）</div>
        </div>
        <van-button type="primary" block round class="form-save" @click="saveSettings">
          保存设置
        </van-button>
        <van-button type="danger" plain block round class="form-clear" @click="onClearClick">
          {{ clearConfirm ? '再点一次确认清空' : '清空当前会话' }}
        </van-button>
      </div>
    </div>
  </van-popup>

  <van-popup v-model:show="showModelPicker" position="bottom" round>
    <van-picker
      :columns="modelColumns"
      title="选择模型"
      @confirm="onModelConfirm"
      @cancel="showModelPicker = false"
    />
  </van-popup>
  </div>
</template>

<style scoped>
.mobile-shell {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: var(--m-bg, #f7f8fa);
}
.mobile-body {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 8px;
}

/* ───────── 抽屉 ───────── */
.mobile-drawer {
  height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--surface, #fff);
}
.drawer-head {
  position: relative;
  overflow: hidden;
  padding: 28px 20px 24px;
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--m-brand-grad, linear-gradient(135deg, #6d5ef1, #8b5cf6 55%, #38bdf8));
  color: #fff;
}
/* 装饰圆，营造层次 */
.drawer-head::before,
.drawer-head::after {
  content: '';
  position: absolute;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.14);
}
.drawer-head::before { width: 150px; height: 150px; right: -40px; top: -60px; }
.drawer-head::after { width: 90px; height: 90px; right: 70px; bottom: -50px; background: rgba(255,255,255,.09); }
.drawer-logo {
  position: relative;
  z-index: 1;
  width: 48px;
  height: 48px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.22);
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.15);
}
.drawer-head-text { position: relative; z-index: 1; }
.drawer-title { font-size: 16px; font-weight: 700; letter-spacing: 0.2px; }
.drawer-sub { font-size: 12px; opacity: 0.85; margin-top: 3px; }

.drawer-body { flex: 1; overflow-y: auto; padding: 14px 12px 24px; }
.drawer-group-label {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1px;
  color: var(--text-secondary, #64748b);
  padding: 4px 10px 10px;
  text-transform: uppercase;
}
.drawer-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 11px 12px;
  border-radius: 14px;
  margin-bottom: 4px;
  cursor: pointer;
  transition: background 0.15s;
}
.drawer-item:active { background: var(--bg, #f1f5f9); }
.drawer-item.current { background: var(--primary-soft, #eef2ff); }
.drawer-item-ico {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 19px;
  flex-shrink: 0;
}
.drawer-item-title { flex: 1; font-size: 14.5px; font-weight: 500; color: var(--text, #1e293b); }
.drawer-item.current .drawer-item-title { color: var(--primary, #4f46e5); font-weight: 600; }
.drawer-item-arrow { color: var(--text-secondary, #94a3b8); font-size: 14px; }

/* ───────── TabBar 激活态：渐变胶囊 ───────── */
.tab-ico {
  width: 34px;
  height: 30px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 21px;
  color: #9aa3b5;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
.tab-ico.active {
  background: var(--m-brand-grad, linear-gradient(135deg, #6d5ef1, #8b5cf6 55%, #38bdf8));
  color: #fff;
  box-shadow: 0 4px 12px rgba(109, 94, 241, 0.35);
  transform: translateY(-1px);
}
  /* @@CHAT_PANEL_STYLE@@ */
  /* ── 设置分组（移动端聊天对齐 ChatGPT） ── */
  .drawer-sub-group { padding: 0 12px 10px; }
  .drawer-sub-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
    color: var(--text-secondary, #64748b);
    padding: 2px 2px 8px;
  }
  .model-options { display: flex; flex-wrap: wrap; gap: 8px; }
  .model-opt {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 12px;
    border-radius: 10px;
    border: 1px solid var(--border, #eef1f6);
    background: var(--bg, #f8fafc);
    font-size: 13px;
    color: var(--text, #1e293b);
    cursor: pointer;
    transition: all 0.15s;
  }
  .model-opt.current {
    border-color: var(--primary, #4f46e5);
    background: var(--primary-soft, #eef2ff);
    color: var(--primary, #4f46e5);
    font-weight: 600;
  }
  .settings-item { cursor: pointer; }
  .drawer-item-main { flex: 1; min-width: 0; }
  .drawer-item-sub {
    font-size: 11px;
    color: var(--text-secondary, #94a3b8);
    margin-top: 2px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .danger .drawer-item-title { color: #ef4444; }
  .danger-text { color: #ef4444 !important; }  /* @@CHAT_SETTINGS_FORM_STYLE@@ */
  /* ── 设置表单弹层（与功能导航 drawer-item 视觉统一） ── */
  .settings-form { padding: 6px 0 calc(12px + env(safe-area-inset-bottom, 0px)); }
  .settings-form-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 16px 6px;
  }
  .settings-form-title { font-size: 16px; font-weight: 700; }
  .settings-form-close {
    font-size: 18px;
    color: var(--text-secondary, #94a3b8);
    cursor: pointer;
    padding: 4px;
  }
  .settings-form-body { padding: 8px 16px; }
  .form-field { margin-bottom: 14px; }
  .form-label {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-secondary, #64748b);
    margin-bottom: 6px;
    letter-spacing: 0.5px;
  }
  .form-row { display: flex; align-items: center; justify-content: space-between; }
  .form-row .form-label { margin-bottom: 0; }
  .form-tip { font-size: 11px; color: var(--text-secondary, #94a3b8); margin-top: 6px; }
  .form-save { margin-top: 8px; }
  .form-clear { margin-top: 10px; }

</style>
