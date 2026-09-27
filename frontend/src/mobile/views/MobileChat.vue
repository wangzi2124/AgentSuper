<script setup lang="ts">
// 移动端聊天页：复用完整的多智能体视图（流式、附件、权限、工作目录、模型选择等）
// 由 Vant NavBar 提供顶栏标题、TabBar 提供底部导航；移动端头部细节由 mobile.css 精简。
// 异步引入：与路由表的 `/multi-agent` 懒加载共享同一 chunk，避免重复下载。
import { defineAsyncComponent } from 'vue'
import { useRoute } from 'vue-router'

const MultiAgentView = defineAsyncComponent(() => import('../../views/MultiAgentView.vue'))
const route = useRoute()
</script>

<template>
  <MultiAgentView class="m-chat" :key="route.fullPath" />
</template>

<style scoped>
.m-chat {
  height: 100%;
}
</style>
