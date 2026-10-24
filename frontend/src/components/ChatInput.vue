<script setup lang="ts">
import { ref, computed, nextTick, onBeforeUnmount, watch } from 'vue'
import type { FileContent, VoiceMessageData, Skill, Plugin } from '../types'
import { useMultiAgentStore } from '../stores/multiAgent'
import { listSkills, getSkill } from '../api/skills'
import { listPlugins } from '../api/plugins'

// 定义组件事件：发送消息（含可选附件）、取消请求
const emit = defineEmits<{ send: [text: string, files: FileContent[], voice?: VoiceMessageData]; cancel: [] }>()
// 定义组件属性：加载状态
const props = defineProps<{ loading: boolean }>()

// Gemini 式输入卡底部操作：模型选择下沉到输入框内
const agent = useMultiAgentStore()

// [模型选择] 向上弹出的自绘下拉（原生 select 只能向下且无法自定义样式）
const modelMenuOpen = ref(false)
const tipVisible = ref(false)
const currentModel = computed(
  () => agent.modelOptions.find(m => m.value === agent.selectedModel) ?? agent.modelOptions[0]
)
const currentModelLabel = computed(() => currentModel.value.label)
const currentModelDesc = computed(() => currentModel.value.desc)
function toggleModelMenu() {
  if (props.loading) return
  // 互斥：打开模型菜单时收起 agent 菜单
  if (agentMenuOpen.value) agentMenuOpen.value = false
  modelMenuOpen.value = !modelMenuOpen.value
}
async function pickModel(value: string) {
  modelMenuOpen.value = false
  await agent.persistSelectedModel(value)
}

// [Agent 选择] 对齐 opencode agent modes：顶层只有 build（默认）/ 规划两个命令，
// explore 等 subagent 由委派进入，不再作为用户可选模式
const AGENT_OPTIONS = [
  { value: 'default' as const, icon: '🤖', label: '智能助手', desc: '统一主 Agent（知识库检索 + 代码/文件 + 联网搜索）' },
  { value: 'plan' as const, icon: '📋', label: '规划', desc: '生成结构化实施计划，不执行操作' },
]
const agentMenuOpen = ref(false)
const currentAgent = computed(
  () => AGENT_OPTIONS.find(a => a.value === agent.agentMode) ?? AGENT_OPTIONS[0]
)
function toggleAgentMenu() {
  if (props.loading) return
  // 互斥：打开 agent 菜单时收起模型菜单
  if (modelMenuOpen.value) modelMenuOpen.value = false
  agentMenuOpen.value = !agentMenuOpen.value
}
function pickAgent(value: typeof AGENT_OPTIONS[number]['value']) {
  agent.agentMode = value
  agentMenuOpen.value = false
}

// [录音] 语音输入：实时(PCM)采集 + 本地 Whisper 增量转写（边说话边出字）。
//   AudioContext + ScriptProcessorNode 持续采浮点 PCM，每 ~1.8s 把「新增切片」编码为
//   wav 发 /api/voice/transcribe，结果后缀追加；停止时补转剩余分片。
//   采集初始化失败退回 MediaRecorder（一次性转写），再退回 Web Speech。
//   ⚠ 并发转写会同时拉起多个 Whisper 子进程（内存翻倍），增量调用严格排队串行。
import { transcribeAudio } from '../api/voice'
import { showToast } from '../utils/toast'

// [语音消息] 微信式按住说话 → 上传音频 → 发送真实语音气泡
import { PressToTalkRecorder } from '../voice/recorder'
import { uploadVoiceMessage } from '../api/voice'
const pttRecorder = new PressToTalkRecorder()
const pttHolding = ref(false)
const pttSeconds = ref(0)
let pttTimer: number | null = null
let pttUploading = false

// ── 统一麦克风按钮：点击=语音转文字，长按=按住说话 ──
let micPointerDownTime = 0
let micLongPressTimer: number | null = null
const MIC_LONG_PRESS_MS = 300

function micPointerDown() {
  if (props.loading || pttUploading) return
  if (pttHolding.value) return
  if (listening.value) {
    // [fix] 正在听写：布防「单击=停止」，但不启动长按（避免与实时采集抢麦）。
    // 旧逻辑在此 return → 指针落下不布防，pointerup 里 micLongPressTimer 为 null、
    // pttHolding 为 false，两条分支都不命中，toggleMic() 永不被调用来停止听写。
    micPointerDownTime = Date.now()
    micLongPressTimer = null
    return
  }
  micPointerDownTime = Date.now()
  micLongPressTimer = window.setTimeout(() => {
    micLongPressTimer = null
    // 长按触发 → 进入 PTT 录音模式
    startPtt()
  }, MIC_LONG_PRESS_MS)
}

function micPointerUp() {
  if (micLongPressTimer) {
    // 长按未触发 → 点击 → 切换语音转文字
    clearTimeout(micLongPressTimer)
    micLongPressTimer = null
    toggleMic()
  } else if (pttHolding.value) {
    // PTT 录音中松开 → 停止并发送
    endPtt()
  } else if (listening.value) {
    // [fix] 听写状态下的单击 → 停止实时采集/转写
    toggleMic()
  }
}

function micPointerLeave() {
  if (micLongPressTimer) {
    clearTimeout(micLongPressTimer)
    micLongPressTimer = null
  } else if (pttHolding.value) {
    pttCancel()
  }
}

function micPointerCancel() {
  if (micLongPressTimer) {
    clearTimeout(micLongPressTimer)
    micLongPressTimer = null
  } else if (pttHolding.value) {
    pttCancel()
  }
}

