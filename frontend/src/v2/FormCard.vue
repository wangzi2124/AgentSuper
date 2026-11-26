<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import type { FormField, FormInfo, FormOption } from '../contracts/v2'

const props = defineProps<{ form: FormInfo }>()
const emit = defineEmits<{
  submit: [response: Record<string, unknown>]
  cancel: []
}>()

const values = ref<Record<string, string | string[]>>({})
const customOpen = ref<string | null>(null)
const customText = ref('')
const submitting = ref(false)

watch(
  () => [props.form.id, props.form.revision] as const,
  () => {
    values.value = {}
    customOpen.value = null
    customText.value = ''
    submitting.value = false
  },
)

function optionsOf(field: FormField): FormOption[] {
  return field.option_details?.length
    ? field.option_details
    : field.options.map(label => ({ label, description: '', recommended: false }))
}

const groups = computed(() => props.form.fields.map(field => ({ field, options: optionsOf(field) })))
const needsConfirm = computed(() =>
  props.form.fields.length > 1 || props.form.fields.some(field => field.multiple),
)
const selectedCount = computed(() =>
  Object.values(values.value).reduce((total, value) => total + (Array.isArray(value) ? value.length : value ? 1 : 0), 0),
)

function isSelected(field: FormField, label: string): boolean {
  const value = values.value[field.id]
  return Array.isArray(value) ? value.includes(label) : value === label
}

function hasAnswer(field: FormField): boolean {
  const value = values.value[field.id]
  return Array.isArray(value) ? value.length > 0 : Boolean(value)
}

function canSubmit(): boolean {
  return props.form.fields.every(field => !field.required || hasAnswer(field))
}

function submit() {
  if (submitting.value || !canSubmit()) return
  submitting.value = true
  emit('submit', { ...values.value })
}

function choose(field: FormField, label: string) {
  if (field.multiple) {
    const current = values.value[field.id]
    const list = Array.isArray(current) ? current : current ? [current] : []
    const next = list.includes(label) ? list.filter(item => item !== label) : [...list, label]
    values.value = { ...values.value, [field.id]: next }
  } else {
    values.value = { ...values.value, [field.id]: label }
  }
  customOpen.value = null
  customText.value = ''
  if (!needsConfirm.value) submit()
}

function onLegacyInput(field: FormField, event: Event) {
  values.value = { ...values.value, [field.id]: (event.target as HTMLInputElement | HTMLSelectElement).value }
}

function toggleCustom(field: FormField) {
  customOpen.value = customOpen.value === field.id ? null : field.id
  customText.value = ''
}

function applyCustom(field: FormField) {
  const text = customText.value.trim()
  if (!text) return
  if (field.multiple) {
    const current = values.value[field.id]
    const list = Array.isArray(current) ? current : current ? [current] : []
    if (!list.includes(text)) values.value = { ...values.value, [field.id]: [...list, text] }
  } else {
    values.value = { ...values.value, [field.id]: text }
  }
  customOpen.value = null
  customText.value = ''
  if (!needsConfirm.value) submit()
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && (event.target as HTMLElement | null)?.closest('button')) return
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('cancel')
    return
  }
  if (event.key === 'Enter') {
    event.preventDefault()
    const openField = props.form.fields.find(field => field.id === customOpen.value)
    if (openField) applyCustom(openField)
    else if (!canSubmit() && groups.value.length === 1) {
      const first = groups.value[0]!
      const option = first.options.find(item => item.recommended) ?? first.options[0]
      if (option) choose(first.field, option.label)
    } else submit()
    return
  }
  if (!/^[1-9]$/.test(event.key)) return
  const index = Number(event.key) - 1
  const target = groups.value.find(group => group.options[index])
  const option = target?.options[index]
  if (!target || !option) return
  event.preventDefault()
  choose(target.field, option.label)
}
</script>

