<script setup lang="ts">
import { ref, watch } from 'vue'

import type { FormField, FormInfo, FormOption } from '../contracts/v2'

const props = defineProps<{ form: FormInfo }>()
const emit = defineEmits<{
  submit: [response: Record<string, unknown>]
  cancel: []
}>()

const values = ref<Record<string, string | string[]>>({})

watch(
  () => props.form.id,
  () => {
    values.value = {}
  },
)

function optionsOf(field: FormField): FormOption[] {
  return field.option_details?.length
    ? field.option_details
    : field.options.map(label => ({ label, description: '', recommended: false }))
}

function isSelected(field: FormField, label: string): boolean {
  const value = values.value[field.id]
  return Array.isArray(value) ? value.includes(label) : value === label
}

function choose(field: FormField, label: string) {
  if (field.multiple) {
    const current = values.value[field.id]
    const list = Array.isArray(current) ? current : current ? [current] : []
    const next = list.includes(label) ? list.filter(item => item !== label) : [...list, label]
    values.value = { ...values.value, [field.id]: next }
    return
  }
  values.value = { ...values.value, [field.id]: label }
}

function onLegacyInput(field: FormField, event: Event) {
  values.value = { ...values.value, [field.id]: (event.target as HTMLInputElement | HTMLSelectElement).value }
}

function hasAnswer(field: FormField): boolean {
  const value = values.value[field.id]
  return Array.isArray(value) ? value.length > 0 : Boolean(value)
}

function canSubmit(): boolean {
  return props.form.fields.every(field => !field.required || hasAnswer(field))
}

function submit() {
  if (!canSubmit()) return
  emit('submit', { ...values.value })
}

function onKeydown(event: KeyboardEvent) {
  if (!/^[1-9]$/.test(event.key)) return
  const index = Number(event.key) - 1
  const target = props.form.fields.find(field => optionsOf(field)[index])
  if (!target) return
  event.preventDefault()
  choose(target, optionsOf(target)[index]!.label)
}
</script>

<template>
  <div class="v2-form" tabindex="0" @keydown="onKeydown">
    <div class="v2-card-head">
      <span class="v2-card-title">{{ form.title || form.question || '请选择' }}</span>
      <span v-if="form.header" class="v2-form-header">{{ form.header }}</span>
    </div>

    <div v-for="field in form.fields" :key="field.id" class="v2-field">
      <span class="v2-field-label">{{ field.label || field.id }}</span>

      <div v-if="field.option_details?.length" class="v2-options">
        <button
          v-for="(option, index) in optionsOf(field)"
          :key="option.label"
          type="button"
          class="v2-option"
          :class="{ selected: isSelected(field, option.label) }"
          :title="option.description || undefined"
          @click="choose(field, option.label)"
        >
          <span class="v2-option-key">{{ index + 1 }}</span>
          <span class="v2-option-text">
            <span class="v2-option-label">{{ option.label }}</span>
            <span v-if="option.description" class="v2-option-desc">{{ option.description }}</span>
          </span>
          <span v-if="option.recommended" class="v2-recommended">推荐</span>
        </button>
      </div>

      <select
        v-else-if="field.options.length"
        :value="values[field.id] ?? ''"
        @change="onLegacyInput(field, $event)"
      >
        <option value="">请选择</option>
        <option v-for="option in field.options" :key="option" :value="option">{{ option }}</option>
      </select>

      <input
        v-else
        :value="values[field.id] ?? ''"
        @input="onLegacyInput(field, $event)"
      />

      <input
        v-if="field.allow_custom && field.option_details?.length"
        class="v2-custom"
        type="text"
        placeholder="自定义答案（可选）"
        @input="onLegacyInput(field, $event)"
      />
    </div>

    <div class="v2-form-hint">按 1–9 快速选择选项</div>
    <div class="v2-card-actions">
      <button class="danger" type="button" @click="emit('cancel')">取消</button>
      <button type="button" :disabled="!canSubmit()" @click="submit">提交</button>
    </div>
  </div>
</template>

<style scoped>
.v2-form {
  outline: none;
}
.v2-card-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.v2-form-header {
  font-size: 10px;
  color: var(--text-muted);
}
.v2-field-label {
  font-size: 11px;
  color: var(--text-secondary);
}
.v2-options {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
}
.v2-option {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
}
.v2-option.selected {
  border-color: var(--accent, var(--primary, var(--warning)));
  background: color-mix(in srgb, var(--warning) 12%, var(--surface));
  color: var(--text-primary);
}
.v2-option-key {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--text-muted);
}
.v2-option-text {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.v2-option-label {
  font-size: 12px;
}
.v2-option-desc {
  font-size: 10px;
  color: var(--text-muted);
}
.v2-recommended {
  flex-shrink: 0;
  padding: 1px 4px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--warning) 22%, transparent);
  color: var(--warning);
  font-size: 10px;
}
.v2-custom {
  margin-top: 6px;
}
.v2-form-hint {
  margin-top: 6px;
  font-size: 10px;
  color: var(--text-muted);
}
</style>