async function startPtt() {
  if (props.loading) return
  try {
    // [fix] 只在此回调里标记"正在录音"，不要再清零 pttSeconds：
    // recorder.frameTimer 每 200ms 回调一次，若这里每帧把秒数清零，
    // 会与下方 1s 计时器互相打架 → 红色秒数永远停在 0~1"数不上去"。
    await pttRecorder.start(() => { pttHolding.value = true })
  } catch {
    showToast('麦克风不可用，请检查权限')
    return
  }
  pttHolding.value = true
  pttSeconds.value = 0
  if (pttTimer) clearInterval(pttTimer)
  pttTimer = window.setInterval(() => { pttSeconds.value += 1 }, 1000)
}

async function endPtt() {
  const r = await pttRecorder.stop()
  if (pttTimer) { clearInterval(pttTimer); pttTimer = null }
  pttHolding.value = false
  if (!r) return
  if (r.duration < 1) { showToast('说话时间太短，请按久一点'); return }
  if (props.loading) { showToast('正在处理，请稍候再发送'); return }
  pttUploading = true
  try {
    // 先转写音频，再上传——让模型看到语音内容
    let transcript = ''
    try {
      transcript = await transcribeAudio(r.blob, 'voice.webm')
    } catch { /* 转写失败不阻塞，降级为占位符 */ }

    const up = await uploadVoiceMessage(r.blob, r.duration, r.peaks, transcript, 'voice.webm')
    if (!up) { showToast('语音上传失败'); return }
    const voice: VoiceMessageData = { id: up.id, url: up.url, duration: up.duration, waveform: up.waveform, text: transcript || up.text || '' }
    const msgText = transcript || '[语音]'
    emit('send', msgText, [], voice)
  } catch {
    showToast('语音上传失败')
  } finally {
    pttUploading = false
  }
}

function pttCancel() {
  pttRecorder.cancelNow()
  if (pttTimer) { clearInterval(pttTimer); pttTimer = null }
  pttHolding.value = false
}

onBeforeUnmount(() => {
  pttRecorder.cancelNow()
  if (micLongPressTimer) { clearTimeout(micLongPressTimer); micLongPressTimer = null }
})

const listening = ref(false)
// [录音圈] 开始录音后每秒 +1，套在麦克风外的状态圈显示录音时长
const recordSeconds = ref(0)
let recordTimer: number | null = null
watch(listening, (on) => {
  if (recordTimer) { clearInterval(recordTimer); recordTimer = null }
  if (on) {
    recordSeconds.value = 0
    recordTimer = window.setInterval(() => { recordSeconds.value += 1 }, 1000)
  }
})
const recognition = ref<{ start(): void; stop(): void } | null>(null)
const hasSpeechRecognition = computed(() =>
  typeof window !== 'undefined' && !!((window as any).SpeechRecognition || (window as any).webkitSpeechRecognition)
)
// MediaRecorder 兜底路径（采集可用时不再使用；历史代码保留）
let mediaRecorder: MediaRecorder | null = null
let mediaChunks: Blob[] = []

// ── 实时采集状态 ──
let liveCtx: AudioContext | null = null
let liveSource: MediaStreamAudioSourceNode | null = null
let liveProcessor: ScriptProcessorNode | null = null
let liveStream: MediaStream | null = null
let liveSamples: Float32Array = new Float32Array(0)
let liveSampleRate = 48000
let livePending = 0              // 已（排队）交给转写的样本结束下标
let liveTimer: number | null = null
let transcribeChain: Promise<void> = Promise.resolve()
let transcribeBusy = false  // 上一块仍在转写时跳本次 tick，避免出字突发/滞后
let liveErrorNotified = false  // 本次录音首次转写失败已提示（避免逐块弹窗轰炸）

function appendSamples(data: Float32Array) {
  const n = liveSamples.length
  const buf = new Float32Array(n + data.length)
  buf.set(liveSamples)
  buf.set(data, n)
  liveSamples = buf
}

// Float32 PCM → 16bit PCM wav（本地 Whisper 经 ffmpeg 解码 + 重采样）
function pcmToWav(samples: Float32Array, sampleRate: number): Blob {
  const n = samples.length
  const buf = new ArrayBuffer(44 + n * 2)
  const v = new DataView(buf)
  const ws = (off: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i)) }
  ws(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); ws(8, 'WAVE')
  ws(12, 'fmt '); v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true)
  v.setUint32(24, sampleRate, true); v.setUint32(28, sampleRate * 2, true)
  v.setUint16(32, 2, true); v.setUint16(34, 16, true)
  ws(36, 'data'); v.setUint32(40, n * 2, true)
  let o = 44
  for (let i = 0; i < n; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true)
    o += 2
  }
  return new Blob([buf], { type: 'audio/wav' })
}

// 增量转写：进入全局串行队列，完成后追加到输入框
function enqueueTranscribe(slice: Float32Array) {
  const blob = pcmToWav(slice, liveSampleRate)
  transcribeBusy = true
  transcribeChain = transcribeChain.then(async () => {
    try {
      const t = await transcribeAudio(blob, 'live.wav')
      if (t) text.value = (text.value ? text.value + ' ' : '') + t
    } catch (e) {
      // 首次失败即提示（此前逐块静默 → 后端转写不可用时用户只看到「没有任何反应」）
      if (!liveErrorNotified) {
        liveErrorNotified = true
        showToast(e instanceof Error && e.message ? e.message : '语音转写失败，请重试')
      }
    }
    transcribeBusy = false
  })
  return transcribeChain
}

