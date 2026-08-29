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

// 全站导航（用于顶部菜单抽屉）
const menu = [
  { name: 'MultiAgent', path: '/multi-agent', title: '多智能体', icon: '🔮' },
  { name: 'Documents', path: '/documents', title: '文档管理', icon: '📄' },
  { name: 'Skills', path: '/skills', title: '技能', icon: '🧠' },
  { name: 'Plugins', path: '/plugins', title: '插件', icon: '🔌' },
  { name: 'CustomTools', path: '/custom-tools', title: '自定义工具', icon: '🧰' },
  { name: 'Vectors', path: '/vectors', title: '向量库', icon: '🔢' },
  { name: 'Generated', path: '/generated', title: '生成文件', icon: '📝' },
  { name: 'Monitoring', path: '/monitoring', title: '系统监控', icon: '📊' },
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
        :icon="t.icon"
      >{{ t.title }}</van-tabbar-item>
    </van-tabbar>

    <van-popup
      v-model:show="showMenu"
      position="left"
      :style="{ width: '72%', height: '100%' }"
    >
      <div class="mobile-drawer">
        <div class="mobile-drawer-head">
          <div class="mobile-drawer-logo">🧠</div>
          <div class="mobile-drawer-title">知识库 · Agent + RAG</div>
        </div>
        <van-cell
          v-for="m in menu"
          :key="m.name"
          :title="m.title"
          :icon="m.icon"
          :to="m.path"
          clickable
          @click="showMenu = false"
        />
      </div>
    </van-popup>
  </div>
</template>

<style scoped>
.mobile-shell {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f7f8fa;
}
.mobile-body {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 8px;
}
.mobile-drawer {
  height: 100%;
  background: #fff;
}
.mobile-drawer-head {
  padding: 24px 16px;
  display: flex;
  align-items: center;
  gap: 10px;
  background: linear-gradient(135deg, #4f46e5, #6366f1);
  color: #fff;
}
.mobile-drawer-logo {
  font-size: 28px;
}
.mobile-drawer-title {
  font-size: 15px;
  font-weight: 600;
}
</style>
