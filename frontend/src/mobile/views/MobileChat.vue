<script setup lang="ts">
// 移动端聊天页：复用完整的多智能体视图（流式、附件、权限、工作目录、模型选择等）
// 由 Vant NavBar 提供顶栏标题、TabBar 提供底部导航；移动端头部细节由 mobile.css 精简。
import MultiAgentView from '../../views/MultiAgentView.vue'
import { useRoute } from 'vue-router'

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