// 定时切块：每 1.8s 把「上次转写结束点 → 当前」的新增样本交给转写。
// 上一块仍在转写（busy）时直接跳过本次，保证始终最多一个在途转写——
// 串行队列只是「不丢」，但会让人听到话后迟几秒才出字，体感更差。
function tickLiveTranscribe() {
  if (transcribeBusy) return
  const end = liveSamples.length
  if (end - livePending >= liveSampleRate * 0.9) {
    const slice = liveSamples.slice(livePending, end)
    livePending = end
    void enqueueTranscribe(slice)
  }
}

async function initLive(): Promise<boolean> {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const Ctx = window.AudioContext || (window as any).webkitAudioContext
    const ctx = new Ctx()
    // Chrome 自动播放策略下 AudioContext 默认 suspended——不 resume() 则
    // onaudioprocess 永不触发（录音全程采不到样本、一字不出）。须在用户手势内恢复。
    try { await ctx.resume() } catch { /* 个别浏览器无需 resume */ }
    liveStream = stream
    liveCtx = ctx
    liveSampleRate = ctx.sampleRate || 48000
    liveSource = ctx.createMediaStreamSource(stream)
    // ScriptProcessorNode 已弃用但全平台可用；0 gain 静音透传以驱动回调
    const proc = ctx.createScriptProcessor(4096, 1, 1)
    const gain = ctx.createGain()
    gain.gain.value = 0
    proc.onaudioprocess = (e: AudioProcessingEvent) => {
      // 通道 0，麦克风一般单声道/立体声首位为拾音
      appendSamples(e.inputBuffer.getChannelData(0))
    }
    liveSource.connect(proc)
    proc.connect(gain)
    gain.connect(ctx.destination)
    liveProcessor = proc
    return true
  } catch {
    cleanupLive()
    return false
  }
}

function cleanupLive() {
  try { liveSource?.disconnect() } catch { /* noop */ }
  try { liveProcessor?.disconnect() } catch { /* noop */ }
  try { liveStream?.getTracks().forEach(t => t.stop()) } catch { /* noop */ }
  if (liveCtx && liveCtx.state !== 'closed') { try { void liveCtx.close() } catch { /* noop */ } }
  liveSource = null; liveProcessor = null; liveStream = null; liveCtx = null
}

// 静音检测：近静音切片（无语音内容）直接跳过，避免无意义的空转写与等待
function isSilent(samples: Float32Array): boolean {
  if (!samples.length) return true
  let peak = 0
  for (let i = 0; i < samples.length; i += 64) {
    const v = Math.abs(samples[i])
    if (v > peak) peak = v
    if (peak > 0.01) return false
  }
  return peak < 0.008
}

async function stopLive() {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null }
  // 补转剩余分片（先采集，结束点不再增长）
  const end = liveSamples.length
  if (end > livePending) {
    const tailLen = end - livePending
    const slice = liveSamples.slice(livePending, end)
    livePending = end
    // 尾部是静音尾音（如说完话后残留）→ 跳过，避免白跑一次 ~1.3s 转写还返回空；
    // 长度达标或有语音内容则转写，不丢尾部的话。
    if (tailLen >= liveSampleRate * 0.4 || !isSilent(slice)) {
      transcribeChain = transcribeChain.then(async () => {
        try {
          const t = await transcribeAudio(pcmToWav(slice, liveSampleRate), 'live.wav')
          if (t) text.value = (text.value ? text.value + ' ' : '') + t
        } catch {
          showToast('语音转写失败，请重试')
        }
      }).catch(() => {})
    }
  }
  // 立即收尾：不阻塞 stop 等待在途/尾部转写（后台完成后自动追加），界面先恢复可操作
  cleanupLive()
  liveSamples = new Float32Array(0)
  livePending = 0
  listening.value = false
}

function initRecognition() {
  const w = window as any
  const SR = w.SpeechRecognition || w.webkitSpeechRecognition
  if (!SR) return
  const r = new SR()
  r.lang = 'zh-CN'
  r.interimResults = false
  r.maxAlternatives = 1
  r.onresult = (e: any) => {
    const t = e.results[0]?.[0]?.transcript || ''
    if (t) text.value = (text.value ? text.value + ' ' : '') + t
    listening.value = false
  }
  // Web Speech 识别失败（network/not-allowed/service-not-allowed…）→ 自动切本地链路重试
  r.onerror = async () => {
    listening.value = false
    if (!mediaRecorder) await tryStartRecorder(true)
  }
  r.onend = () => { listening.value = false }
  recognition.value = r
}

async function stopRecording() {
  // 实时采集路径
  if (liveProcessor || liveSource || liveStream) { await stopLive(); return }
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop()
  } else {
    recognition.value?.stop()
    listening.value = false
  }
}

async function initRecorder(): Promise<boolean> {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    const rec = new MediaRecorder(stream)
    mediaRecorder = rec
    rec.ondataavailable = (e: BlobEvent) => { if (e.data.size) mediaChunks.push(e.data) }
    rec.onstop = async () => {
      try { stream.getTracks().forEach(t => t.stop()) } catch { /* noop */ }
      mediaRecorder = null
      const blob = new Blob(mediaChunks, { type: rec.mimeType || 'audio/webm' })
      mediaChunks = []
      try {
        const t = await transcribeAudio(blob)
        if (t) text.value = (text.value ? text.value + ' ' : '') + t
      } catch {
        if (hasSpeechRecognition.value) {
          if (!recognition.value) initRecognition()
          try { recognition.value?.start(); listening.value = true; return } catch { /* noop */ }
        }
        showToast('语音转写失败，请重试')
      }
      listening.value = false
    }
    return true
  } catch {
    return false // 麦克风权限被拒或不可用
  }
}

