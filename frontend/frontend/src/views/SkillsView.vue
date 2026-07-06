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
    <h2>Skills</h2>
    <p>Manage skill.md files that define agent capabilities</p>
  </div>
  <div class="page-content">
    <div v-if="skills.loading" style="display:flex;justify-content:center;padding:40px;">
      <span class="spinner"></span>
    </div>

    <div v-else-if="skills.skills.length === 0" class="empty-state">
      <div class="icon">🧠</div>
      <p>No skills loaded</p>
      <p style="font-size:13px;margin-top:4px;">Skills are loaded from the backend skills directory.</p>
    </div>

    <div v-else style="display:flex;flex-direction:column;gap:8px;">
      <div style="font-size:13px;color:var(--text-secondary);margin-bottom:4px;">
        {{ skills.skills.length }} skill(s) available
      </div>
      <div v-for="skill in skills.skills" :key="skill.name" class="card" style="display:flex;align-items:center;gap:14px;">
        <div style="font-size:24px;">🧠</div>
        <div style="flex:1;min-width:0;">
          <div style="font-weight:600;font-size:14px;">{{ skill.name }}</div>
          <div style="font-size:12px;color:var(--text-secondary);margin-top:2px;">{{ skill.description }}</div>
          <div style="font-size:11px;color:var(--text-secondary);margin-top:2px;">{{ skill.path }}</div>
        </div>
        <span class="badge" :class="skill.enabled ? 'badge-enabled' : 'badge-disabled'">
          {{ skill.enabled ? 'Enabled' : 'Disabled' }}
        </span>
        <button
          class="btn"
          :class="skill.enabled ? 'btn-danger' : 'btn-primary'"
          style="font-size:12px;padding:6px 12px;"
          @click="handleToggle(skill.name, skill.enabled)"
        >
          {{ skill.enabled ? 'Disable' : 'Enable' }}
        </button>
      </div>
    </div>
  </div>
</template>
