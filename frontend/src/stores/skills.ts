import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Skill, SkillDetail } from '../types'
import {
  listSkills,
  getSkill,
  createSkill,
  updateSkill,
  deleteSkill,
  toggleSkill,
  getSkillsSources,
  addSkillsDirectory,
  removeSkillsDirectory,
} from '../api/skills'

/**
 * 技能管理 Store
 *
 * 存储模型：受管库 `data/skills`（后端启动自动创建，UI 新建的技能落这里）
 * + 若干外部技能源（用户「选择技能文件夹」追加，可移除）。同名以受管库优先。
 */
export const useSkillStore = defineStore('skills', () => {
  const skills = ref<Skill[]>([])
  /** 受管技能库路径 */
  const managedDir = ref('')
  /** 用户追加的外部技能源 */
  const extraDirs = ref<string[]>([])
  const loading = ref(false)
  const initialized = ref(false)

  function applySources(s: { managed?: string; directory?: string; extra_dirs?: string[] }) {
    managedDir.value = s.managed || s.directory || ''
    extraDirs.value = s.extra_dirs || []
  }

  /** 获取技能列表 + 技能源（两个请求并行） */
  async function fetchAll() {
    loading.value = true
    try {
      const [src, list] = await Promise.all([getSkillsSources(), listSkills()])
      applySources(src)
      skills.value = list
    } finally {
      loading.value = false
      initialized.value = true
    }
  }

  /** 重新扫描（目录内容被外部改动后） */
  async function refresh() {
    await fetchAll()
  }

  /** 追加外部技能源 */
  async function addDirectory(dir: string) {
    const res = await addSkillsDirectory(dir)
    applySources(res)
    skills.value = res.skills
    initialized.value = true
  }

  /** 移除外部技能源 */
  async function removeDirectory(dir: string) {
    const res = await removeSkillsDirectory(dir)
    applySources(res)
    skills.value = res.skills
  }

  /** 新建技能（写入受管库） */
  async function create(payload: {
    name: string
    description?: string
    content?: string
    disable_model_invocation?: boolean
    enabled?: boolean
  }): Promise<Skill> {
    const skill = await createSkill(payload)
    await fetchAll()
    return skill
  }

  /** 读取技能详情（含正文，编辑表单回填用） */
  async function detail(name: string): Promise<SkillDetail> {
    return getSkill(name)
  }

  /** 编辑受管库中的技能 */
  async function update(
    name: string,
    payload: {
      description?: string
      content?: string
      disable_model_invocation?: boolean
      enabled?: boolean
    },
  ): Promise<Skill> {
    const skill = await updateSkill(name, payload)
    await fetchAll()
    return skill
  }

  /** 删除受管库中的技能 */
  async function remove(name: string): Promise<void> {
    await deleteSkill(name)
    await fetchAll()
  }

  /** 切换启用状态（乐观更新，失败时回滚由调用方处理） */
  async function toggle(name: string, enabled: boolean) {
    const skill = skills.value.find((s) => s.name === name)
    const prev = skill?.enabled
    if (skill) skill.enabled = enabled
    try {
      await toggleSkill(name, enabled)
    } catch (e) {
      if (skill && prev !== undefined) skill.enabled = prev
      throw e
    }
  }

  return {
    skills,
    /** @deprecated 兼容旧调用方，等于 managedDir */
    directory: managedDir,
    managedDir,
    extraDirs,
    loading,
    initialized,
    fetchAll,
    refresh,
    addDirectory,
    removeDirectory,
    create,
    detail,
    update,
    remove,
    toggle,
  }
})
