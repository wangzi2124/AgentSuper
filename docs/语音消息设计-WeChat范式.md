# 语音消息设计（WeChat 范式 · 桌面 + 移动端）

| 项 | 内容 |
|---|---|
| 版本 | v0.1（设计稿，待评审） |
| 日期 | 2026-09-04 |
| 状态 | 📝 设计中（尚未实现） |
| 关联 | `docs/语音与TTS集成设计.md`（现有语音链路）、`docs/移动端聊天交互设计要点-ChatGPT范式.md` |

---

## 一、背景与目标

当前系统的「语音」只有两条链路，均**不产生语音消息**：

1. **语音输入（voice-to-text）**：`ChatInput.vue` 录音 → 实时/一次性转写 → 文字填入输入框 → 用户再点「发送」。产出的是**文字**。
2. **朗读（TTS）**：AI 回复点击「朗读」→ TTS 合成播放。产出的是**临时播放**，不落为消息。

用户希望像**微信发语音**一样：**按住说话 → 录音 → 松开发送一条可播放的语音消息**，在聊天里出现带时长、波形、可点播放的「语音气泡」，桌面端与移动端都覆盖。

目标：

1. 新增「**语音消息**」这一真实的消息类型（可播放的音频条，持久化、可回放）。
2. 交互手势贴近微信：**按住说话 / 上滑取消 / 时长与波形 / 点击播放**。
3. 与现有「语音输入（转文字）」**共存可切换**，而不是互相取代。
4. 复用现有 `app/services/voice.py` 子进程链路与 `/api/voice/*`，不引入新的外部服务。

> 说明：本文档是**设计稿**，不含实现代码。实现落在方案评审通过后另行排期。

---

## 二、现状（代码事实）

| 层 | 现状 | 类型 |
|---|---|---|
| 录音采集 | `ChatInput.vue`：AudioContext + ScriptProcessorNode 实时采集 PCM，每 ~1.8s 切块增量转写（`liveSamples`/`enqueueTranscribe`/`pcmToWav`）；失败退 MediaRecorder 一次性转写，再退 Web Speech | 转文字 |
| 转写 | `api/voice.ts:transcribeAudio(blob)` → `POST /api/voice/transcribe` → 本地 Whisper 子进程 | 文本 |
| 朗读 | `api/voice.ts:synthesize(text, speaker, language)` → `POST /api/voice/tts` → 本地 Qwen3-TTS 子进程，返回 `FileResponse(wav)` | 临时播放 |
| 消息部件 | 前端 `Part.type ∈ {text, reasoning, tool, step-start, step-finish, file, patch, agent, compaction}`（`types/index.ts:26`）；后端 `message_parts.type`（`session/repository.py` `append_part(session_id, message_id, type, data)`） | 无音频 |
| 附件 | `FileContent {filename, data, mime}`（多模态图片/文档）上传入库 | 非音频气泡 |

**结论**：现有 `voice.py` 的「录音采集 + Whisper 转写」能力可直接复用，只缺「**保存原始音频 + 前端语音气泡渲染/播放 + 上滑取消手势**」这三块。

---

## 三、微信语音交互的体验要点（设计基准）

| 交互 | 微信做法 | 我们照搬/适配 |
|---|---|---|
| 触发 | 输入框右侧「按住说话」按钮，长按进入录音 | 照搬（长按 250ms 判定，防误触） |
| 录音 | 按住期间实时录音，按钮区浮现大号「脉冲/波形」提示 | 照搬 |
| 松开发送 | 松手即发送（≥1s 才算有效语音，＜1s 提示「说话时间太短」并丢弃） | 照搬 |
| 上滑取消 | 按住后手指上滑切换到「取消」区，变红，松手取消不发送 | 照搬（移动端触摸；桌面用鼠标按住+上滑/松开在取消区） |
| 时长显示 | 气泡右上角「X″」 | 照搬 |
| 波形 | 气泡内波形（音量柱），播放时波形随进度点亮 | 照搬 |
| 播放 | 点气泡一次播放/暂停，播放中进度推进，播完复位 | 照搬 |
| 未读/红点 | 移动端「听过的变淡，未听的有红点」 | 桌面省略，移动端可选 |
| 连续发送 | 微信默认「按住不能连续快速发」，中间有间距 | 不强求，普通限流即可 |

