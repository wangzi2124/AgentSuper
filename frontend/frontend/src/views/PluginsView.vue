<script setup lang="ts">
import { onMounted } from 'vue'
import { usePluginStore } from '../stores/plugins'

const plugins = usePluginStore()

onMounted(() => {
  plugins.fetchAll()
})

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
    <h2>Plugins</h2>
    <p>Manage third-party plugins integrated with the agent</p>
  </div>
  <div class="page-content">
    <div v-if="plugins.loading" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="plugins.plugins.length === 0" class="empty-state">
      <div class="icon">🔌</div>
      <p>No plugins installed</p>
      <p style="font-size:13px;margin-top:4px;">Install third-party plugins to extend agent capabilities.</p>
    </div>

    <div v-else style="display:flex;flex-direction:column;gap:8px;">
      <div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">
        {{ plugins.plugins.length }} plugin(s) installed
      </div>
      <div v-for="plugin in plugins.plugins" :key="plugin.name" class="card" style="display:flex;align-items:center;gap:14px;">
        <div style="font-size:24px;">🔌</div>
        <div style="flex:1;min-width:0;">
          <div style="font-weight:600;font-size:14px;">{{ plugin.name }}</div>
          <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">{{ plugin.description }}</div>
          <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">v{{ plugin.version }}</div>
        </div>
        <span class="badge" :class="plugin.enabled ? 'badge-enabled' : 'badge-disabled'">
          {{ plugin.enabled ? 'Enabled' : 'Disabled' }}
        </span>
        <button
          class="btn"
          :class="plugin.enabled ? 'btn-danger' : 'btn-primary'"
          style="font-size:12px;padding:6px 12px;"
          @click="handleToggle(plugin.name, plugin.enabled)"
        >
          {{ plugin.enabled ? 'Disable' : 'Enable' }}
        </button>
      </div>
    </div>
  </div>
</template>
