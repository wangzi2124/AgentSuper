import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Skill } from '../types'
import { listSkills, toggleSkill } from '../api/skills'

// 技能管理 Store
export const useSkillStore = defineStore('skills', () => {
  // 技能列表
  const skills = ref<Skill[]>([])
  // 加载状态
  const loading = ref(false)

  // 获取所有技能
  async function fetchAll() {
    loading.value = true
    try {
      skills.value = await listSkills()
    } finally {
      loading.value = false
    }
  }

  // 切换技能的启用状态
  async function toggle(name: string, enabled: boolean) {
    await toggleSkill(name, enabled)
    const skill = skills.value.find((s) => s.name === name)
    if (skill) skill.enabled = enabled
  }

  return { skills, loading, fetchAll, toggle }
})