async function tryStartRecorder(silent: boolean): Promise<boolean> {
  let rec = mediaRecorder
  if (!rec) {
    const ok = await initRecorder()
    if (!ok) {
      if (!silent) showToast('麦克风不可用，请检查权限')
      return false
    }
    rec = mediaRecorder
  }
  try {
    mediaChunks = []
    rec!.start()
    listening.value = true
    return true
  } catch {
    mediaRecorder = null
    if (!silent) showToast('麦克风不可用，请检查权限')
    return false
  }
}

async function toggleMic() {
  if (props.loading) return
  if (listening.value) { await stopRecording(); return }
  // 1) 首选：实时采集 + 增量转写
  if (await initLive()) {
    liveSamples = new Float32Array(0)
    livePending = 0
    transcribeChain = Promise.resolve()
    liveTimer = window.setInterval(tickLiveTranscribe, 1800)
    transcribeBusy = false
    liveErrorNotified = false
    listening.value = true
    return
  }
  // 2) 兜底：MediaRecorder 一次性转写
  if (await tryStartRecorder(true)) return
  // 3) 最后：Web Speech（Chrome 已弃用/依赖 Google 服务器，仅尽力）
  if (hasSpeechRecognition.value) {
    if (!recognition.value) initRecognition()
    if (recognition.value) {
      try {
        recognition.value.start()
        listening.value = true
      } catch {
        listening.value = false
        showToast('麦克风不可用，请检查权限')
      }
      return
    }
  }
  showToast('麦克风不可用，请检查权限')
}
onBeforeUnmount(() => {
  if (liveTimer) clearInterval(liveTimer)
  if (recordTimer) clearInterval(recordTimer)
  cleanupLive()
  try { recognition.value?.stop() } catch { /* noop */ }
})

// [F8] 待发送附件：data 为 base64 编码内容（对齐后端 FileContent），
// 图片额外保留 data URL 预览用
interface PendingFile extends FileContent {
  id: string
  preview?: string
  size?: number
}

// 输入框文本内容
const text = ref('')
// [F8] 待发送附件列表
const files = ref<PendingFile[]>([])

// [F9] 已发送历史导航：sentHistory 最新在前（索引 0 = 最近一条）
// historyIndex -1 = 当前输入，0..len-1 = 回溯历史；draft 保存离开时的草稿
const sentHistory = ref<string[]>([])
const historyIndex = ref(-1)
const draft = ref('')

// 消息长度上限（与后端 ChatRequest.max_length=50_000 对齐，双层约束）
const MAX_LENGTH = 50_000

// 剩余可输入字符数
const remaining = computed(() => MAX_LENGTH - text.value.length)
// 剩余不足 5000 字符时显示计数器，不足 1000 时红色告警
const showCounter = computed(() => remaining.value < 5000)
const counterDanger = computed(() => remaining.value < 1000)

// F10: 输入框占位文案（loading 时为动态处理文案），修复模板引用未定义 computed
const placeholder = computed(() =>
  props.loading ? '正在处理，请稍候…' : '输入消息，Enter 发送，Shift+Enter 换行'
)

// F3: textarea 自动增高（min 30px 单行 / max 200px）
function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  const clamped = Math.min(Math.max(el.scrollHeight, 30), 200)
  el.style.height = clamped + 'px'
}

// ── [/ 命令面板] 输入 `/`（行首/空白后，命令名尚未选定）→ 弹出技能 / 插件列表 ──
// 三种顺序均弹出：先描述再补 `/`、先 `/` 再描述、只输入 `/`；描述含空格不关闭面板。
// 交互：按名字实时过滤；↑/↓ 选择；Enter 填入；点条目看完整详情（正文可能被截断）。
type SlashItem = {
  key: string
  kind: 'skill' | 'plugin'
  name: string
  description: string
  enabled: boolean
}
const slashItems = ref<SlashItem[]>([])
const slashLoaded = ref(false)
const slashLoading = ref(false)
const slashError = ref('')
const slashActive = ref(0)
const slashDismissed = ref(false)
const slashDetail = ref<SlashItem | null>(null)
const slashDetailBody = ref('')
const slashDetailLoading = ref(false)

// 触发位置：最后一个「位于行首或空白之后」的 `/`，其后允许继续输入含空格的业务描述。
// 三种输入顺序均触发：先描述后在前面补 `/`、先 `/` 后描述、只输入 `/`。
// 仅行首/空白后触发 → 排除 URL（https://x/y）、词内路径（a/b）等误触发。
const slashTrigger = computed(() => {
  const v = text.value
  if (!v) return -1
  for (let i = v.length - 1; i >= 0; i--) {
    if (v[i] !== '/') continue
    const prev = i > 0 ? v[i - 1] : ''
    if (prev && !/\s/.test(prev)) continue
    if (v[i + 1] === '/') continue // `//` 不触发
    return i
  }
  return -1
})
// `/` 之后的剩余文本；首个空白前的一段是「命令名候选」，其后是业务描述（可含空格）
const slashRest = computed(() => {
  const i = slashTrigger.value
  return i >= 0 ? text.value.slice(i + 1) : ''
})
// 命令已选定 → 收起面板：已输入空白分隔，且首段是已加载的技能/插件名（`/` 后直接跟空白也视为选定）
const slashChosen = computed(() => {
  const rest = slashRest.value
  const sp = rest.search(/\s/)
  if (sp < 0) return false
  const token = rest.slice(0, sp)
  if (!token) return true
  return slashLoaded.value && slashItems.value.some((it) => it.name === token)
})
// 打开：存在触发 `/` 且命令尚未选定（描述里的空格不再关闭面板）
const slashOpen = computed(() => {
  if (props.loading || slashDismissed.value) return false
  if (slashTrigger.value < 0) return false
  return !slashChosen.value
})
// 筛选用词：只取 `/` 后首段（业务描述的空格部分不参与过滤，避免整句匹配不到而空列表）
const slashQuery = computed(() => {
  const rest = slashRest.value
  const sp = rest.search(/\s/)
  return (sp < 0 ? rest : rest.slice(0, sp)).trim().toLowerCase()
})
const slashFiltered = computed(() => {
  const q = slashQuery.value
  if (!q) return slashItems.value
  return slashItems.value.filter(
    (i) => i.name.toLowerCase().includes(q) || i.description.toLowerCase().includes(q),
  )
})