---

## 四、总体设计

### 4.1 两种语音模式的入口切换

在输入框左侧（或上方）放一个「**语音模式切换**」，在两种模式间切换：

- **模式 A · 语音消息（新增，微信范式）**：按住说话 → 松手发出**语音气泡**。
- **模式 B · 语音输入（现有）**：保持现状，录音转文字填入输入框。

默认进入 **A（语音消息）**。切换状态持久化（localStorage，Key：`agent_super_voice_mode`）。

> 设计取舍：不默认把麦克风从「转文字」改成「语音消息」，避免破坏现有用户习惯，而是**显式切换**并存。

### 4.2 顶层交互状态机

```
idle ──(长按 250ms, 获得麦克风权限)──▶ recording
recording ──(按住中, 每帧更新波形/时长)──▶ recording
recording ──(手指上滑进「取消」区)──▶ cancel-arm
cancel-arm ──(手指滑回录音区)──▶ recording
recording/cancel-arm ──(松开)──▶ (时长≥1s && 非取消) → sending → sent
                              └─(时长<1s || 取消) ─▶ 丢弃, 回到 idle
```

### 4.3 支持的手势（按端）

| 功能 | 桌面（鼠标） | 移动端（触摸） |
|---|---|---|
| 开始录音 | 按住按钮不松 250ms | 长按 250ms |
| 录音中提示 | 按钮原地放大 + 顶部 pills 提示「正在录音 x″」 | 全屏层：大麦克风图标 + 波形 + 读秒 + 「上滑取消」 |
| 上滑取消 | 按住向上拖出按钮区进入取消区（变红「松开取消」） | 手指上滑越过阈值进入顶部红色「松开手指，取消发送」 |
| 取消返回录音 | 拖回按钮区 | 手指下滑回录音区 |
| 松开发送 | 松开鼠标即发送 | 松手即发送 |

> 移动端有自己的**独立录音层**（`van-popup` 全屏或底部大面板），桌面则原地在输入框内做增强提示。二者共享同一套控制器（见 4.4）。

### 4.4 录音控制器（前后端共用逻辑）

新增一个**框架无关的录音控制器 TS 模块** `frontend/src/voice/recorder.ts`（桌面/移动共用）：

- `start(onFrame)` / `stop()`：MediaRecorder 采集 `audio/webm`（或 `audio/ogg`）**原始音频**（不再是声纹转文字，直接收原始 bytes）。
- `onFrame({ seconds, peak, cancelArmed })`：每帧回调给 UI 更新时长/波形/取消态。
- `state: 'idle' | 'recording' | 'cancel-arm' | 'sending'`。
- 波形数据：`AnalyserNode` 每帧取 `getByteTimeDomainData` 或频谱峰值 → 归一化为柱状数组（例如每秒取 1 个柱，或按 100ms 间隔采若干点缓存）。
- **取消语义**：取消 = 丢弃本地 Blob，**不发后端**；发送 = 万一支 Blob → `POST /api/voice/message`（见 5.1）。

复用现有 `pcmToWav`/live 采集思路，但**存原始音频而非转文字**。

### 4.5 语音消息的数据结构

新增前端 `Part`/消息类型：

```ts
// types/index.ts 新增
export interface VoiceMessageData {
  /** 服务端分配的音频标识（相对 URL，如 /api/voice/audio/<id>.webm） */
  url: string
  /** 时长（秒） */
  duration: number
  /** 波形柱（归一化 0..1，播放时逐柱点亮） */
  waveform: number[]
  /** 是否已播放（移动端红点逻辑，可选） */
  played?: boolean
  /** AI 合成语音时附带字幕/文本（可选） */
  text?: string
}
```

