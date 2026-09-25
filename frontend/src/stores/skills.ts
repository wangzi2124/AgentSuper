import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Skill } from '../types'
import { listSkills, toggleSkill, getSkillsDirectory, setSkillsDirectory } from '../api/skills'

// 技能管理 Store（技能目录由前端在自定义工具页选择）
export const useSkillStore = defineStore('skills', () => {
  // 技能列表
  const skills = ref<Skill[]>([])
  // 当前技能目录
  const directory = ref('')
  // 加载状态
  const loading = ref(false)
  // 暂未初始化
  const initialized = ref(false)

  // 获取所有技能 + 当前目录
  async function fetchAll() {
    loading.value = true
    try {
      const dir = await getSkillsDirectory()
      directory.value = dir.directory
      skills.value = await listSkills()
    } finally {
      loading.value = false
      initialized.value = true
    }
  }

  // 设置技能目录（选择文件夹后热加载）
  async function setDirectory(dir: string) {
    const res = await setSkillsDirectory(dir)
    directory.value = res.directory
    skills.value = res.skills
    initialized.value = true
  }

  // 切换技能的启用状态
  async function toggle(name: string, enabled: boolean) {
    await toggleSkill(name, enabled)
    const skill = skills.value.find((s) => s.name === name)
    if (skill) skill.enabled = enabled
  }

  return { skills, directory, loading, initialized, fetchAll, setDirectory, toggle }
})