watch(slashOpen, (open) => {
  if (open) {
    slashActive.value = 0
    void loadSlashItems()
  } else {
    slashDetail.value = null
  }
})
watch(slashFiltered, () => { slashActive.value = 0 })

async function loadSlashItems() {
  if (slashLoaded.value || slashLoading.value) return
  slashLoading.value = true
  slashError.value = ''
  try {
    const [skills, plugins] = await Promise.allSettled([listSkills(), listPlugins()])
    const items: SlashItem[] = []
    if (skills.status === 'fulfilled') {
      for (const s of skills.value as Skill[]) {
        items.push({
          key: `skill:${s.name}`, kind: 'skill', name: s.name,
          description: s.description || '', enabled: s.enabled,
        })
      }
    }
    if (plugins.status === 'fulfilled') {
      for (const p of plugins.value as Plugin[]) {
        items.push({
          key: `plugin:${p.name}`, kind: 'plugin', name: p.name,
          description: p.description || '', enabled: p.enabled,
        })
      }
    }
    items.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'skill' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    slashItems.value = items
    slashLoaded.value = true
    if (!items.length) slashError.value = '没有可用的技能或插件'
  } catch (e) {
    slashError.value = e instanceof Error ? e.message : String(e)
  } finally {
    slashLoading.value = false
  }
}

function slashMove(step: number) {
  const n = slashFiltered.value.length
  if (!n) return
  slashActive.value = (slashActive.value + step + n) % n
}

function slashUseDetail() {
  if (slashDetail.value) slashUse(slashDetail.value)
}

// 填入输入框（在触发的 `/` 处替换，保留前缀文字；后接空格表示命令已选定 → 面板自然收起）
function slashUse(item: SlashItem) {
  const i = slashTrigger.value
  const prefix = i >= 0 ? text.value.slice(0, i) : ''
  text.value = `${prefix}/${item.name} `
  slashDetail.value = null
  nextTick(() => {
    autoResize()
    textareaRef.value?.focus()
  })
}

// 点条目看完整详情：技能额外拉取 SKILL.md 正文（列表里只显示截断后的描述）
async function slashShowDetail(item: SlashItem) {
  slashDetail.value = item
  slashDetailBody.value = ''
  if (item.kind !== 'skill') return
  slashDetailLoading.value = true
  try {
    const detail = await getSkill(item.name)
    slashDetailBody.value = detail.content || ''
  } catch (e) {
    slashDetailBody.value = e instanceof Error ? e.message : String(e)
  } finally {
    slashDetailLoading.value = false
  }
}

// [F8] 读取 File → base64（对齐后端 FileContent.data），图片额外生成 data URL 预览
function readFile(file: File): Promise<PendingFile> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const base64 = String(reader.result || '').split(',')[1] || ''
      const pending: PendingFile = {
        id: genId(), filename: file.name, mime_type: file.type || 'application/octet-stream',
        data: base64, size: file.size,
      }
      if (file.type.startsWith('image/')) {
        pending.preview = reader.result as string
      }
      resolve(pending)
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

