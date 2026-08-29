// 语音能力封装：本地 Qwen3-TTS 服务（ttsclone）+ 浏览器系统语音降级
// dev：vite proxy `/tts-api` → http://localhost:7861（规避 CORS）
// prod：由部署层将 `/tts-api` 代理到 ttsclone 服务（或用 VITE_TTS_BASE 指向完整地址）

const TTS_BASE = (import.meta.env.VITE_TTS_BASE as string) || '/tts-api'
const TTS_SPEAKER = (import.meta.env.VITE_TTS_SPEAKER as string) || 'Vivian'

export interface TtsHealth {
  ok: boolean
  speakers?: string[]
  languages?: string[]
  gpu?: string
  device?: string
}

export async function ttsHealth(): Promise<TtsHealth> {
  try {
    const res = await fetch(`${TTS_BASE}/health`)
    if (!res.ok) return { ok: false }
    const data = await res.json()
    return { ok: !!data.ok, speakers: data.speakers, languages: data.languages, gpu: data.gpu, device: data.device }
  } catch {
    return { ok: false }
  }
}

// ASR 转写：录音 Blob → 文本（Qwen3-TTS Whisper）
export async function transcribeAudio(blob: Blob): Promise<string> {
  const form = new FormData()
  form.append('audio', blob, 'record.webm')
  const res = await fetch(`${TTS_BASE}/api/tts/transcribe`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`转写失败(${res.status})`)
  const data = await res.json()
  if (!data.ok) throw new Error(data.error || '转写失败')
  return data.text || ''
}

// TTS 合成：文本 → 可播放音频 URL（预设音色）
export async function synthesize(text: string, speaker: string = TTS_SPEAKER): Promise<string> {
  const form = new FormData()
  form.append('text', text)
  form.append('speaker', speaker)
  form.append('language', 'Auto')
  const res = await fetch(`${TTS_BASE}/api/tts/custom`, { method: 'POST', body: form })
  if (!res.ok) throw new Error(`合成失败(${res.status})`)
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

// 浏览器系统语音朗读（ttsclone 不可达时降级）
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