- `Part.type` 增加 `'voice'`；`data` 即 `VoiceMessageData`。
- 后端 `message_parts.type` 增加 `'voice'`，`data` 存 `{audio_id, duration, waveform, text?}`；实际音频文件落 `backend/data/voice/`（不在 `data/generated/`，避免被「生成文件」列表混淆）。

---

## 五、后端设计

### 5.1 新增接口（`app/api/voice.py` 扩充，仍走统一信封 + `AuthMiddleware`）

| 端点 | 入参 | 出参 | 说明 |
|---|---|---|---|
| `POST /api/voice/message` | multipart `audio`（webm/ogg）+ form `duration` + `waveform` + 可选 `text` | `{code:0, data:{id, url, duration, waveform}}` | 保存语音消息音频，返回可播放 URL |
| `GET /api/voice/audio/<id>` | — | `FileResponse(audio/webm)` 或 404 | 播放语音消息（供气泡/历史回放） |
| （可选）`DELETE /api/voice/audio/<id>` | — | 删除单条音频 | 撤销/删除消息时清理 |

> 复用 `voice.py` 现有 `_run` subprocess 与 `asyncio.to_thread` 模式；音频**不强制转码**（原样存 webm/ogg，播放无需 ffmpeg 干预，省一次编解码）。若需与后端朗读统一，可选再走一次 `synthesize`/转码，但**默认不做**（微信也不转码）。

- **存储**：`backend/data/voice/<session_id>_<msg_id>.<ext>`，`id` = 文件名（UUID）。路径列入 `PathStore`/workspace 权限宽松区（类似 uploads）。
- **大小/时长限制**：单条 ≤ 60s（对齐微信，避免超大文件）；超 60s 提示「已达上限，请分段」。

### 5.2 持久化与回放

- `_persist_multi_agent`（`chat.py`）在保存 user/assistant 消息时，若消息含 `voice` part，则同步 `message_parts.append_part(type='voice', data={...})`。
- 历史回放：`getConversation`/`loadConversation` 按现状回放 `parts`，`voice` part 回放即用 `url` 播放；`audio/<id>` 由服务端文件留存，会话删除时级联清理（复用 session 删除流程）。

### 5.3 AI 侧的语音回复（对称能力，P2）

- 复用 `tool_tts_synthesize`（已注册，`graphmod/tools.py:207`）生成 AI **朗读**音频。
- 可选增强：AI 回复「语音版」以 `voice` part 附在 assistant 消息上（需 `VOICE_TTS_ENABLED=true` + 模型下载就绪），气泡同款渲染 + 点击播放。默认关闭（`VOICE_TTS_AUTO` 已作为朗读开关，此为其「落为语音消息」的进阶版，P2 再做）。

---

## 六、前端 UI 设计

### 6.1 输入框（ChatInput.vue）

- 麦克风按钮改为**可长按**：`@pointerdown` + 250ms 定时器进入录音；`@pointerup`/`@pointercancel` 结束。`@pointerdown` 需在用户手势内调用 `getUserMedia`（对齐现有 `initLive` 的 `await ctx.resume()` 教训：**必须先 user gesture 内拿权限**）。
- 录音中：按钮外圈脉冲 + 顶部 `pills` 显示「正在录音 x″」；上滑到按钮上方区域出现「取消」色块（变红），松手若在取消区则该次取消。
- 录音时长 < 1s：不发送，toast「说话时间太短」。

### 6.2 语音气泡（新组件 `VoiceBubble.vue`，桌面/移动共用）

```
┌────────────────────────────────┐
│ ■ waveform 波形（柱状，随播放点亮）│  X″
└────────────────────────────────┘
```

- 左侧（user）/右侧（assistant）定位沿用现有气泡对齐。
- 波形：`VoiceMessageData.waveform` 渲染为 N 根柱，按比例缩放；播放时进度柱高亮（`primary` 色），未播完的暗色。
- 播放：`<audio>` 单例（复用 `MultiAgentView` 的 `speakAudio` 模式），点击一次播放/再点暂停；`timeupdate` 驱动波形进度；`ended` 复位。
- 时长：右上角 `X″`；`<10″` 显示为 e.g. `8″`。
- 移动端可选未读红点（`played:false` → 圆点，播放后消除）。
- 深色模式/移动端样式进 `mobile.css`（`VoiceBubble` 新增规则放该文件，符合现有约定）。

