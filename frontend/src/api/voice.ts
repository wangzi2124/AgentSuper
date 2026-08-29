// 语音能力封装：后端 /api/voice/*（subprocess 驱动本地 Qwen3-TTS，无外部 HTTP 直连）
// 录音/朗读都走本后端（带鉴权头），服务不可用时降级浏览器 speechSynthesis。
import { fetchWithTimeout } from './fetch'

const TTS_BASE = (import.meta.env.VITE_TTS_BASE as string) || '/api/voice'
const TTS_SPEAKER = (import.meta.env.VITE_TTS_SPEAKER as string) || 'Vivian'

export interface TtsHealth {
  ok: boolean
  enabled?: boolean
  speakers?: string[]
  languages?: string[]
}

export async function ttsHealth(): Promise<TtsHealth> {
  try {
    const res = await fetchWithTimeout(`${TTS_BASE}/status`, {}, 3000)
    if (!res.ok) return { ok: false }
    const body = await res.json()
    const data = body?.data ?? {}
    return { ok: body?.code === 0, enabled: !!data.enabled, speakers: data.speakers, languages: data.languages }
  } catch {
    return { ok: false }
  }
}

// ASR 转写：录音 Blob → 文本（后端 subprocess → 本地 Whisper）
export async function transcribeAudio(blob: Blob): Promise<string> {
  const form = new FormData()
  form.append('audio', blob, 'record.webm')
  const res = await fetchWithTimeout(`${TTS_BASE}/transcribe`, { method: 'POST', body: form }, 120000)
  const body = await res.json().catch(() => null)
  if (!res.ok || body?.code !== 0) {
    throw new Error(body?.message || `转写失败(${res.status})`)
  }
  return body?.data?.text || ''
}

// TTS 合成：文本 → 可播放音频 URL（后端 subprocess → 本地 Qwen3-TTS 预设音色）
export async function synthesize(text: string, speaker: string = TTS_SPEAKER): Promise<string> {
  const form = new FormData()
  form.append('text', text)
  form.append('speaker', speaker)
  form.append('language', 'Auto')
  const res = await fetchWithTimeout(`${TTS_BASE}/tts`, { method: 'POST', body: form }, 180000)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.message || `合成失败(${res.status})`)
  }
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

// 浏览器系统语音朗读（后端/ttsclone 不可达时降级）
export function speakNative(text: string) {
  try {
    const synth = window.speechSynthesis
    if (!synth) return
    synth.cancel()
    const u = new SpeechSynthesisUtterance(text)
    u.lang = 'zh-CN'
    synth.speak(u)
  } catch { /* noop */ }
}

export function stopNative() {
  try { window.speechSynthesis?.cancel() } catch { /* noop */ }
}
