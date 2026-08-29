<script setup lang="ts">
import { onMounted } from 'vue'
import { usePluginStore } from '../../stores/plugins'
import { showToast } from 'vant'

const plugins = usePluginStore()

onMounted(() => {
  plugins.fetchAll()
})

async function handleToggle(name: string, enabled: boolean) {
  try {
    await plugins.toggle(name, enabled)
    showToast(enabled ? '已禁用' : '已启用')
  } catch (err: any) {
    showToast(err.message || '操作失败')
  }
}
</script>

<template>
  <div class="m-plugins">
    <van-loading v-if="plugins.loading" class="loading" />
    <van-empty v-else-if="plugins.plugins.length === 0" image="search" description="未加载任何插件" />

    <div v-else class="plugin-list">
      <div class="m-count-pill"><van-icon name="apps-o" />共 {{ plugins.plugins.length }} 个插件</div>
      <div v-for="plugin in plugins.plugins" :key="plugin.name" class="m-card plugin-item">
        <div class="plugin-ico">
          <van-icon name="apps-o" />
        </div>
        <div class="plugin-main">
          <div class="plugin-name">{{ plugin.name }}</div>
          <div class="plugin-desc">{{ plugin.description || plugin.version }}</div>
        </div>
        <van-switch
          :model-value="plugin.enabled"
          size="20px"
          @update:model-value="handleToggle(plugin.name, plugin.enabled)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.m-plugins { padding: 4px 12px 16px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.plugin-list { display: flex; flex-direction: column; gap: 10px; }

.plugin-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 14px;
}
.plugin-ico {
  width: 40px;
  height: 40px;
  border-radius: 13px;
  background: var(--m-plugin-soft);
  color: var(--m-plugin);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.plugin-main { flex: 1; min-width: 0; }
.plugin-name { font-size: 14.5px; font-weight: 600; color: var(--text); }
.plugin-desc {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 3px;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}
</style>