### 6.3 移动端（MobileChat → 复用 MultiAgentView + mobile.css）

- 录音层用 Vant：`van-popup`（bottom/全屏）+ 大麦克风 + `van-progress`/自绘波形 + 读秒 + 顶部「松开手指，取消发送」。
- 上滑取消阈值：距顶 <80px 进入取消态，字体变红 + 震动 `navigator.vibrate(30)`（移动端）。
- 全屏录音层 `position:fixed; z-index` 高于设置抽屉（吸取 DirPickerModal 被遮罩教训，z-index ≥ 1200）。

---

## 七、边界与降级

| 场景 | 处理 |
|---|---|
| 麦克风权限拒绝 | 降级为「语音输入（转文字）」Web Speech 兜底；toast 提示 |
| MediaRecorder 不支持 | 降级：退 Web Speech / 提示 |
| 后端 `/api/voice` 不可用/未启用 | `ttsHealth()` 探测 `enabled=false` → 语音消息按钮禁用，toast「语音服务不可用」 |
| 录音 <1s | 丢弃 + toast |
| 超过 60s | 到 `60s` 自动停止并发送（提示「已达上限」），不静默截断 |
| 发送中失败 | 音频已本地持有 → 重试上传；重试 3 次失败则丢弃 + toast |
| 上传 `<id>` 音频被清理 | 气泡播放返回 404 → 显示灰态「文件已失效」 |
| 撤销消息 | `revert` 时级联删除对应 `data/voice/*`（复用 `service.revert` 级联） |

---

## 八、可访问性与细节

- 长按按钮提供 `aria-label`（「按住说话」「正在录音，松开发送，上滑取消」）并动态更新。
- 波形保留 `role="img"` + `aria-label="语音时长 X 秒"`；气泡支持键盘聚焦 + Enter 播放。
- 读秒用 `tabular-nums`（对齐现有 `.step-time`），避免跳动。
- 深色模式：波形/层用 CSS 变量（`var(--primary)`/`var(--surface)`），不写死色值。

---

## 九、落地步骤（方案评审通过后）

1. **P0 基础**：`frontend/src/voice/recorder.ts` 控制器 + `VoiceBubble.vue` + 输入框长按手势（桌面优先）。
2. **P0 后端**：`/api/voice/message` + `/api/voice/audio/<id>` + `data/voice/` 存储 + `message_parts` 增加 `voice` 类型 + 持久化/回放。
3. **P1 移动端**：全屏 Vant 录音层 + 上滑取消 + 红点 + `mobile.css` 适配。
4. **P1 完善**：<1s / 60s 上限、失败重试、音频清理级联、可访问性细节。
5. **P2 进阶**：AI 语音回复落为语音气泡（`tool_tts_synthesize` → assistant `voice` part）。
6. **验证**：桌面 + 移动各跑一遍交互状态机矩阵；`npm run build` + `npm test`（37 例保持绿）；`scripts/voice_quickcheck.py all` 确认后端语音可用。

---

## 十、风险与取舍

| 风险 | 缓解 |
|---|---|
| 微信式长按与现有「点击录音」冲突 | 显式「模式 A/B」切换，默认 A，不影响现有转文字 |
| 桌面鼠标「按住+上滑」体验不如触屏 | 桌面提供额外「点击开录/点击停止」备选手势（无障碍） |
| 音频文件越积越多 | 时长上限 + 撤销/删除级联清理 + 定期 TTL 清理（对齐 `cleanup_truncated`） |
| 与 `voice.py` 转写双路径漂移 | 录音控制器单一实现；`/api/voice/message` 是新增端点，不改造现有 transcribe |
| 播放并发（多条同时点） | 单例 `<audio>`，点新气泡先停旧的（复用 `speakAudio` 模式） |
