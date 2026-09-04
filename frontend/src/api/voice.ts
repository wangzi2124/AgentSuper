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

// ASR 转写：音频 Blob → 文本（后端 subprocess → 本地 Whisper）。文件名后缀决定后端临时文件格式（wav/webm/mp3…）
export async function transcribeAudio(blob: Blob, filename = 'record.webm'): Promise<string> {
  const form = new FormData()
  form.append('audio', blob, filename)
  const res = await fetchWithTimeout(`${TTS_BASE}/transcribe`, { method: 'POST', body: form }, 120000)
  const body = await res.json().catch(() => null)
  if (!res.ok || body?.code !== 0) {
    throw new Error(body?.message || `转写失败(${res.status})`)
  }
  return body?.data?.text || ''
}

export interface VoiceMessageUpload {
  id: string
  url: string
  duration: number
  waveform: number[]
  text: string
}

// 语音消息：上传原始音频 → 返回可播放标识（前端先上传，再随消息持久化）
export async function uploadVoiceMessage(
  blob: Blob,
  duration: number,
  waveform: number[] = [],
  text = '',
  filename = 'voice.webm',
): Promise<VoiceMessageUpload> {
  const form = new FormData()
  form.append('audio', blob, filename)
  form.append('duration', String(duration))
  form.append('waveform', waveform.join(','))
  if (text) form.append('text', text)
  const res = await fetchWithTimeout(`${TTS_BASE}/message`, { method: 'POST', body: form }, 120000)
  const body = await res.json().catch(() => null)
  if (!res.ok || body?.code !== 0) {
    throw new Error(body?.message || `语音上传失败(${res.status})`)
  }
  return body?.data as VoiceMessageUpload
}

// 语音消息播放地址（后端按 id 定位文件，历史回放同样适用）
export function voiceAudioUrl(filenameOrId: string): string {
  const name = filenameOrId.includes('/') ? filenameOrId.split('/').pop() : filenameOrId
  return `${TTS_BASE}/audio/${encodeURIComponent(name || '')}`
}

// TTS 合成：文本 → 可播放音频 URL（后端 subprocess → 本地 Qwen3-TTS 预设音色）
export async function synthesize(text: string, speaker: string = TTS_SPEAKER, language: string = 'Auto'): Promise<string> {
  const form = new FormData()
  form.append('text', text)
  form.append('speaker', speaker)
  form.append('language', language)
  const res = await fetchWithTimeout(`${TTS_BASE}/tts`, { method: 'POST', body: form }, 180000)
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.message || `合成失败(${res.status})`)
  }
  const blob = await res.blob()
  return URL.createObjectURL(blob)
}

// 浏览器系统语音朗读（后端/ttsclone 不可达时降级）。
// language 取值与 stores/chatSettings TTS_LANGUAGES / 后端 LANGUAGES 一致。
const NATIVE_LANG_MAP: Record<string, string> = {
  Auto: 'zh-CN', // 保持历史默认；未显式选择时按中文兜底
  Chinese: 'zh-CN',
  English: 'en-US',
  Japanese: 'ja-JP',
  Korean: 'ko-KR',
  French: 'fr-FR',
  German: 'de-DE',
  Spanish: 'es-ES',
  Portuguese: 'pt-PT',
  Russian: 'ru-RU',
  Italian: 'it-IT',
}

export function speakNative(text: string, language: string = 'Auto') {
  try {
    const synth = window.speechSynthesis
    if (!synth) return
    synth.cancel()
    const u = new SpeechSynthesisUtterance(text)
    // [fix] 按「朗读语言」设置选择 BCP-47 语言并尽量匹配对应 voice，
    // 而非此前写死 zh-CN —— 设置英文/日语等时降级朗读也应使用对应语言。
    const lang = NATIVE_LANG_MAP[language] || 'zh-CN'
    u.lang = lang
    try {
      const primary = lang.split('-')[0].toLowerCase()
      const pick = synth.getVoices().find(v => (v.lang || '').toLowerCase().startsWith(primary))
      if (pick) u.voice = pick
    } catch { /* noop */ }
    synth.speak(u)
  } catch { /* noop */ }
}

export function stopNative() {
  try { window.speechSynthesis?.cancel() } catch { /* noop */ }
}
