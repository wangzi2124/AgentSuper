<script setup lang="ts">
import { onMounted } from 'vue'
import { useSkillStore } from '../stores/skills'

const skills = useSkillStore()

onMounted(() => {
  skills.fetchAll()
})

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
    <p>定义智能体能力的 skill.md 文件</p>
  </div>
  <div class="page-content">
    <div v-if="skills.loading" class="loading-wrap">
      <span class="spinner"></span>
    </div>

    <div v-else-if="skills.skills.length === 0" class="empty-state">
      <div class="icon">🧠</div>
      <p>未加载任何技能</p>
      <p style="font-size:13px;margin-top:4px;">技能从后端技能目录加载。</p>
    </div>

    <div v-else class="item-list">
      <div class="list-meta">{{ skills.skills.length }} 个技能可用</div>
      <div v-for="skill in skills.skills" :key="skill.name" class="item-card">
        <div class="item-icon" data-accent="skill">🧠</div>
        <div class="item-info">
          <div class="item-name">{{ skill.name }}</div>
          <div class="item-desc">{{ skill.description }}</div>
          <div class="item-path">{{ skill.path }}</div>
        </div>
        <span class="badge" :class="skill.enabled ? 'badge-enabled' : 'badge-disabled'">
          {{ skill.enabled ? '已启用' : '已禁用' }}
        </span>
        <button
          class="btn item-action"
          :class="skill.enabled ? 'btn-danger' : 'btn-primary'"
          @click="handleToggle(skill.name, skill.enabled)"
        >
          {{ skill.enabled ? '禁用' : '启用' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.loading-wrap {
  display: flex;
  justify-content: center;
  padding: 48px;
}
.item-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.list-meta {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
  font-weight: 500;
}
.item-card {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 18px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  flex-wrap: wrap;
  transition: all var(--duration) var(--ease);
  animation: fadeSlideUp 0.4s var(--ease);
}
.item-card:hover {
  box-shadow: var(--shadow-md);
  border-color: color-mix(in srgb, var(--primary) 25%, var(--border));
  transform: translateY(-1px);
}
.item-icon {
  width: 46px;
  height: 46px;
  border-radius: var(--radius);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
  background: linear-gradient(135deg, color-mix(in srgb, var(--primary) 12%, transparent), color-mix(in srgb, var(--accent) 8%, transparent));
  border: 1px solid color-mix(in srgb, var(--primary) 20%, transparent);
}
.item-info { flex: 1; min-width: 0; }
.item-name { font-weight: 700; font-size: 14px; color: var(--text); }
.item-desc { font-size: 12.5px; color: var(--text-secondary); margin-top: 3px; line-height: 1.5; }
.item-path {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-action { font-size: 12px; padding: 7px 14px; }
@media (max-width: 600px) {
  .item-card { gap: 12px; }
  .item-action { margin-left: auto; }
}
</style>
