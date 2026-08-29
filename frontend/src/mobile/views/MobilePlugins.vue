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
      <div class="count">共 {{ plugins.plugins.length }} 个插件</div>
      <van-cell-group inset>
        <van-cell
          v-for="plugin in plugins.plugins"
          :key="plugin.name"
          :title="plugin.name"
          :label="plugin.description || plugin.version"
          center
        >
          <template #icon>
            <div class="plugin-icon">🔌</div>
          </template>
          <template #right-icon>
            <van-switch
              :model-value="plugin.enabled"
              size="20px"
              @update:model-value="handleToggle(plugin.name, plugin.enabled)"
            />
          </template>
        </van-cell>
      </van-cell-group>
    </div>
  </div>
</template>

<style scoped>
.m-plugins { padding: 4px 12px 16px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.plugin-list { display: flex; flex-direction: column; gap: 8px; }
.count { font-size: 12px; color: #97a0b4; padding: 6px 4px; }
.plugin-icon { margin-right: 10px; font-size: 22px; }
</style>
