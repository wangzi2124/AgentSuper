<script setup lang="ts">
import { onMounted } from 'vue'
import { useSkillStore } from '../stores/skills'

// 技能状态管理
const skills = useSkillStore()

// 组件挂载时加载所有技能
onMounted(() => {
  skills.fetchAll()
})

// 切换技能启用/禁用状态
async function handleToggle(name: string, enabled: boolean) {
  try {
    await skills.toggle(name, !enabled)
  } catch (err: any) {
    alert(err.message)
  }
}
</script>

<template>
  <div class="page-header">
    <h2>技能管理</h2>
    <p>管理定义智能体能力的 skill.md 文件</p>
  </div>
  <div class="page-content">
    <div v-if="skills.loading" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="skills.skills.length === 0" class="empty-state">
      <div class="icon">🧠</div>
      <p>未加载任何技能</p>
      <p style="font-size:13px;margin-top:4px;">技能从后端技能目录加载。</p>
    </div>

    <div v-else style="display:flex;flex-direction:column;gap:8px;">
      <div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">
        {{ skills.skills.length }} 个技能可用
      </div>
      <div v-for="skill in skills.skills" :key="skill.name" class="card" style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;">
        <div style="font-size:24px;">🧠</div>
        <div style="flex:1;min-width:0;">
          <div style="font-weight:600;font-size:14px;">{{ skill.name }}</div>
          <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">{{ skill.description }}</div>
          <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">{{ skill.path }}</div>
        </div>
        <span class="badge" :class="skill.enabled ? 'badge-enabled' : 'badge-disabled'">
          {{ skill.enabled ? '已启用' : '已禁用' }}
        </span>
        <button
          class="btn"
          :class="skill.enabled ? 'btn-danger' : 'btn-primary'"
          style="font-size:12px;padding:6px 12px;"
          @click="handleToggle(skill.name, skill.enabled)"
        >
          {{ skill.enabled ? '禁用' : '启用' }}
        </button>
      </div>
    </div>
  </div>
</template>
