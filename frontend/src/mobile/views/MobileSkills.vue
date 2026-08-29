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
      <div class="count">共 {{ skills.skills.length }} 个技能可用</div>
      <van-cell-group inset>
        <van-cell
          v-for="skill in skills.skills"
          :key="skill.name"
          :title="skill.name"
          :label="skill.description || skill.path"
          center
        >
          <template #icon>
            <div class="skill-icon">🧠</div>
          </template>
          <template #right-icon>
            <van-switch
              :model-value="skill.enabled"
              size="20px"
              @update:model-value="handleToggle(skill.name, skill.enabled)"
            />
          </template>
        </van-cell>
      </van-cell-group>
    </div>
  </div>
</template>

<style scoped>
.m-skills { padding: 4px 12px 16px; }
.loading { display: flex; justify-content: center; padding: 48px 0; }
.skill-list { display: flex; flex-direction: column; gap: 8px; }
.count { font-size: 12px; color: #97a0b4; padding: 6px 4px; }
.skill-icon { margin-right: 10px; font-size: 22px; }
</style>