<template>
  <div
    class="decision-card"
    role="group"
    :aria-label="form.title || form.question || '请选择'"
    tabindex="0"
    @keydown="onKeydown"
  >
    <header class="decision-head">
      <span v-if="form.header" class="decision-eyebrow">{{ form.header }}</span>
      <p class="decision-question">{{ form.title || form.question || '请选择' }}</p>
    </header>

    <div v-for="group in groups" :key="group.field.id" class="decision-field">
      <span v-if="form.fields.length > 1" class="decision-label">{{ group.field.label || group.field.id }}</span>
      <div class="decision-options">
        <button
          v-for="(option, index) in group.options"
          :key="option.label"
          type="button"
          class="decision-option"
          :class="{ selected: isSelected(group.field, option.label), recommended: option.recommended }"
          :aria-pressed="isSelected(group.field, option.label)"
          @click="choose(group.field, option.label)"
        >
          <span class="decision-index">{{ index + 1 }}</span>
          <span class="decision-copy">
            <strong class="decision-label-text">{{ option.label }}</strong>
            <small v-if="option.description" class="decision-desc">{{ option.description }}</small>
          </span>
          <span v-if="option.recommended" class="decision-tag">推荐</span>
          <svg v-if="isSelected(group.field, option.label)" class="decision-check" viewBox="0 0 16 16" aria-hidden="true">
            <path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </button>

        <button
          v-if="group.field.allow_custom && group.options.length"
          type="button"
          class="decision-option decision-other"
          :class="{ active: customOpen === group.field.id }"
          @click="toggleCustom(group.field)"
        >
          <span class="decision-other-plus">+</span>
          <span class="decision-label-text">其他</span>
        </button>
      </div>

      <input
        v-if="customOpen === group.field.id"
        v-model="customText"
        class="decision-custom"
        type="text"
        placeholder="输入你的答案，按 Enter 发送"
        @keydown.enter.prevent.stop="applyCustom(group.field)"
      />
      <template v-else-if="!group.options.length">
        <select :value="values[group.field.id] ?? ''" @change="onLegacyInput(group.field, $event)">
          <option value="">请选择</option>
          <option v-for="option in group.field.options" :key="option" :value="option">{{ option }}</option>
        </select>
        <input :value="values[group.field.id] ?? ''" @input="onLegacyInput(group.field, $event)" />
      </template>
    </div>

    <footer class="decision-actions">
      <button type="button" class="decision-skip" @click="emit('cancel')">跳过</button>
      <button
        v-if="needsConfirm"
        type="button"
        class="decision-submit"
        :disabled="!canSubmit() || submitting"
        @click="submit"
      >
        确认{{ selectedCount ? ` · ${selectedCount}` : '' }}
      </button>
    </footer>
  </div>
</template>

<style scoped>
.decision-card {
  outline: none;
  border: 1px solid var(--border-subtle);
  border-radius: 14px;
  background: var(--surface-elevated, var(--surface));
  box-shadow: 0 6px 24px rgb(0 0 0 / 8%);
  padding: 14px;
}
.decision-card:focus-visible {
  border-color: var(--primary);
}
.decision-head {
  margin-bottom: 10px;
}
.decision-eyebrow {
  display: block;
  margin-bottom: 4px;
  color: var(--text-muted);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: .06em;
  text-transform: uppercase;
}
.decision-question {
  margin: 0;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  line-height: 1.5;
}
.decision-field + .decision-field {
  margin-top: 12px;
}
.decision-label {
  display: block;
  margin-bottom: 5px;
  color: var(--text-secondary);
  font-size: 11px;
}
.decision-options {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 8px;
}
.decision-option {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  padding: 9px 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text-secondary);
  text-align: left;
  cursor: pointer;
  transition: border-color .12s ease, background .12s ease, transform .12s ease;
}
.decision-option:hover {
  border-color: color-mix(in srgb, var(--primary) 40%, var(--border));
  transform: translateY(-1px);
}
.decision-option:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--primary) 55%, transparent);
  outline-offset: 1px;
}
.decision-option.recommended {
  border-color: color-mix(in srgb, var(--primary) 45%, var(--border));
  background: color-mix(in srgb, var(--primary) 6%, var(--surface));
}
.decision-option.selected {
  border-color: var(--primary);
  background: color-mix(in srgb, var(--primary) 12%, var(--surface));
  color: var(--text-primary);
}
.decision-index {
  flex: 0 0 auto;
  display: inline-grid;
  place-items: center;
  width: 18px;
  height: 18px;
  border-radius: 5px;
  background: var(--surface-elevated, var(--surface));
  color: var(--text-muted);
  font-size: 10px;
  font-variant-numeric: tabular-nums;
}
.decision-copy {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.decision-label-text {
  color: inherit;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
}
.decision-desc {
  color: var(--text-muted);
  font-size: 11px;
  font-weight: 400;
  line-height: 1.45;
}
.decision-tag {
  flex: 0 0 auto;
  color: var(--primary);
  font-size: 10px;
}
.decision-check {
  flex: 0 0 auto;
  width: 14px;
  height: 14px;
  color: var(--primary);
}
.decision-other {
  align-items: center;
  border-style: dashed;
  color: var(--text-muted);
}
.decision-other.active {
  border-color: var(--primary);
  color: var(--primary);
}
.decision-other-plus {
  width: 18px;
  font-size: 14px;
  text-align: center;
}
.decision-custom {
  width: 100%;
  margin-top: 8px;
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface);
  color: var(--text-primary);
  font-size: 12px;
}
.decision-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid var(--border-subtle);
}
.decision-skip,
.decision-submit {
  border: 0;
  background: none;
  cursor: pointer;
  font-size: 11px;
}
.decision-skip {
  color: var(--text-muted);
}
.decision-skip:hover {
  color: var(--text-primary);
}
.decision-submit {
  padding: 5px 12px;
  border-radius: 999px;
  background: var(--primary);
  color: var(--text-on-primary, #fff);
  font-weight: 600;
}
.decision-submit:disabled {
  opacity: .45;
  cursor: default;
}
@media (max-width: 560px) {
  .decision-options {
    grid-template-columns: 1fr;
  }
}
</style>
