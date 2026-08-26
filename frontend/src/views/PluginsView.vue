<script setup lang="ts">
import { onMounted } from 'vue'
import { usePluginStore } from '../stores/plugins'

// 插件状态管理
const plugins = usePluginStore()

// 组件挂载时加载所有插件
onMounted(() => {
  plugins.fetchAll()
})

// 切换插件启用/禁用状态
async function handleToggle(name: string, enabled: boolean) {
  try {
    await plugins.toggle(name, !enabled)
  } catch (err: any) {
    alert(err.message)
  }
}
</script>

<template>
  <div class="page-header">
    <h2>插件管理</h2>
    <p>管理集成到智能体的第三方插件</p>
  </div>
  <div class="page-content">
    <div v-if="plugins.loading" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="plugins.plugins.length === 0" class="empty-state">
      <div class="icon">🔌</div>
      <p>未安装任何插件</p>
      <p style="font-size:13px;margin-top:4px;">安装第三方插件以扩展智能体能力。</p>
    </div>

    <div v-else style="display:flex;flex-direction:column;gap:8px;">
      <div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">
        {{ plugins.plugins.length }} 个插件已安装
      </div>
      <div v-for="plugin in plugins.plugins" :key="plugin.name" class="card" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
        <div style="font-size:24px;">🔌</div>
        <div style="flex:1;min-width:0;">
          <div style="font-weight:600;font-size:14px;">{{ plugin.name }}</div>
          <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">{{ plugin.description }}</div>
          <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">v{{ plugin.version }}</div>
        </div>
        <span class="badge" :class="plugin.enabled ? 'badge-enabled' : 'badge-disabled'">
          {{ plugin.enabled ? '已启用' : '已禁用' }}
        </span>
        <button
          class="btn"
          :class="plugin.enabled ? 'btn-danger' : 'btn-primary'"
          style="font-size:12px;padding:6px 12px;"
          @click="handleToggle(plugin.name, plugin.enabled)"
        >
          {{ plugin.enabled ? '禁用' : '启用' }}
        </button>
      </div>
    </div>
  </div>
</template>
