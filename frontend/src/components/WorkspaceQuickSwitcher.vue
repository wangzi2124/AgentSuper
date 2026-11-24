<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { ConversationMeta } from '../api/sessions'
import { baseName, loadRecentPaths, matchesTokens, normPath, queryTokens } from '../utils/workspaceNav'

type OptionType = 'directory' | 'conversation'

interface Option {
  key: string
  type: OptionType
  label: string
  detail: string
  root?: string
  convId?: string
}

const props = defineProps<{ show: boolean; workspaces: string[]; conversations: ConversationMeta[] }>()
const emit = defineEmits<{
  close: []
  'select-directory': [root: string]
  'select-conversation': [id: string]
  'new-session': [root: string]
}>()

const query = ref('')
const selected = ref(0)
const input = ref<HTMLInputElement | null>(null)

const dirOptions = computed<Option[]>(() => {
  const tokens = queryTokens(query.value)
  const seen = new Set<string>()
  const paths: string[] = []
  for (const p of [...loadRecentPaths(), ...props.workspaces]) {
    const key = normPath(p)
    if (!p || seen.has(key)) continue
    seen.add(key)
    paths.push(p)
  }
  return paths
    .filter(p => matchesTokens(`${p} ${baseName(p)}`, tokens))
    .slice(0, tokens.length ? 6 : 5)
    .map(p => ({ key: `d:${normPath(p)}`, type: 'directory' as const, label: baseName(p), detail: p, root: p }))
})

const convOptions = computed<Option[]>(() => {
  const tokens = queryTokens(query.value)
  return [...props.conversations]
    .sort((a, b) => (a.updated_at < b.updated_at ? 1 : -1))
    .filter(c => matchesTokens(`${c.title} ${c.directory || ''} ${baseName(c.directory || '')}`, tokens))
    .slice(0, tokens.length ? 10 : 6)
    .map(c => ({
      key: `c:${c.id}`,
      type: 'conversation' as const,
      label: c.title,
      detail: c.directory ? baseName(c.directory) : '临时会话',
      convId: c.id,
    }))
})

const options = computed<Option[]>(() => [...dirOptions.value, ...convOptions.value])

function optionIndex(option: Option): number {
  return options.value.indexOf(option)
}

function activate(option: Option) {
  if (!option) return
  if (option.type === 'conversation' && option.convId) emit('select-conversation', option.convId)
  else if (option.root) emit('select-directory', option.root)
  emit('close')
}

function createIn(root: string) {
  emit('new-session', root)
  emit('close')
}

function move(delta: number) {
  if (!options.value.length) return
  selected.value = (selected.value + delta + options.value.length) % options.value.length
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    emit('close')
    return
  }
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    move(1)
    return
  }
  if (event.key === 'ArrowUp') {
    event.preventDefault()
    move(-1)
    return
  }
  if (event.key === 'Enter' && options.value[selected.value]) {
    event.preventDefault()
    activate(options.value[selected.value])
  }
}

watch(query, () => { selected.value = 0 })

watch(() => props.show, async (show) => {
  if (!show) return
  query.value = ''
  selected.value = 0
  await nextTick()
  input.value?.focus()
})
</script>

<template>
  <Teleport to="body">
    <div v-if="show" class="qs-overlay" @mousedown.self="emit('close')">
      <div class="qs-panel" role="dialog" aria-label="快速切换" @keydown="onKeydown">
        <div class="qs-search">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
          <input ref="input" v-model="query" placeholder="搜索目录或会话..." aria-label="搜索目录或会话" />
          <kbd>ESC</kbd>
        </div>

        <div class="qs-list">
          <template v-if="dirOptions.length">
            <div class="qs-section">目录</div>
            <div
              v-for="option in dirOptions"
              :key="option.key"
              class="qs-row"
              :class="{ active: optionIndex(option) === selected }"
              @mousemove="selected = optionIndex(option)"
              @click="activate(option)"
            >
              <svg class="qs-icon folder" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg>
              <span class="qs-label">{{ option.label }}</span>
              <span class="qs-detail" :title="option.detail">{{ option.detail }}</span>
              <button v-if="option.root" class="qs-new" title="在此目录新建会话" @click.stop="createIn(option.root!)">+</button>
            </div>
          </template>

          <template v-if="convOptions.length">
            <div class="qs-section">会话</div>
            <div
              v-for="option in convOptions"
              :key="option.key"
              class="qs-row"
              :class="{ active: optionIndex(option) === selected }"
              @mousemove="selected = optionIndex(option)"
              @click="activate(option)"
            >
              <svg class="qs-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
              <span class="qs-label">{{ option.label }}</span>
              <span class="qs-detail">{{ option.detail }}</span>
            </div>
          </template>

          <div v-if="!options.length" class="qs-empty">没有匹配的目录或会话</div>
        </div>

        <div class="qs-footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> 切换</span>
          <span><kbd>↵</kbd> 打开</span>
          <span><kbd>+</kbd> 新建会话</span>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped src="../styles/chat/workspaceQuickSwitcher.css"></style>
