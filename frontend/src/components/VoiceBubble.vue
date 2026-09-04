<script setup lang="ts">
// 语音消息气泡：波形 + 时长 + 点击播放/暂停（微信式）。
// 全局单例 <audio>，多气泡共享同一播放，点新气泡停旧的。
import { onBeforeUnmount, ref } from 'vue'
import type { VoiceMessageData } from '../types'
import { voiceAudioUrl } from '../api/voice'
import { fetchWithTimeout } from '../api/fetch'

const props = defineProps<{ voice: VoiceMessageData }>()

let sharedAudio: HTMLAudioElement | null = null
let loadUrl = ''
let blobUrl = ''

const playing = ref(false)
const progress = ref(0)

function resolveUrl(): string {
  const u = props.voice?.url || ''
  if (!u) return ''
  if (u.startsWith('/api/voice/audio/')) return u
  if (u.includes('/')) return u
  return voiceAudioUrl(u)
}

async function toggle() {
  const url = resolveUrl()
  if (!url) return
  const audio = sharedAudio
  // 同一段已在此气泡播放 → 暂停复位
  if (audio && loadUrl === url && playing.value) {
    audio.pause()
    playing.value = false
    progress.value = 0
    return
  }
  // 停掉其它/旧播放
  if (audio) {
    audio.pause()
    audio.currentTime = 0
  }
  // 释放旧 blob URL
  if (blobUrl) { URL.revokeObjectURL(blobUrl); blobUrl = '' }

  try {
    // 带鉴权头获取音频 blob（new Audio(url) 不带 auth → 401）
    const res = await fetchWithTimeout(url, {}, 30000)
    if (!res.ok) { playing.value = false; progress.value = 0; return }
    const blob = await res.blob()
    blobUrl = URL.createObjectURL(blob)

    const a = new Audio(blobUrl)
    sharedAudio = a
    loadUrl = url
    playing.value = true
    a.ontimeupdate = () => {
      if (a.duration) progress.value = a.currentTime / a.duration
    }
    a.onended = () => {
      playing.value = false
      progress.value = 0
    }
    a.onerror = () => {
      playing.value = false
      progress.value = 0
    }
    void a.play().catch(() => {
      playing.value = false
      progress.value = 0
    })
  } catch {
    playing.value = false
    progress.value = 0
  }
}

onBeforeUnmount(() => {
  // 仅当本气泡仍持有共享播放时停止，避免误停其它气泡
  if (sharedAudio && playing.value) {
    sharedAudio.pause()
    sharedAudio.currentTime = 0
  }
  if (blobUrl) { URL.revokeObjectURL(blobUrl); blobUrl = '' }
})

const bars = computedBars()

function computedBars(): number[] {
  const w = props.voice?.waveform
  if (w && w.length) return w
  // 无波形时按时长生成占位
  const n = Math.max(8, Math.min(32, Math.round((props.voice?.duration || 2) * 2)))
  return Array.from({ length: n }, (_, i) => 0.4 + 0.5 * Math.abs(Math.sin(i * 1.7)))
}

const durText = (d: number) => `${Math.max(1, Math.round(d))}″`
</script>

<template>
  <button
    class="voice-bubble"
    :class="{ playing }"
    :aria-label="(playing ? '暂停' : '播放') + '，语音时长 ' + durText(voice.duration) + '秒'"
    @click="toggle"
  >
    <span class="voice-icon" :class="{ playing }">{{ playing ? '▐▌' : '▶' }}</span>
    <span class="voice-wave" aria-hidden="true">
      <span
        v-for="(b, i) in bars"
        :key="i"
        class="voice-bar"
        :class="{ lit: progress > 0 && i / bars.length <= progress }"
        :style="{ height: Math.round(20 + b * 60) + '%' }"
      ></span>
    </span>
    <span class="voice-dur">{{ durText(voice.duration) }}</span>
  </button>
</template>

<style scoped>
.voice-bubble {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 128px;
  max-width: 220px;
  padding: 8px 12px;
  border: none;
  border-radius: 12px;
  background: rgba(0, 0, 0, 0.12);
  color: var(--text, inherit);
  cursor: pointer;
  font-family: inherit;
  transition: background var(--duration, 0.2s) var(--ease, ease);
}
.voice-bubble:hover { background: rgba(0, 0, 0, 0.18); }
.voice-bubble.playing { background: rgba(0, 0, 0, 0.22); }
.voice-icon {
  font-size: 11px;
  width: 16px;
  text-align: center;
  flex-shrink: 0;
  color: var(--primary, #4f46e5);
}
.voice-icon.playing { animation: none; }
.voice-wave {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 2px;
  height: 26px;
  min-width: 60px;
  overflow: hidden;
}
.voice-bar {
  flex: 1;
  min-width: 2px;
  border-radius: 2px;
  background: var(--text-secondary, #909399);
  opacity: 0.55;
  transition: background 0.15s ease, opacity 0.15s ease;
}
.voice-bar.lit {
  background: var(--primary, #4f46e5);
  opacity: 1;
}
.voice-dur {
  font-size: 12px;
  color: var(--text-secondary, #909399);
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
</style>