// [预览] base64 → Uint8Array
function base64ToBytes(base64: string): Uint8Array {
  const bin = atob(base64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return bytes
}

// [预览] base64 → UTF-8 文本
function decodeText(base64: string): string {
  try {
    return new TextDecoder('utf-8').decode(base64ToBytes(base64))
  } catch {
    return ''
  }
}

const TEXT_EXTS = ['txt', 'md', 'markdown', 'json', 'csv', 'log', 'js', 'ts', 'py', 'xml', 'html', 'yml', 'yaml', 'ini', 'conf', 'sql', 'sh', 'css', 'toml']

function isTextLike(f: PendingFile): boolean {
  if (f.mime_type.startsWith('text/')) return true
  const ext = (f.filename.split('.').pop() || '').toLowerCase()
  return TEXT_EXTS.includes(ext)
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// [预览] 附件预览浮层
const previewFile = ref<PendingFile | null>(null)
const previewText = computed(() => {
  const f = previewFile.value
  if (!f || !isTextLike(f)) return ''
  const txt = decodeText(f.data)
  return txt.length > 20000 ? `${txt.slice(0, 20000)}\n\n…（内容过长，仅预览前 20000 字符）` : txt
})
function openPreview(f: PendingFile) { previewFile.value = f }
function closePreview() { previewFile.value = null }
function downloadPending(f: PendingFile) {
  try {
    const blob = new Blob([base64ToBytes(f.data) as BlobPart], { type: f.mime_type })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = f.filename
    a.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch {
    // 下载失败静默
  }
}

function genId(): string {
  try { return crypto.randomUUID() }
  catch { return 'xxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16) }) }
}

// [F8] 加入文件（去重同名同大小 + 上限保护）
async function addFiles(list: FileList | File[] | null) {
  if (!list || props.loading) return
  const incoming = Array.from(list)
  for (const f of incoming) {
    // 简单跳空目录占位（File 无 .size===0 但 type 为空且 name 为空视为目录）
    if (!f.name) continue
    // 去重：同名文件不重复添加
    if (files.value.some(p => p.filename === f.name)) continue
    // 后端/链路尽力而为，这里对单个文件大小做硬上限保护（50MB）
    if (f.size > 50 * 1024 * 1024) continue
    try {
      const pending = await readFile(f)
      files.value = [...files.value, pending]
    } catch {
      // 读取失败静默跳过
    }
  }
}

// [F8] 移除附件
function removeFile(id: string) {
  files.value = files.value.filter(f => f.id !== id)
}

// 发送消息
function handleSend() {
  // loading 时不清空输入，交给父组件入队
  const msg = text.value
  if (!msg.trim() && files.value.length === 0) return
  emit('send', msg, files.value.map(({ id: _id, preview: _pv, size: _sz, ...fc }) => fc))
  // [F9] 记录已发送历史（最新在前），重置导航指针
  if (msg.trim()) {
    sentHistory.value = [msg, ...sentHistory.value.filter(m => m !== msg)].slice(0, 50)
  }
  historyIndex.value = -1
  draft.value = ''
  text.value = ''
  files.value = []
  // F3: 发送后重置高度
  nextTick(() => autoResize())
  // F6: 发送后焦点恢复
  nextTick(() => textareaRef.value?.focus())
}

// 取消当前请求
function handleCancel() {
  emit('cancel')
}

// [F8] 拖拽上传
function onDrop(e: DragEvent) {
  dragging.value = false
  if (props.loading) return
  addFiles(e.dataTransfer?.files || null)
}
function onDragOver(e: DragEvent) {
  if (props.loading) return
  e.preventDefault()
  dragging.value = true
}
function onDragLeave() { dragging.value = false }

// [F8] 粘贴上传（图片/文件）
function onPaste(e: ClipboardEvent) {
  if (props.loading) return
  const items = e.clipboardData?.items
  if (!items || items.length === 0) return
  const dropped: File[] = []
  for (const item of Array.from(items)) {
    const f = item.getAsFile?.()
    if (f) dropped.push(f)
  }
  if (dropped.length) {
    e.preventDefault()
    addFiles(dropped)
  }
}

// [F8] 文件选择器
const fileInputRef = ref<HTMLInputElement>()
function triggerFilePicker() {
  if (props.loading) return
  fileInputRef.value?.click()
}
function onFileInput(e: Event) {
  const input = e.target as HTMLInputElement
  addFiles(input.files || null)
  input.value = ''
}

const dragging = ref(false)
const canSend = computed(() => !!(text.value.trim() || files.value.length))

// [F9] 历史导航提示：是否处于历史回看状态，及当前位置「N / 总数」
const historyActive = computed(() => historyIndex.value !== -1)
const historyPosition = computed(() =>
  historyActive.value ? `${historyIndex.value + 1} / ${sentHistory.value.length}` : ''
)
// 退出历史回看，回到草稿
function exitHistory() {
  historyIndex.value = -1
  text.value = draft.value
  nextTick(() => autoResize())
  nextTick(() => textareaRef.value?.focus())
}

// [F9] 历史提示写入输入框：回看时在文本域首行插入「历史 N/M」提示（与输入内容一样作为文本展示，发送时仅发送消息本身）
const historyHintText = computed(() =>
  historyActive.value ? `历史 ${historyPosition.value}：` : ''
)
const displayValue = computed(() =>
  historyHintText.value ? historyHintText.value + '\n' + text.value : text.value
)
// 输入框改动：剥离首行历史提示后写入 text（编辑提示本身则退出历史模式）
function onInput(e: Event) {
  // 用户继续输入 → 恢复 `/` 面板（此前可能被 Esc/点击遮罩收起）
  slashDismissed.value = false
  const v = (e.target as HTMLTextAreaElement).value
  const pre = historyHintText.value ? historyHintText.value + '\n' : ''
  if (v.startsWith(pre)) {
    text.value = v.slice(pre.length)
  } else {
    historyIndex.value = -1
    draft.value = ''
    text.value = v
  }
  nextTick(() => autoResize())
}

// [F9] 历史消息导航：↑ 回溯上一条、↓ 前进（最新在前，索引 0 = 最近一条）
function navigateHistory(step: number) {
  const len = sentHistory.value.length
  if (!len) return
  // 从当前输入开始时，先保存草稿
  if (historyIndex.value === -1) draft.value = text.value
  let next = historyIndex.value + step
  next = Math.min(Math.max(next, -1), len - 1)
  historyIndex.value = next
  text.value = next === -1 ? draft.value : (sentHistory.value[next] || '')
  nextTick(() => autoResize())
  nextTick(() => {
    const el = textareaRef.value
    if (el) el.setSelectionRange(el.value.length, el.value.length)
  })
}

// 键盘事件处理：回车发送，Shift+回车换行；↑/↓ 历史导航
// F1 修复：IME 合成期间（isComposing / keyCode 229）的 Enter 直接忽略，
// 避免中文输入法选词/组句时误触发发送。
function onKeydown(e: KeyboardEvent) {
  if (e.isComposing || e.keyCode === 229) return
  // [/ 命令面板] 打开时接管 ↑/↓/Enter/Esc（Enter 填入选中项，而不是发送）
  if (slashOpen.value) {
    if (e.key === 'ArrowDown') { e.preventDefault(); slashMove(1); return }
    if (e.key === 'ArrowUp') { e.preventDefault(); slashMove(-1); return }
    if (e.key === 'Escape') { e.preventDefault(); slashDismissed.value = true; return }
    if (e.key === 'Enter' && !e.shiftKey) {
      const item = slashFiltered.value[slashActive.value]
      if (item) { e.preventDefault(); slashUse(item); return }
    }
  }
  if (e.key === 'Escape' && props.loading) {
    e.preventDefault()
    emit('cancel')
    return
  }
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
    return
  }
  // [F9] Esc 退出历史回看（提示条按钮已移除，改为键盘退出）
  if (e.key === 'Escape' && historyActive.value) {
    e.preventDefault()
    exitHistory()
    return
  }
  const el = textareaRef.value
  // F9：仅当光标位于首行（↑）或末行末尾（↓）时才拦截，避免影响多行文本内光标移动
  // 历史回看时，首行被「历史 N/M」提示占据，消息体从提示行之后开始 → 边界按提示长度偏移
  const boundary = historyActive.value && displayValue.value
    ? (displayValue.value.length - text.value.length)
    : 0
  if (e.key === 'ArrowUp' && el && el.selectionStart <= boundary && el.selectionEnd <= boundary) {
    e.preventDefault()
    navigateHistory(-1)
  } else if (e.key === 'ArrowDown' && el && el.selectionStart === el.value.length) {
    e.preventDefault()
    navigateHistory(1)
  }
}

// 设置输入框文本（供父组件调用）
function setText(val: string) {
  text.value = val
  nextTick(() => autoResize())
}

// 暴露方法供父组件调用
defineExpose({
  setText,
  focus: () => textareaRef.value?.focus(),
  toggleModelMenu: toggleModelMenu,
})

// 文本域元素引用
const textareaRef = ref<HTMLTextAreaElement>()
</script>

<template>
  <div class="chat-input" :class="{ dragging }" @dragover.prevent="onDragOver" @dragleave="onDragLeave" @drop.prevent="onDrop">
    <!-- [F8] 附件预览列表（点击查看大图/内容） -->
    <div v-if="files.length" class="file-list">
      <div v-for="f in files" :key="f.id" class="file-chip" @click="openPreview(f)">
        <img v-if="f.preview" :src="f.preview" class="file-thumb" alt="" />
        <span v-else class="file-icon">📄</span>
        <span class="file-name" :title="f.filename">{{ f.filename }}</span>
        <button class="file-remove" title="移除附件" @click.stop="removeFile(f.id)">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
    </div>

    <!-- [预览] 附件预览浮层：图片大图 / 文本内容 / 不支持提示 + 下载 -->
    <div v-if="previewFile" class="preview-overlay" @click.self="closePreview">
      <div class="preview-panel" @click.stop>
        <div class="preview-head">
          <span class="preview-title" :title="previewFile.filename">{{ previewFile.filename }}</span>
          <button class="preview-close" title="关闭" @click="closePreview">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="preview-body">
          <img v-if="previewFile.preview" :src="previewFile.preview" class="preview-img" alt="" />
          <pre v-else-if="previewText" class="preview-text">{{ previewText }}</pre>
          <div v-else class="preview-nosupport">
            <div class="preview-nosupport-icon">📄</div>
            <p>该格式暂不支持直接预览</p>
            <p class="preview-nosupport-sub">可直接发送给模型分析，或点击下方下载查看</p>
          </div>
        </div>
        <div class="preview-foot">
          <span class="preview-meta">{{ previewFile.mime_type }}{{ previewFile.size ? ` · ${formatSize(previewFile.size)}` : '' }}</span>
          <button class="preview-dl" @click="downloadPending(previewFile)">下载</button>
        </div>
      </div>
    </div>
    <!-- [/ 命令面板] 输入 `/` 弹出：技能 + 插件列表，可按名字过滤，点条目看完整详情 -->
    <div v-if="slashOpen" class="slash-mask" @click="slashDismissed = true"></div>
    <div v-if="slashOpen" class="slash-panel">
      <div class="slash-head">
        <span class="slash-title">技能与插件</span>
        <span class="slash-tip">输入名字筛选 · ↑↓ 选择 · Enter 填入 · Esc 关闭</span>
        <button class="slash-close" title="关闭" @click="slashDismissed = true">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>

      <div v-if="slashLoading" class="slash-empty">加载中…</div>
      <div v-else-if="slashError" class="slash-empty">{{ slashError }}</div>
      <div v-else-if="!slashFiltered.length" class="slash-empty">没有匹配的技能或插件</div>
      <div v-else class="slash-list">
        <button
          v-for="(it, i) in slashFiltered"
          :key="it.key"
          type="button"
          class="slash-item"
          :class="{ active: i === slashActive, disabled: !it.enabled }"
          @mouseenter="slashActive = i"
          @click="slashShowDetail(it)"
        >
          <span class="slash-badge" :class="it.kind">{{ it.kind === 'skill' ? '技能' : '插件' }}</span>
          <span class="slash-main">
            <span class="slash-name">
              {{ it.name }}
              <span v-if="!it.enabled" class="slash-off">已禁用</span>
            </span>
            <span class="slash-desc">{{ it.description || '（无描述）' }}</span>
          </span>
          <span class="slash-insert" title="填入输入框" @click.stop="slashUse(it)">↵</span>
        </button>
      </div>

      <!-- 详情：覆盖在列表上，展示完整描述 / 技能正文 -->
      <div v-if="slashDetail" class="slash-detail" @click.stop>
        <div class="slash-detail-head">
          <span class="slash-badge" :class="slashDetail.kind">{{ slashDetail.kind === 'skill' ? '技能' : '插件' }}</span>
          <span class="slash-detail-name">{{ slashDetail.name }}</span>
          <button class="slash-close" title="返回列表" @click="slashDetail = null">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
          </button>
        </div>
        <div class="slash-detail-desc">{{ slashDetail.description || '（无描述）' }}</div>
        <div v-if="slashDetailLoading" class="slash-empty">加载内容…</div>
        <pre v-else-if="slashDetailBody" class="slash-detail-body">{{ slashDetailBody }}</pre>
        <div class="slash-detail-foot">
          <button class="slash-use-btn" @click="slashUseDetail">填入输入框</button>
        </div>
      </div>
    </div>

    <!-- Gemini 风格大圆角输入卡 -->
    <div class="input-card">
      <textarea
        ref="textareaRef"
        :value="displayValue"
        :maxlength="MAX_LENGTH"
        :placeholder="placeholder"
        rows="1"
        @keydown="onKeydown"
        @input="onInput"
        @paste="onPaste"
      ></textarea>
      <div class="input-actions">
        <button class="attach-btn" :disabled="loading" title="添加附件" @click="triggerFilePicker">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
        </button>
        <!-- Gemini 式模型选择胶囊（输入卡底部，向上弹出） -->
        <span class="model-wrap" :class="{ 'menu-open': modelMenuOpen }">
          <button
            type="button"
            class="model-pill"
            :disabled="loading"
            @click.stop="toggleModelMenu"
            @mouseenter="tipVisible = true"
            @mouseleave="tipVisible = false"
            @focus="tipVisible = true"
            @blur="tipVisible = false"
            title="选择模型"
          >
            <svg class="model-icon" width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z"/></svg>
            <span class="model-current">{{ currentModelLabel }}</span>
            <svg class="model-chevron" :class="{ open: modelMenuOpen }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          </button>
          <!-- hover 悬浮说明框（JS 驱动，展开菜单时隐藏） -->
          <div v-show="tipVisible && !modelMenuOpen" class="model-tip">
            <div class="model-tip-head">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z"/></svg>
              <span class="model-tip-name">{{ currentModelLabel }}</span>
            </div>
            <span class="model-tip-desc">{{ currentModelDesc }}</span>
          </div>
          <div v-if="modelMenuOpen" class="model-menu-mask" @click="modelMenuOpen = false"></div>
          <div v-if="modelMenuOpen" class="model-menu">
            <button
              v-for="m in agent.modelOptions"
              :key="m.value"
              type="button"
              class="model-menu-item"
              :class="{ active: agent.selectedModel === m.value }"
              @click="pickModel(m.value)"
            >
              <span class="model-menu-text">
                <span class="model-menu-name">{{ m.label }}</span>
                <span class="model-menu-desc">{{ m.desc }}</span>
              </span>
              <svg v-if="agent.selectedModel === m.value" class="check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            </button>
          </div>
        </span>
        <!-- Agent 选择（样式与模型一致：胶囊 + 向上弹出下拉） -->
        <span class="model-wrap agent-wrap" :class="{ 'menu-open': agentMenuOpen }">
          <button
            type="button"
            class="model-pill agent-pill"
            :disabled="loading"
            @click.stop="toggleAgentMenu"
            title="选择 Agent"
          >
            <span class="agent-icon">{{ currentAgent.icon }}</span>
            <span class="model-current">{{ currentAgent.label }}</span>
            <svg class="model-chevron" :class="{ open: agentMenuOpen }" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
          </button>
          <div v-if="agentMenuOpen" class="model-menu-mask" @click="agentMenuOpen = false"></div>
          <div v-if="agentMenuOpen" class="model-menu agent-menu">
            <button
              v-for="a in AGENT_OPTIONS"
              :key="a.value"
              type="button"
              class="model-menu-item"
              :class="{ active: agent.agentMode === a.value }"
              @click="pickAgent(a.value)"
            >
              <span class="model-menu-text">
                <span class="model-menu-name"><span class="agent-menu-icon">{{ a.icon }}</span> {{ a.label }}</span>
                <span class="model-menu-desc">{{ a.desc }}</span>
              </span>
              <svg v-if="agent.agentMode === a.value" class="check" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            </button>
          </div>
        </span>
        <span class="actions-spacer"></span>
        <span
          v-if="showCounter"
          class="char-counter"
          :class="{ 'char-counter--danger': counterDanger }"
        >{{ remaining }}</span>
        <!-- 统一麦克风按钮：点击=语音转文字，长按=按住说话 -->
        <span class="mic-wrap" :class="{ listening, holding: pttHolding }">
          <button
            class="mic-btn"
            :class="{ listening, holding: pttHolding }"
            :disabled="loading || pttUploading"
            @pointerdown.prevent="micPointerDown"
            @pointerup.prevent="micPointerUp"
            @pointerleave="micPointerLeave"
            @pointercancel="micPointerCancel"
            title="点击语音输入，长按发送语音消息"
            aria-label="点击语音输入，长按发送语音消息"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
          </button>
          <span v-if="listening" class="mic-rec-timer">{{ recordSeconds }}s</span>
          <span v-else-if="pttHolding" class="mic-rec-timer">{{ pttSeconds }}s</span>
        </span>
        <button
          class="send-btn"
          :disabled="!canSend"
          @click="handleSend"
          :title="loading ? '加入队列（当前任务完成后发送）' : '发送'"
          :aria-label="loading ? '加入队列' : '发送'"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>
        </button>
      </div>
    </div>
    <input
      ref="fileInputRef"
      type="file"
      multiple
      style="display: none"
      @change="onFileInput"
    />
  </div>
</template>


<style scoped src="../styles/chat/chatInput.css"></style>
