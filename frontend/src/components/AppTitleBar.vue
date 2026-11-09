<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import UserMenu from './UserMenu.vue'
import { useMultiAgentStore } from '../stores/multiAgent'
import { useTabsStore, type AppTab } from '../stores/tabs'
import { useSidebarStore } from '../stores/sidebar'
import { useV2SessionStore } from '../v2/sessionStore'
import { FEATURE_NAV, pageTitle } from '../config/navigation'

const route = useRoute()
const router = useRouter()
const agent = useMultiAgentStore()
const v2 = useV2SessionStore()
const tabsStore = useTabsStore()
const sidebar = useSidebarStore()
const scroller = ref<HTMLElement | null>(null)
const featureMenuOpen = ref(false)

const featureIcons = new Map(FEATURE_NAV.map((item) => [item.path, item.icon]))

function tabIcon(tab: AppTab): { d: string; box: string } {
  if (tab.kind === 'conversation') {
    return { d: 'M2.5 3.5h11v7.2H7l-3.2 2.6v-2.6H2.5V3.5Z', box: '0 0 16 16' }
  }
  const icon = featureIcons.get(tab.path)
  return icon
    ? { d: icon, box: '0 0 24 24' }
    : { d: 'M4 4h9l7 7v9H4V4Zm9 0v7h7M8 14h8M8 17h5', box: '0 0 24 24' }
}

function openFeature(path: string) {
  featureMenuOpen.value = false
  if (route.path !== path) router.push(path)
}

function syncTabs() {
  tabsStore.setActive(route.path)
  if (
    route.name === 'Login' ||
    route.name === 'NotFound' ||
    route.name === 'MultiAgent' ||
    route.name === 'V2Chat'
  ) return
  if (route.name === 'V2ChatConversation') {
    const id = String(route.params.id || '')
    const title = v2.getRuntime(id)?.info?.title || '会话'
    tabsStore.openTab(route.path, title, 'conversation')
    return
  }
  if (route.name === 'MultiAgentConversation') {
    const id = String(route.params.id || '')
    const title = v2.getRuntime(id)?.info?.title
      || (agent.conversationId === id ? agent.conversationTitle : '')
      || agent.conversations.find(c => c.id === id)?.title
      || '会话'
    tabsStore.openTab(route.path, title, 'conversation')
    return
  }
  if (route.name) tabsStore.openTab(route.path, pageTitle(route.path), 'page')
}

watch(() => route.fullPath, () => {
  syncTabs()
  void nextTick(() => {
    scroller.value?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ inline: 'nearest', block: 'nearest' })
  })
}, { immediate: true })
watch(() => agent.conversationTitle, syncTabs)
watch(() => agent.conversations.map(c => `${c.id}\u0000${c.title}`).join('\u0001'), syncTabs)
watch(
  () => Object.values(v2.runtimes).map(r => `${r.id}\u0000${r.info?.title || ''}`).join('\u0001'),
  syncTabs,
)

function openTab(tab: AppTab) {
  if (route.path !== tab.path) router.push(tab.path)
}

function closeTab(event: Event, tab: AppTab) {
  event.stopPropagation()
  const next = tabsStore.closeTab(tab.path)
  if (next) router.push(next)
}

function closeWithMiddle(event: MouseEvent, tab: AppTab) {
  if (event.button !== 1) return
  event.preventDefault()
  event.stopPropagation()
  const next = tabsStore.closeTab(tab.path)
  if (next) router.push(next)
}

function goHome() {
  if (route.name !== 'MultiAgent') router.push({ name: 'MultiAgent' })
}

function newConversation() {
  agent.newChat(agent.activeDirectory)
  router.push({ name: 'MultiAgent' })
}
</script>

<template>
  <header class="app-titlebar">
    <div class="titlebar-left">
      <div class="titlebar-feature">
        <button
          class="titlebar-icon"
          :class="{ pressed: featureMenuOpen }"
          title="功能"
          aria-label="功能"
          @click.stop="featureMenuOpen = !featureMenuOpen"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">
            <path d="m12 3 9 5-9 5-9-5 9-5Z"/>
            <path d="m3 12 9 5 9-5"/>
            <path d="m3 16 9 5 9-5"/>
          </svg>
        </button>
        <div v-if="featureMenuOpen" class="titlebar-menu-mask" @click="featureMenuOpen = false"></div>
        <div v-if="featureMenuOpen" class="titlebar-feature-menu">
          <button
            v-for="item in FEATURE_NAV"
            :key="item.path"
            class="titlebar-feature-item"
            :class="{ active: route.path === item.path }"
            type="button"
            @click="openFeature(item.path)"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path :d="item.icon" /></svg>
            <span>{{ item.label }}</span>
          </button>
        </div>
      </div>

      <button class="titlebar-icon" :class="{ pressed: route.name === 'MultiAgent' }" title="主页" aria-label="主页" @click="goHome">
        <svg width="20" height="20" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.2">
          <path d="M13.8 11.6H9.4M11.6 9.4v4.4M6.8 9.4v4.4H2V9.4h4.8Zm0-7.4v4.4H2V2h4.8Zm7.2 0v4.4H9.4V2H14Z"/>
        </svg>
      </button>

      <button
        class="titlebar-icon titlebar-sidebar-toggle"
        :class="{ collapsed: sidebar.collapsed }"
        :title="sidebar.collapsed ? '展开侧边栏' : '收起侧边栏'"
        aria-label="切换侧边栏"
        @click="sidebar.toggle()"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="4" width="18" height="16" rx="2"/>
          <path d="M9 4v16"/>
          <polyline points="15.5 9.5 13 12 15.5 14.5"/>
        </svg>
      </button>

      <div ref="scroller" class="titlebar-tabs">
        <button
          v-for="tab in tabsStore.tabs"
          :key="tab.path"
          class="titlebar-tab"
          :class="{ active: tabsStore.activePath === tab.path }"
          :data-active="tabsStore.activePath === tab.path"
          :title="tab.title"
          @click="openTab(tab)"
          @mouseup="closeWithMiddle($event, tab)"
        >
          <svg class="tab-kind" width="13" height="13" :viewBox="tabIcon(tab).box" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path :d="tabIcon(tab).d" />
          </svg>
          <span class="tab-label">{{ tab.title }}</span>
          <span class="tab-close" title="关闭标签页" @click="closeTab($event, tab)">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M4.4 4.4l7.2 7.2M11.6 4.4 4.4 11.6"/></svg>
          </span>
        </button>
      </div>

      <button class="titlebar-icon" title="新建会话" aria-label="新建会话" @click="newConversation">
        <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round">
          <path d="M8 2.9v10.2M2.9 8h10.2"/>
        </svg>
      </button>
    </div>

    <div class="titlebar-right">
      <UserMenu />
    </div>
  </header>
</template>

<style scoped src="../styles/titleBar.css"></style>
