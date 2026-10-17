/// <reference types="../../node_modules/.vue-global-types/vue_3.5_0_0_0.d.ts" />
import { ref, computed, nextTick, onBeforeUnmount, watch } from 'vue';
import { useMultiAgentStore } from '../stores/multiAgent';
const emit = defineEmits();
const props = defineProps();
// Gemini 式输入卡底部操作：模型选择下沉到输入框内
const agent = useMultiAgentStore();
// [模型选择] 向上弹出的自绘下拉（原生 select 只能向下且无法自定义样式）
const modelMenuOpen = ref(false);
const tipVisible = ref(false);
const currentModel = computed(() => agent.modelOptions.find(m => m.value === agent.selectedModel) ?? agent.modelOptions[0]);
const currentModelLabel = computed(() => currentModel.value.label);
const currentModelDesc = computed(() => currentModel.value.desc);
function toggleModelMenu() {
    if (props.loading)
        return;
    // 互斥：打开模型菜单时收起 agent 菜单
    if (agentMenuOpen.value)
        agentMenuOpen.value = false;
    modelMenuOpen.value = !modelMenuOpen.value;
}
function pickModel(value) {
    agent.selectedModel = value;
    agent.persistSelectedModel();
    modelMenuOpen.value = false;
}
// [Agent 选择] 对齐 opencode agent modes：顶层只有 build（默认）/ 规划两个命令，
// explore 等 subagent 由委派进入，不再作为用户可选模式
const AGENT_OPTIONS = [
    { value: 'default', icon: '🤖', label: '智能助手', desc: '统一主 Agent（知识库检索 + 代码/文件 + 联网搜索）' },
    { value: 'plan', icon: '📋', label: '规划', desc: '生成结构化实施计划，不执行操作' },
];
const agentMenuOpen = ref(false);
const currentAgent = computed(() => AGENT_OPTIONS.find(a => a.value === agent.agentMode) ?? AGENT_OPTIONS[0]);
function toggleAgentMenu() {
    if (props.loading)
        return;
    // 互斥：打开 agent 菜单时收起模型菜单
    if (modelMenuOpen.value)
        modelMenuOpen.value = false;
    agentMenuOpen.value = !agentMenuOpen.value;
}
function pickAgent(value) {
    agent.agentMode = value;
    agentMenuOpen.value = false;
}
// [录音] 语音输入：实时(PCM)采集 + 本地 Whisper 增量转写（边说话边出字）。
//   AudioContext + ScriptProcessorNode 持续采浮点 PCM，每 ~1.8s 把「新增切片」编码为
//   wav 发 /api/voice/transcribe，结果后缀追加；停止时补转剩余分片。
//   采集初始化失败退回 MediaRecorder（一次性转写），再退回 Web Speech。
//   ⚠ 并发转写会同时拉起多个 Whisper 子进程（内存翻倍），增量调用严格排队串行。
import { transcribeAudio } from '../api/voice';
import { showToast } from '../utils/toast';
// [语音消息] 微信式按住说话 → 上传音频 → 发送真实语音气泡
import { PressToTalkRecorder } from '../voice/recorder';
import { uploadVoiceMessage } from '../api/voice';
const pttRecorder = new PressToTalkRecorder();
const pttHolding = ref(false);
const pttSeconds = ref(0);
let pttTimer = null;
let pttUploading = false;
// ── 统一麦克风按钮：点击=语音转文字，长按=按住说话 ──
let micPointerDownTime = 0;
let micLongPressTimer = null;
const MIC_LONG_PRESS_MS = 300;
function micPointerDown() {
    if (props.loading || pttUploading)
        return;
    if (pttHolding.value)
        return;
    if (listening.value) {
        // [fix] 正在听写：布防「单击=停止」，但不启动长按（避免与实时采集抢麦）。
        // 旧逻辑在此 return → 指针落下不布防，pointerup 里 micLongPressTimer 为 null、
        // pttHolding 为 false，两条分支都不命中，toggleMic() 永不被调用来停止听写。
        micPointerDownTime = Date.now();
        micLongPressTimer = null;
        return;
    }
    micPointerDownTime = Date.now();
    micLongPressTimer = window.setTimeout(() => {
        micLongPressTimer = null;
        // 长按触发 → 进入 PTT 录音模式
        startPtt();
    }, MIC_LONG_PRESS_MS);
}
function micPointerUp() {
    if (micLongPressTimer) {
        // 长按未触发 → 点击 → 切换语音转文字
        clearTimeout(micLongPressTimer);
        micLongPressTimer = null;
        toggleMic();
    }
    else if (pttHolding.value) {
        // PTT 录音中松开 → 停止并发送
        endPtt();
    }
    else if (listening.value) {
        // [fix] 听写状态下的单击 → 停止实时采集/转写
        toggleMic();
    }
}
function micPointerLeave() {
    if (micLongPressTimer) {
        clearTimeout(micLongPressTimer);
        micLongPressTimer = null;
    }
    else if (pttHolding.value) {
        pttCancel();
    }
}
function micPointerCancel() {
    if (micLongPressTimer) {
        clearTimeout(micLongPressTimer);
        micLongPressTimer = null;
    }
    else if (pttHolding.value) {
        pttCancel();
    }
}
async function startPtt() {
    if (props.loading)
        return;
    try {
        // [fix] 只在此回调里标记"正在录音"，不要再清零 pttSeconds：
        // recorder.frameTimer 每 200ms 回调一次，若这里每帧把秒数清零，
        // 会与下方 1s 计时器互相打架 → 红色秒数永远停在 0~1"数不上去"。
        await pttRecorder.start(() => { pttHolding.value = true; });
    }
    catch {
        showToast('麦克风不可用，请检查权限');
        return;
    }
    pttHolding.value = true;
    pttSeconds.value = 0;
    if (pttTimer)
        clearInterval(pttTimer);
    pttTimer = window.setInterval(() => { pttSeconds.value += 1; }, 1000);
}
async function endPtt() {
    const r = await pttRecorder.stop();
    if (pttTimer) {
        clearInterval(pttTimer);
        pttTimer = null;
    }
    pttHolding.value = false;
    if (!r)
        return;
    if (r.duration < 1) {
        showToast('说话时间太短，请按久一点');
        return;
    }
    if (props.loading) {
        showToast('正在处理，请稍候再发送');
        return;
    }
    pttUploading = true;
    try {
        // 先转写音频，再上传——让模型看到语音内容
        let transcript = '';
        try {
            transcript = await transcribeAudio(r.blob, 'voice.webm');
        }
        catch { /* 转写失败不阻塞，降级为占位符 */ }
        const up = await uploadVoiceMessage(r.blob, r.duration, r.peaks, transcript, 'voice.webm');
        if (!up) {
            showToast('语音上传失败');
            return;
        }
        const voice = { id: up.id, url: up.url, duration: up.duration, waveform: up.waveform, text: transcript || up.text || '' };
        const msgText = transcript || '[语音]';
        emit('send', msgText, [], voice);
    }
    catch {
        showToast('语音上传失败');
    }
    finally {
        pttUploading = false;
    }
}
function pttCancel() {
    pttRecorder.cancelNow();
    if (pttTimer) {
        clearInterval(pttTimer);
        pttTimer = null;
    }
    pttHolding.value = false;
}
onBeforeUnmount(() => {
    pttRecorder.cancelNow();
    if (micLongPressTimer) {
        clearTimeout(micLongPressTimer);
        micLongPressTimer = null;
    }
});
const listening = ref(false);
// [录音圈] 开始录音后每秒 +1，套在麦克风外的状态圈显示录音时长
const recordSeconds = ref(0);
let recordTimer = null;
watch(listening, (on) => {
    if (recordTimer) {
        clearInterval(recordTimer);
        recordTimer = null;
    }
    if (on) {
        recordSeconds.value = 0;
        recordTimer = window.setInterval(() => { recordSeconds.value += 1; }, 1000);
    }
});
const recognition = ref(null);
const hasSpeechRecognition = computed(() => typeof window !== 'undefined' && !!window.SpeechRecognition || !!window.webkitSpeechRecognition);
// MediaRecorder 兜底路径（采集可用时不再使用；历史代码保留）
let mediaRecorder = null;
let mediaChunks = [];
// ── 实时采集状态 ──
let liveCtx = null;
let liveSource = null;
let liveProcessor = null;
let liveStream = null;
let liveSamples = new Float32Array(0);
let liveSampleRate = 48000;
let livePending = 0; // 已（排队）交给转写的样本结束下标
let liveTimer = null;
let transcribeChain = Promise.resolve();
let transcribeBusy = false; // 上一块仍在转写时跳本次 tick，避免出字突发/滞后
function appendSamples(data) {
    const n = liveSamples.length;
    const buf = new Float32Array(n + data.length);
    buf.set(liveSamples);
    buf.set(data, n);
    liveSamples = buf;
}
// Float32 PCM → 16bit PCM wav（本地 Whisper 经 ffmpeg 解码 + 重采样）
function pcmToWav(samples, sampleRate) {
    const n = samples.length;
    const buf = new ArrayBuffer(44 + n * 2);
    const v = new DataView(buf);
    const ws = (off, s) => { for (let i = 0; i < s.length; i++)
        v.setUint8(off + i, s.charCodeAt(i)); };
    ws(0, 'RIFF');
    v.setUint32(4, 36 + n * 2, true);
    ws(8, 'WAVE');
    ws(12, 'fmt ');
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true);
    v.setUint16(22, 1, true);
    v.setUint32(24, sampleRate, true);
    v.setUint32(28, sampleRate * 2, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    ws(36, 'data');
    v.setUint32(40, n * 2, true);
    let o = 44;
    for (let i = 0; i < n; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
        o += 2;
    }
    return new Blob([buf], { type: 'audio/wav' });
}
// 增量转写：进入全局串行队列，完成后追加到输入框
function enqueueTranscribe(slice) {
    const blob = pcmToWav(slice, liveSampleRate);
    transcribeBusy = true;
    transcribeChain = transcribeChain.then(async () => {
        try {
            const t = await transcribeAudio(blob, 'live.wav');
            if (t)
                text.value = (text.value ? text.value + ' ' : '') + t;
        }
        catch { /* 单块失败静默，最终块兜底提示 */ }
        transcribeBusy = false;
    });
    return transcribeChain;
}
// 定时切块：每 1.8s 把「上次转写结束点 → 当前」的新增样本交给转写。
// 上一块仍在转写（busy）时直接跳过本次，保证始终最多一个在途转写——
// 串行队列只是「不丢」，但会让人听到话后迟几秒才出字，体感更差。
function tickLiveTranscribe() {
    if (transcribeBusy)
        return;
    const end = liveSamples.length;
    if (end - livePending >= liveSampleRate * 0.9) {
        const slice = liveSamples.slice(livePending, end);
        livePending = end;
        void enqueueTranscribe(slice);
    }
}
async function initLive() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const Ctx = window.AudioContext || window.webkitAudioContext;
        const ctx = new Ctx();
        // Chrome 自动播放策略下 AudioContext 默认 suspended——不 resume() 则
        // onaudioprocess 永不触发（录音全程采不到样本、一字不出）。须在用户手势内恢复。
        try {
            await ctx.resume();
        }
        catch { /* 个别浏览器无需 resume */ }
        liveStream = stream;
        liveCtx = ctx;
        liveSampleRate = ctx.sampleRate || 48000;
        liveSource = ctx.createMediaStreamSource(stream);
        // ScriptProcessorNode 已弃用但全平台可用；0 gain 静音透传以驱动回调
        const proc = ctx.createScriptProcessor(4096, 1, 1);
        const gain = ctx.createGain();
        gain.gain.value = 0;
        proc.onaudioprocess = (e) => {
            // 通道 0，麦克风一般单声道/立体声首位为拾音
            appendSamples(e.inputBuffer.getChannelData(0));
        };
        liveSource.connect(proc);
        proc.connect(gain);
        gain.connect(ctx.destination);
        liveProcessor = proc;
        return true;
    }
    catch {
        cleanupLive();
        return false;
    }
}
function cleanupLive() {
    try {
        liveSource?.disconnect();
    }
    catch { /* noop */ }
    try {
        liveProcessor?.disconnect();
    }
    catch { /* noop */ }
    try {
        liveStream?.getTracks().forEach(t => t.stop());
    }
    catch { /* noop */ }
    if (liveCtx && liveCtx.state !== 'closed') {
        try {
            void liveCtx.close();
        }
        catch { /* noop */ }
    }
    liveSource = null;
    liveProcessor = null;
    liveStream = null;
    liveCtx = null;
}
// 静音检测：近静音切片（无语音内容）直接跳过，避免无意义的空转写与等待
function isSilent(samples) {
    if (!samples.length)
        return true;
    let peak = 0;
    for (let i = 0; i < samples.length; i += 64) {
        const v = Math.abs(samples[i]);
        if (v > peak)
            peak = v;
        if (peak > 0.01)
            return false;
    }
    return peak < 0.008;
}
async function stopLive() {
    if (liveTimer) {
        clearInterval(liveTimer);
        liveTimer = null;
    }
    // 补转剩余分片（先采集，结束点不再增长）
    const end = liveSamples.length;
    if (end > livePending) {
        const tailLen = end - livePending;
        const slice = liveSamples.slice(livePending, end);
        livePending = end;
        // 尾部是静音尾音（如说完话后残留）→ 跳过，避免白跑一次 ~1.3s 转写还返回空；
        // 长度达标或有语音内容则转写，不丢尾部的话。
        if (tailLen >= liveSampleRate * 0.4 || !isSilent(slice)) {
            transcribeChain = transcribeChain.then(async () => {
                try {
                    const t = await transcribeAudio(pcmToWav(slice, liveSampleRate), 'live.wav');
                    if (t)
                        text.value = (text.value ? text.value + ' ' : '') + t;
                }
                catch {
                    showToast('语音转写失败，请重试');
                }
            }).catch(() => { });
        }
    }
    // 立即收尾：不阻塞 stop 等待在途/尾部转写（后台完成后自动追加），界面先恢复可操作
    cleanupLive();
    liveSamples = new Float32Array(0);
    livePending = 0;
    listening.value = false;
}
function initRecognition() {
    const w = window;
    const SR = w.SpeechRecognition || w.webkitSpeechRecognition;
    if (!SR)
        return;
    const r = new SR();
    r.lang = 'zh-CN';
    r.interimResults = false;
    r.maxAlternatives = 1;
    r.onresult = (e) => {
        const t = e.results[0]?.[0]?.transcript || '';
        if (t)
            text.value = (text.value ? text.value + ' ' : '') + t;
        listening.value = false;
    };
    // Web Speech 识别失败（network/not-allowed/service-not-allowed…）→ 自动切本地链路重试
    r.onerror = async () => {
        listening.value = false;
        if (!mediaRecorder)
            await tryStartRecorder(true);
    };
    r.onend = () => { listening.value = false; };
    recognition.value = r;
}
async function stopRecording() {
    // 实时采集路径
    if (liveProcessor || liveSource || liveStream) {
        await stopLive();
        return;
    }
    if (mediaRecorder && mediaRecorder.state === 'recording') {
        mediaRecorder.stop();
    }
    else {
        recognition.value?.stop();
        listening.value = false;
    }
}
async function initRecorder() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const rec = new MediaRecorder(stream);
        mediaRecorder = rec;
        rec.ondataavailable = (e) => { if (e.data.size)
            mediaChunks.push(e.data); };
        rec.onstop = async () => {
            try {
                stream.getTracks().forEach(t => t.stop());
            }
            catch { /* noop */ }
            mediaRecorder = null;
            const blob = new Blob(mediaChunks, { type: rec.mimeType || 'audio/webm' });
            mediaChunks = [];
            try {
                const t = await transcribeAudio(blob);
                if (t)
                    text.value = (text.value ? text.value + ' ' : '') + t;
            }
            catch {
                if (hasSpeechRecognition.value) {
                    if (!recognition.value)
                        initRecognition();
                    try {
                        recognition.value?.start();
                        listening.value = true;
                        return;
                    }
                    catch { /* noop */ }
                }
                showToast('语音转写失败，请重试');
            }
            listening.value = false;
        };
        return true;
    }
    catch {
        return false; // 麦克风权限被拒或不可用
    }
}
async function tryStartRecorder(silent) {
    let rec = mediaRecorder;
    if (!rec) {
        const ok = await initRecorder();
        if (!ok) {
            if (!silent)
                showToast('麦克风不可用，请检查权限');
            return false;
        }
        rec = mediaRecorder;
    }
    try {
        mediaChunks = [];
        rec.start();
        listening.value = true;
        return true;
    }
    catch {
        mediaRecorder = null;
        if (!silent)
            showToast('麦克风不可用，请检查权限');
        return false;
    }
}
async function toggleMic() {
    if (props.loading)
        return;
    if (listening.value) {
        await stopRecording();
        return;
    }
    // 1) 首选：实时采集 + 增量转写
    if (await initLive()) {
        liveSamples = new Float32Array(0);
        livePending = 0;
        transcribeChain = Promise.resolve();
        liveTimer = window.setInterval(tickLiveTranscribe, 1800);
        transcribeBusy = false;
        listening.value = true;
        return;
    }
    // 2) 兜底：MediaRecorder 一次性转写
    if (await tryStartRecorder(true))
        return;
    // 3) 最后：Web Speech（Chrome 已弃用/依赖 Google 服务器，仅尽力）
    if (hasSpeechRecognition.value) {
        if (!recognition.value)
            initRecognition();
        if (recognition.value) {
            try {
                recognition.value.start();
                listening.value = true;
            }
            catch {
                listening.value = false;
                showToast('麦克风不可用，请检查权限');
            }
            return;
        }
    }
    showToast('麦克风不可用，请检查权限');
}
onBeforeUnmount(() => {
    if (liveTimer)
        clearInterval(liveTimer);
    if (recordTimer)
        clearInterval(recordTimer);
    cleanupLive();
    try {
        recognition.value?.stop();
    }
    catch { /* noop */ }
});
// 输入框文本内容
const text = ref('');
// [F8] 待发送附件列表
const files = ref([]);
// [F9] 已发送历史导航：sentHistory 最新在前（索引 0 = 最近一条）
// historyIndex -1 = 当前输入，0..len-1 = 回溯历史；draft 保存离开时的草稿
const sentHistory = ref([]);
const historyIndex = ref(-1);
const draft = ref('');
// 消息长度上限（与后端 ChatRequest.max_length=50_000 对齐，双层约束）
const MAX_LENGTH = 50_000;
// 剩余可输入字符数
const remaining = computed(() => MAX_LENGTH - text.value.length);
// 剩余不足 5000 字符时显示计数器，不足 1000 时红色告警
const showCounter = computed(() => remaining.value < 5000);
const counterDanger = computed(() => remaining.value < 1000);
// F10: 输入框占位文案（loading 时为动态处理文案），修复模板引用未定义 computed
const placeholder = computed(() => props.loading ? '正在处理，请稍候…' : '输入消息，Enter 发送，Shift+Enter 换行');
// F3: textarea 自动增高（min 30px 单行 / max 200px）
function autoResize() {
    const el = textareaRef.value;
    if (!el)
        return;
    el.style.height = 'auto';
    const clamped = Math.min(Math.max(el.scrollHeight, 30), 200);
    el.style.height = clamped + 'px';
}
// [F8] 读取 File → base64（对齐后端 FileContent.data），图片额外生成 data URL 预览
function readFile(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
            const base64 = String(reader.result || '').split(',')[1] || '';
            const pending = {
                id: genId(), filename: file.name, mime_type: file.type || 'application/octet-stream',
                data: base64, size: file.size,
            };
            if (file.type.startsWith('image/')) {
                pending.preview = reader.result;
            }
            resolve(pending);
        };
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}
// [预览] base64 → Uint8Array
function base64ToBytes(base64) {
    const bin = atob(base64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++)
        bytes[i] = bin.charCodeAt(i);
    return bytes;
}
// [预览] base64 → UTF-8 文本
function decodeText(base64) {
    try {
        return new TextDecoder('utf-8').decode(base64ToBytes(base64));
    }
    catch {
        return '';
    }
}
const TEXT_EXTS = ['txt', 'md', 'markdown', 'json', 'csv', 'log', 'js', 'ts', 'py', 'xml', 'html', 'yml', 'yaml', 'ini', 'conf', 'sql', 'sh', 'css', 'toml'];
function isTextLike(f) {
    if (f.mime_type.startsWith('text/'))
        return true;
    const ext = (f.filename.split('.').pop() || '').toLowerCase();
    return TEXT_EXTS.includes(ext);
}
function formatSize(bytes) {
    if (bytes < 1024)
        return `${bytes} B`;
    if (bytes < 1024 * 1024)
        return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
// [预览] 附件预览浮层
const previewFile = ref(null);
const previewText = computed(() => {
    const f = previewFile.value;
    if (!f || !isTextLike(f))
        return '';
    const txt = decodeText(f.data);
    return txt.length > 20000 ? `${txt.slice(0, 20000)}\n\n…（内容过长，仅预览前 20000 字符）` : txt;
});
function openPreview(f) { previewFile.value = f; }
function closePreview() { previewFile.value = null; }
function downloadPending(f) {
    try {
        const blob = new Blob([base64ToBytes(f.data)], { type: f.mime_type });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = f.filename;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    catch {
        // 下载失败静默
    }
}
function genId() {
    try {
        return crypto.randomUUID();
    }
    catch {
        return 'xxxxxxxx'.replace(/[xy]/g, c => { const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16); });
    }
}
// [F8] 加入文件（去重同名同大小 + 上限保护）
async function addFiles(list) {
    if (!list || props.loading)
        return;
    const incoming = Array.from(list);
    for (const f of incoming) {
        // 简单跳空目录占位（File 无 .size===0 但 type 为空且 name 为空视为目录）
        if (!f.name)
            continue;
        // 去重：同名文件不重复添加
        if (files.value.some(p => p.filename === f.name))
            continue;
        // 后端/链路尽力而为，这里对单个文件大小做硬上限保护（50MB）
        if (f.size > 50 * 1024 * 1024)
            continue;
        try {
            const pending = await readFile(f);
            files.value = [...files.value, pending];
        }
        catch {
            // 读取失败静默跳过
        }
    }
}
// [F8] 移除附件
function removeFile(id) {
    files.value = files.value.filter(f => f.id !== id);
}
// 发送消息
function handleSend() {
    // loading 时不清空输入，交给父组件入队
    const msg = text.value;
    if (!msg.trim() && files.value.length === 0)
        return;
    emit('send', msg, files.value.map(({ id: _id, preview: _pv, size: _sz, ...fc }) => fc));
    // [F9] 记录已发送历史（最新在前），重置导航指针
    if (msg.trim()) {
        sentHistory.value = [msg, ...sentHistory.value.filter(m => m !== msg)].slice(0, 50);
    }
    historyIndex.value = -1;
    draft.value = '';
    text.value = '';
    files.value = [];
    // F3: 发送后重置高度
    nextTick(() => autoResize());
    // F6: 发送后焦点恢复
    nextTick(() => textareaRef.value?.focus());
}
// 取消当前请求
function handleCancel() {
    emit('cancel');
}
// [F8] 拖拽上传
function onDrop(e) {
    dragging.value = false;
    if (props.loading)
        return;
    addFiles(e.dataTransfer?.files || null);
}
function onDragOver(e) {
    if (props.loading)
        return;
    e.preventDefault();
    dragging.value = true;
}
function onDragLeave() { dragging.value = false; }
// [F8] 粘贴上传（图片/文件）
function onPaste(e) {
    if (props.loading)
        return;
    const items = e.clipboardData?.items;
    if (!items || items.length === 0)
        return;
    const dropped = [];
    for (const item of Array.from(items)) {
        const f = item.getAsFile?.();
        if (f)
            dropped.push(f);
    }
    if (dropped.length) {
        e.preventDefault();
        addFiles(dropped);
    }
}
// [F8] 文件选择器
const fileInputRef = ref();
function triggerFilePicker() {
    if (props.loading)
        return;
    fileInputRef.value?.click();
}
function onFileInput(e) {
    const input = e.target;
    addFiles(input.files || null);
    input.value = '';
}
const dragging = ref(false);
const canSend = computed(() => !!(text.value.trim() || files.value.length));
// [F9] 历史导航提示：是否处于历史回看状态，及当前位置「N / 总数」
const historyActive = computed(() => historyIndex.value !== -1);
const historyPosition = computed(() => historyActive.value ? `${historyIndex.value + 1} / ${sentHistory.value.length}` : '');
// 退出历史回看，回到草稿
function exitHistory() {
    historyIndex.value = -1;
    text.value = draft.value;
    nextTick(() => autoResize());
    nextTick(() => textareaRef.value?.focus());
}
// [F9] 历史提示写入输入框：回看时在文本域首行插入「历史 N/M」提示（与输入内容一样作为文本展示，发送时仅发送消息本身）
const historyHintText = computed(() => historyActive.value ? `历史 ${historyPosition.value}：` : '');
const displayValue = computed(() => historyHintText.value ? historyHintText.value + '\n' + text.value : text.value);
// 输入框改动：剥离首行历史提示后写入 text（编辑提示本身则退出历史模式）
function onInput(e) {
    const v = e.target.value;
    const pre = historyHintText.value ? historyHintText.value + '\n' : '';
    if (v.startsWith(pre)) {
        text.value = v.slice(pre.length);
    }
    else {
        historyIndex.value = -1;
        draft.value = '';
        text.value = v;
    }
    nextTick(() => autoResize());
}
// [F9] 历史消息导航：↑ 回溯上一条、↓ 前进（最新在前，索引 0 = 最近一条）
function navigateHistory(step) {
    const len = sentHistory.value.length;
    if (!len)
        return;
    // 从当前输入开始时，先保存草稿
    if (historyIndex.value === -1)
        draft.value = text.value;
    let next = historyIndex.value + step;
    next = Math.min(Math.max(next, -1), len - 1);
    historyIndex.value = next;
    text.value = next === -1 ? draft.value : (sentHistory.value[next] || '');
    nextTick(() => autoResize());
    nextTick(() => {
        const el = textareaRef.value;
        if (el)
            el.setSelectionRange(el.value.length, el.value.length);
    });
}
// 键盘事件处理：回车发送，Shift+回车换行；↑/↓ 历史导航
// F1 修复：IME 合成期间（isComposing / keyCode 229）的 Enter 直接忽略，
// 避免中文输入法选词/组句时误触发发送。
function onKeydown(e) {
    if (e.isComposing || e.keyCode === 229)
        return;
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSend();
        return;
    }
    // [F9] Esc 退出历史回看（提示条按钮已移除，改为键盘退出）
    if (e.key === 'Escape' && historyActive.value) {
        e.preventDefault();
        exitHistory();
        return;
    }
    const el = textareaRef.value;
    // F9：仅当光标位于首行（↑）或末行末尾（↓）时才拦截，避免影响多行文本内光标移动
    // 历史回看时，首行被「历史 N/M」提示占据，消息体从提示行之后开始 → 边界按提示长度偏移
    const boundary = historyActive.value && displayValue.value
        ? (displayValue.value.length - text.value.length)
        : 0;
    if (e.key === 'ArrowUp' && el && el.selectionStart <= boundary && el.selectionEnd <= boundary) {
        e.preventDefault();
        navigateHistory(-1);
    }
    else if (e.key === 'ArrowDown' && el && el.selectionStart === el.value.length) {
        e.preventDefault();
        navigateHistory(1);
    }
}
// 设置输入框文本（供父组件调用）
function setText(val) {
    text.value = val;
    nextTick(() => autoResize());
}
// 暴露方法供父组件调用
const __VLS_exposed = {
    setText,
    focus: () => textareaRef.value?.focus(),
    toggleModelMenu: toggleModelMenu,
};
defineExpose(__VLS_exposed);
// 文本域元素引用
const textareaRef = ref();
debugger; /* PartiallyEnd: #3632/scriptSetup.vue */
const __VLS_ctx = {};
let __VLS_components;
let __VLS_directives;
// CSS variable injection 
// CSS variable injection end 
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ onDragover: (__VLS_ctx.onDragOver) },
    ...{ onDragleave: (__VLS_ctx.onDragLeave) },
    ...{ onDrop: (__VLS_ctx.onDrop) },
    ...{ class: "chat-input" },
    ...{ class: ({ dragging: __VLS_ctx.dragging }) },
});
if (__VLS_ctx.files.length) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "file-list" },
    });
    for (const [f] of __VLS_getVForSourceType((__VLS_ctx.files))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.files.length))
                        return;
                    __VLS_ctx.openPreview(f);
                } },
            key: (f.id),
            ...{ class: "file-chip" },
        });
        if (f.preview) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.img)({
                src: (f.preview),
                ...{ class: "file-thumb" },
                alt: "",
            });
        }
        else {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
                ...{ class: "file-icon" },
            });
        }
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "file-name" },
            title: (f.filename),
        });
        (f.filename);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.files.length))
                        return;
                    __VLS_ctx.removeFile(f.id);
                } },
            ...{ class: "file-remove" },
            title: "移除附件",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
            width: "12",
            height: "12",
            viewBox: "0 0 24 24",
            fill: "none",
            stroke: "currentColor",
            'stroke-width': "2.5",
            'stroke-linecap': "round",
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
            d: "M18 6 6 18M6 6l12 12",
        });
    }
}
if (__VLS_ctx.previewFile) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (__VLS_ctx.closePreview) },
        ...{ class: "preview-overlay" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: () => { } },
        ...{ class: "preview-panel" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "preview-head" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "preview-title" },
        title: (__VLS_ctx.previewFile.filename),
    });
    (__VLS_ctx.previewFile.filename);
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (__VLS_ctx.closePreview) },
        ...{ class: "preview-close" },
        title: "关闭",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
        width: "16",
        height: "16",
        viewBox: "0 0 24 24",
        fill: "none",
        stroke: "currentColor",
        'stroke-width': "2.5",
        'stroke-linecap': "round",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
        d: "M18 6 6 18M6 6l12 12",
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "preview-body" },
    });
    if (__VLS_ctx.previewFile.preview) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.img)({
            src: (__VLS_ctx.previewFile.preview),
            ...{ class: "preview-img" },
            alt: "",
        });
    }
    else if (__VLS_ctx.previewText) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.pre, __VLS_intrinsicElements.pre)({
            ...{ class: "preview-text" },
        });
        (__VLS_ctx.previewText);
    }
    else {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "preview-nosupport" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
            ...{ class: "preview-nosupport-icon" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({});
        __VLS_asFunctionalElement(__VLS_intrinsicElements.p, __VLS_intrinsicElements.p)({
            ...{ class: "preview-nosupport-sub" },
        });
    }
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "preview-foot" },
    });
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "preview-meta" },
    });
    (__VLS_ctx.previewFile.mime_type);
    (__VLS_ctx.previewFile.size ? ` · ${__VLS_ctx.formatSize(__VLS_ctx.previewFile.size)}` : '');
    __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.previewFile))
                    return;
                __VLS_ctx.downloadPending(__VLS_ctx.previewFile);
            } },
        ...{ class: "preview-dl" },
    });
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "input-card" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.textarea, __VLS_intrinsicElements.textarea)({
    ...{ onKeydown: (__VLS_ctx.onKeydown) },
    ...{ onInput: (__VLS_ctx.onInput) },
    ...{ onPaste: (__VLS_ctx.onPaste) },
    ref: "textareaRef",
    value: (__VLS_ctx.displayValue),
    maxlength: (__VLS_ctx.MAX_LENGTH),
    placeholder: (__VLS_ctx.placeholder),
    rows: "1",
});
/** @type {typeof __VLS_ctx.textareaRef} */ ;
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "input-actions" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.triggerFilePicker) },
    ...{ class: "attach-btn" },
    disabled: (__VLS_ctx.loading),
    title: "添加附件",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "18",
    height: "18",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "model-wrap" },
    ...{ class: ({ 'menu-open': __VLS_ctx.modelMenuOpen }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.toggleModelMenu) },
    ...{ onMouseenter: (...[$event]) => {
            __VLS_ctx.tipVisible = true;
        } },
    ...{ onMouseleave: (...[$event]) => {
            __VLS_ctx.tipVisible = false;
        } },
    ...{ onFocus: (...[$event]) => {
            __VLS_ctx.tipVisible = true;
        } },
    ...{ onBlur: (...[$event]) => {
            __VLS_ctx.tipVisible = false;
        } },
    type: "button",
    ...{ class: "model-pill" },
    disabled: (__VLS_ctx.loading),
    title: "选择模型",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    ...{ class: "model-icon" },
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "currentColor",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "model-current" },
});
(__VLS_ctx.currentModelLabel);
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    ...{ class: "model-chevron" },
    ...{ class: ({ open: __VLS_ctx.modelMenuOpen }) },
    width: "12",
    height: "12",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2.5",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
    points: "6 9 12 15 18 9",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "model-tip" },
});
__VLS_asFunctionalDirective(__VLS_directives.vShow)(null, { ...__VLS_directiveBindingRestFields, value: (__VLS_ctx.tipVisible && !__VLS_ctx.modelMenuOpen) }, null, null);
__VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
    ...{ class: "model-tip-head" },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "13",
    height: "13",
    viewBox: "0 0 24 24",
    fill: "currentColor",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M12 2l2.4 7.6L22 12l-7.6 2.4L12 22l-2.4-7.6L2 12l7.6-2.4z",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "model-tip-name" },
});
(__VLS_ctx.currentModelLabel);
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "model-tip-desc" },
});
(__VLS_ctx.currentModelDesc);
if (__VLS_ctx.modelMenuOpen) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.modelMenuOpen))
                    return;
                __VLS_ctx.modelMenuOpen = false;
            } },
        ...{ class: "model-menu-mask" },
    });
}
if (__VLS_ctx.modelMenuOpen) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "model-menu" },
    });
    for (const [m] of __VLS_getVForSourceType((__VLS_ctx.agent.modelOptions))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.modelMenuOpen))
                        return;
                    __VLS_ctx.pickModel(m.value);
                } },
            key: (m.value),
            type: "button",
            ...{ class: "model-menu-item" },
            ...{ class: ({ active: __VLS_ctx.agent.selectedModel === m.value }) },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "model-menu-text" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "model-menu-name" },
        });
        (m.label);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "model-menu-desc" },
        });
        (m.desc);
        if (__VLS_ctx.agent.selectedModel === m.value) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
                ...{ class: "check" },
                width: "14",
                height: "14",
                viewBox: "0 0 24 24",
                fill: "none",
                stroke: "currentColor",
                'stroke-width': "3",
                'stroke-linecap': "round",
                'stroke-linejoin': "round",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
                points: "20 6 9 17 4 12",
            });
        }
    }
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "model-wrap agent-wrap" },
    ...{ class: ({ 'menu-open': __VLS_ctx.agentMenuOpen }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.toggleAgentMenu) },
    type: "button",
    ...{ class: "model-pill agent-pill" },
    disabled: (__VLS_ctx.loading),
    title: "选择 Agent",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "agent-icon" },
});
(__VLS_ctx.currentAgent.icon);
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "model-current" },
});
(__VLS_ctx.currentAgent.label);
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    ...{ class: "model-chevron" },
    ...{ class: ({ open: __VLS_ctx.agentMenuOpen }) },
    width: "12",
    height: "12",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2.5",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
    points: "6 9 12 15 18 9",
});
if (__VLS_ctx.agentMenuOpen) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ onClick: (...[$event]) => {
                if (!(__VLS_ctx.agentMenuOpen))
                    return;
                __VLS_ctx.agentMenuOpen = false;
            } },
        ...{ class: "model-menu-mask" },
    });
}
if (__VLS_ctx.agentMenuOpen) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.div, __VLS_intrinsicElements.div)({
        ...{ class: "model-menu agent-menu" },
    });
    for (const [a] of __VLS_getVForSourceType((__VLS_ctx.AGENT_OPTIONS))) {
        __VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
            ...{ onClick: (...[$event]) => {
                    if (!(__VLS_ctx.agentMenuOpen))
                        return;
                    __VLS_ctx.pickAgent(a.value);
                } },
            key: (a.value),
            type: "button",
            ...{ class: "model-menu-item" },
            ...{ class: ({ active: __VLS_ctx.agent.agentMode === a.value }) },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "model-menu-text" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "model-menu-name" },
        });
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "agent-menu-icon" },
        });
        (a.icon);
        (a.label);
        __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
            ...{ class: "model-menu-desc" },
        });
        (a.desc);
        if (__VLS_ctx.agent.agentMode === a.value) {
            __VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
                ...{ class: "check" },
                width: "14",
                height: "14",
                viewBox: "0 0 24 24",
                fill: "none",
                stroke: "currentColor",
                'stroke-width': "3",
                'stroke-linecap': "round",
                'stroke-linejoin': "round",
            });
            __VLS_asFunctionalElement(__VLS_intrinsicElements.polyline)({
                points: "20 6 9 17 4 12",
            });
        }
    }
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "actions-spacer" },
});
if (__VLS_ctx.showCounter) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "char-counter" },
        ...{ class: ({ 'char-counter--danger': __VLS_ctx.counterDanger }) },
    });
    (__VLS_ctx.remaining);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
    ...{ class: "mic-wrap" },
    ...{ class: ({ listening: __VLS_ctx.listening, holding: __VLS_ctx.pttHolding }) },
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onPointerdown: (__VLS_ctx.micPointerDown) },
    ...{ onPointerup: (__VLS_ctx.micPointerUp) },
    ...{ onPointerleave: (__VLS_ctx.micPointerLeave) },
    ...{ onPointercancel: (__VLS_ctx.micPointerCancel) },
    ...{ class: "mic-btn" },
    ...{ class: ({ listening: __VLS_ctx.listening, holding: __VLS_ctx.pttHolding }) },
    disabled: (__VLS_ctx.loading || __VLS_ctx.pttUploading),
    title: "点击语音输入，长按发送语音消息",
    'aria-label': "点击语音输入，长按发送语音消息",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "16",
    height: "16",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M19 10v2a7 7 0 0 1-14 0v-2",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
    x1: "12",
    y1: "19",
    x2: "12",
    y2: "23",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.line)({
    x1: "8",
    y1: "23",
    x2: "16",
    y2: "23",
});
if (__VLS_ctx.listening) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mic-rec-timer" },
    });
    (__VLS_ctx.recordSeconds);
}
else if (__VLS_ctx.pttHolding) {
    __VLS_asFunctionalElement(__VLS_intrinsicElements.span, __VLS_intrinsicElements.span)({
        ...{ class: "mic-rec-timer" },
    });
    (__VLS_ctx.pttSeconds);
}
__VLS_asFunctionalElement(__VLS_intrinsicElements.button, __VLS_intrinsicElements.button)({
    ...{ onClick: (__VLS_ctx.handleSend) },
    ...{ class: "send-btn" },
    disabled: (!__VLS_ctx.canSend),
    title: (__VLS_ctx.loading ? '加入队列（当前任务完成后发送）' : '发送'),
    'aria-label': (__VLS_ctx.loading ? '加入队列' : '发送'),
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.svg, __VLS_intrinsicElements.svg)({
    width: "20",
    height: "20",
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    'stroke-width': "2.4",
    'stroke-linecap': "round",
    'stroke-linejoin': "round",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M12 19V5",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.path)({
    d: "M5 12l7-7 7 7",
});
__VLS_asFunctionalElement(__VLS_intrinsicElements.input)({
    ...{ onChange: (__VLS_ctx.onFileInput) },
    ref: "fileInputRef",
    type: "file",
    multiple: true,
    ...{ style: {} },
});
/** @type {typeof __VLS_ctx.fileInputRef} */ ;
/** @type {__VLS_StyleScopedClasses['chat-input']} */ ;
/** @type {__VLS_StyleScopedClasses['dragging']} */ ;
/** @type {__VLS_StyleScopedClasses['file-list']} */ ;
/** @type {__VLS_StyleScopedClasses['file-chip']} */ ;
/** @type {__VLS_StyleScopedClasses['file-thumb']} */ ;
/** @type {__VLS_StyleScopedClasses['file-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['file-name']} */ ;
/** @type {__VLS_StyleScopedClasses['file-remove']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-overlay']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-panel']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-head']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-title']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-close']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-body']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-img']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-text']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-nosupport']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-nosupport-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-nosupport-sub']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-foot']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-meta']} */ ;
/** @type {__VLS_StyleScopedClasses['preview-dl']} */ ;
/** @type {__VLS_StyleScopedClasses['input-card']} */ ;
/** @type {__VLS_StyleScopedClasses['input-actions']} */ ;
/** @type {__VLS_StyleScopedClasses['attach-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['model-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['menu-open']} */ ;
/** @type {__VLS_StyleScopedClasses['model-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['model-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['model-current']} */ ;
/** @type {__VLS_StyleScopedClasses['model-chevron']} */ ;
/** @type {__VLS_StyleScopedClasses['open']} */ ;
/** @type {__VLS_StyleScopedClasses['model-tip']} */ ;
/** @type {__VLS_StyleScopedClasses['model-tip-head']} */ ;
/** @type {__VLS_StyleScopedClasses['model-tip-name']} */ ;
/** @type {__VLS_StyleScopedClasses['model-tip-desc']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-mask']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-item']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-text']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-name']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-desc']} */ ;
/** @type {__VLS_StyleScopedClasses['check']} */ ;
/** @type {__VLS_StyleScopedClasses['model-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['agent-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['menu-open']} */ ;
/** @type {__VLS_StyleScopedClasses['model-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['agent-pill']} */ ;
/** @type {__VLS_StyleScopedClasses['agent-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['model-current']} */ ;
/** @type {__VLS_StyleScopedClasses['model-chevron']} */ ;
/** @type {__VLS_StyleScopedClasses['open']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-mask']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu']} */ ;
/** @type {__VLS_StyleScopedClasses['agent-menu']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-item']} */ ;
/** @type {__VLS_StyleScopedClasses['active']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-text']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-name']} */ ;
/** @type {__VLS_StyleScopedClasses['agent-menu-icon']} */ ;
/** @type {__VLS_StyleScopedClasses['model-menu-desc']} */ ;
/** @type {__VLS_StyleScopedClasses['check']} */ ;
/** @type {__VLS_StyleScopedClasses['actions-spacer']} */ ;
/** @type {__VLS_StyleScopedClasses['char-counter']} */ ;
/** @type {__VLS_StyleScopedClasses['char-counter--danger']} */ ;
/** @type {__VLS_StyleScopedClasses['mic-wrap']} */ ;
/** @type {__VLS_StyleScopedClasses['listening']} */ ;
/** @type {__VLS_StyleScopedClasses['holding']} */ ;
/** @type {__VLS_StyleScopedClasses['mic-btn']} */ ;
/** @type {__VLS_StyleScopedClasses['listening']} */ ;
/** @type {__VLS_StyleScopedClasses['holding']} */ ;
/** @type {__VLS_StyleScopedClasses['mic-rec-timer']} */ ;
/** @type {__VLS_StyleScopedClasses['mic-rec-timer']} */ ;
/** @type {__VLS_StyleScopedClasses['send-btn']} */ ;
var __VLS_dollars;
const __VLS_self = (await import('vue')).defineComponent({
    setup() {
        return {
            agent: agent,
            modelMenuOpen: modelMenuOpen,
            tipVisible: tipVisible,
            currentModelLabel: currentModelLabel,
            currentModelDesc: currentModelDesc,
            toggleModelMenu: toggleModelMenu,
            pickModel: pickModel,
            AGENT_OPTIONS: AGENT_OPTIONS,
            agentMenuOpen: agentMenuOpen,
            currentAgent: currentAgent,
            toggleAgentMenu: toggleAgentMenu,
            pickAgent: pickAgent,
            pttHolding: pttHolding,
            pttSeconds: pttSeconds,
            pttUploading: pttUploading,
            micPointerDown: micPointerDown,
            micPointerUp: micPointerUp,
            micPointerLeave: micPointerLeave,
            micPointerCancel: micPointerCancel,
            listening: listening,
            recordSeconds: recordSeconds,
            files: files,
            MAX_LENGTH: MAX_LENGTH,
            remaining: remaining,
            showCounter: showCounter,
            counterDanger: counterDanger,
            placeholder: placeholder,
            formatSize: formatSize,
            previewFile: previewFile,
            previewText: previewText,
            openPreview: openPreview,
            closePreview: closePreview,
            downloadPending: downloadPending,
            removeFile: removeFile,
            handleSend: handleSend,
            onDrop: onDrop,
            onDragOver: onDragOver,
            onDragLeave: onDragLeave,
            onPaste: onPaste,
            fileInputRef: fileInputRef,
            triggerFilePicker: triggerFilePicker,
            onFileInput: onFileInput,
            dragging: dragging,
            canSend: canSend,
            displayValue: displayValue,
            onInput: onInput,
            onKeydown: onKeydown,
            textareaRef: textareaRef,
        };
    },
    __typeEmits: {},
    __typeProps: {},
});
export default (await import('vue')).defineComponent({
    setup() {
        return {
            ...__VLS_exposed,
        };
    },
    __typeEmits: {},
    __typeProps: {},
});
; /* PartiallyEnd: #4569/main.vue */
