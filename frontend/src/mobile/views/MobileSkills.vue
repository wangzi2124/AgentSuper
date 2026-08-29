<script setup lang="ts">
import { onMounted } from 'vue'
import { useSkillStore } from '../../stores/skills'
import { showToast } from 'vant'

const skills = useSkillStore()

onMounted(() => {
  skills.fetchAll()
})

async function handleToggle(name: string, enabled: boolean) {
  try {
    await skills.toggle(name, enabled)
    showToast(enabled ? '已禁用' : '已启用')
  } catch (err: any) {
    showToast(err.message || '操作失败')
  }
}
</script>

<template>
  <div class="m-skills">
    <van-loading v-if="skills.loading" class="loading" />
    <van-empty v-else-if="skills.skills.length === 0" image="search" description="未加载任何技能" />

    <div v-else class="skill-list">
      <div class="m-count-pill"><van-icon name="star-o" />共 {{ skills.skills.length }} 个技能可用</div>
      <div v-for="skill in skills.skills" :key="skill.name" class="m-card skill-item">
        <div class="skill-ico">
          <van-icon name="star-o" />
        </div>
        <div class="skill-main">
          <div class="skill-name">{{ skill.name }}</div>
          <div class="skill-desc">{{ skill.description || skill.path }}</div>
        </div>
        <van-switch
          :model-value="skill.enabled"
          size="20px"
          @update:model-value="handleToggle(skill.name, skill.enabled)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.m-skills { padding: 4px 12px 16px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.skill-list { display: flex; flex-direction: column; gap: 10px; }

.skill-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 14px;
}
.skill-ico {
  width: 40px;
  height: 40px;
  border-radius: 13px;
  background: var(--m-skill-soft);
  color: var(--m-skill);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.skill-main { flex: 1; min-width: 0; }
.skill-name { font-size: 14.5px; font-weight: 600; color: var(--text); }
.skill-desc {
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
