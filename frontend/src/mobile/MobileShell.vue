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

    <van-tabbar v-model="activeTab" fixed placeholder safe-area-inset-bottom route>
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
    </van-tabbar>

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
        </div>
      </div>
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
</style>
