<script setup lang="ts">
// 移动端聊天页：复用 v2 聊天视图（单一 SSE、durable inbox、权限/压缩）。
import { defineAsyncComponent } from 'vue'
import { useRoute } from 'vue-router'

const V2ChatView = defineAsyncComponent(() => import('../../views/V2ChatView.vue'))
const route = useRoute()
</script>

<template>
  <V2ChatView class="m-chat" :key="route.fullPath" />
</template>

<style scoped>
.m-chat {
  height: 100%;
}
.m-chat :deep(.v2-head) {
  padding-top: calc(6px + env(safe-area-inset-top, 0px));
}
.m-chat :deep(.v2-composer) {
  padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));
}
.m-chat :deep(.v2-messages) {
  padding-left: 10px;
  padding-right: 10px;
}
</style>

