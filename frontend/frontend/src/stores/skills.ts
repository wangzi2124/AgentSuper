import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Skill } from '../types'
import { listSkills, toggleSkill } from '../api/skills'

export const useSkillStore = defineStore('skills', () => {
  const skills = ref<Skill[]>([])
  const loading = ref(false)

  async function fetchAll() {
    loading.value = true
    try {
      skills.value = await listSkills()
    } finally {
      loading.value = false
    }
  }

  async function toggle(name: string, enabled: boolean) {
    await toggleSkill(name, enabled)
    const skill = skills.value.find((s) => s.name === name)
    if (skill) skill.enabled = enabled
  }

  return { skills, loading, fetchAll, toggle }
})
