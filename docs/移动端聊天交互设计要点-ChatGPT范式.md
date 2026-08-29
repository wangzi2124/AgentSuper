# 移动端聊天交互设计要点 — ChatGPT 范式（用户滚动优先 / 回到底部 / 简洁布局 / 功能收纳）

版本 v1.2 / 整理日期：2026-08-29（v1.0 基线）
整理依据：ChatGPT 移动端交互范式 × 项目知识库（README.md、AGENTS.md、docs/优化行动计划.md、docs/移动端UI改版源码修改说明.md、mobile.css、MultiAgentView.vue 源码事实）

> **v1.2 变更记录（2026-08-29）**
> 1. 新增「原则四：功能收纳」：移动端聊天头部占用控件（模型选择 `.model-selector` / 向量库检索 `.toggle` / 天气预警 `.weather-alert-container` / 工作目录 `.ws-manager` / 清空会话 `.chat-footer .btn-danger`）全部收纳进左侧抽屉「设置」分组，聊天框只留消息流 + 流式状态 badge，视觉对齐 ChatGPT。核查实锤：`MobileShell.vue`（`@@CHAT_PANEL_SCRIPT@@` / `@@CHAT_PANEL_TEMPLATE@@` / `@@CHAT_PANEL_STYLE@@` 三处注入）+ `mobile.css` 尾部（`@@CHAT_PANEL_CSS@@`，`@media (max-width:768px)` 隐藏规则）+ `scripts/upgrade_chat_panel.py` + `multiAgent.ts` L46 类型放宽（`ref<string>`）。
> 2. 同步修正：一、设计原则表新增第 4 行；四章 4.1/4.3 头部描述（工具条 → 标题 + 状态 badge）；6.1 对齐矩阵 / 6.2 落地建议 / 6.3 修改入口约束各加一项；七、结论新增第 4 条。
> 3. v1.1 变更记录并入历史（原则二回到底部按钮已落地，见第三节）。
> 4. 章节编号顺延：原则四 = 第五章；原第五章知识库对齐矩阵 = 第六章；原第六章结论 = 第七章。

---

## 一、四大设计原则与项目现状映射

| # | ChatGPT 设计原则 | 核心含义 | 项目现状 | 缺口 |
|---|---|---|---|---|
| 1 | **用户滚动优先** | 用户一旦滚动/触摸，自动滚动立即让位，绝不强制滚动 | ✅ 已落地（`isNearBottom` 智能跟随） | 无实质缺口，需固化规范 |
| 2 | **回到底部按钮** | 用户上翻后，底部浮现「↓ 回到底部」按钮，一键回到最新 | ✅ 已落地（`showScrollBtn` + `isTrusted`，详见第三节） | 需固化规范 |
| 3 | **简洁整体布局** | 单栏消息流、输入区固定底部、头部极简、留白充足 | ✅ 已落地（mobile.css 规范） | 细节收尾 |
| 4 | **功能收纳** | 聊天框只留对话，功能设置入左侧抽屉，不侵入消息流 | ✅ 已落地（`MobileShell.vue` 抽屉「设置」分组 + `mobile.css` 隐藏规则，见第五节） | 需固化规范 |

---

## 二、原则一：用户滚动优先 — 成熟交互模式要点

> 知识库现状事实：README L37「聊天消息列表智能自动滚动（靠近底部才跟随），用户上翻浏览时不强制滚动」；实现见 `MultiAgentView.vue`。

### 2.1 现有实现（成熟模式，建议固化）

| 机制 | 实现细节（源码事实） | 要点 |
|---|---|---|
| 跟随阈值 | `isNearBottom = scrollHeight - scrollTop - clientHeight < 100`（MultiAgentView.vue onScroll） | 距底部 100px 内视为"在底部"，才允许自动跟随 |
| 触发时机 | 三个 watcher：`messages.length` / 最后消息 `content` / 最后消息 agent `steps.length` 任一变化 → `nextTick` → 跟随 | 覆盖「新消息到达」「流式增量」「工具步骤推进」三类更新 |
| 滚动方式 | `scrollTo({ top: scrollHeight, behavior: 'smooth' })` | 平滑滚动，禁止生硬跳动 |
| 用户介入 | `@scroll="onScroll"` 实时重算 `isNearBottom` | 用户上翻 → isNearBottom=false → 自动跟随立即暂停 ✅ 天然满足"用户滚动优先" |

### 2.2 设计要点清单（ChatGPT 范式校准）

1. **触摸/拖拽即让位**：任何用户滚动输入都立即取消自动跟随（现有 onScroll 天然满足，需保持）。
2. **滚动到底即恢复**：用户滚回底部（重新进入 100px 阈值）后，自动跟随自动恢复，无需额外操作。
3. **生成中才跟随，静止不打扰**：仅在流式生成/工具执行期间跟随；历史回放浏览时不自动滚动。
4. **平滑优先**：一律 `behavior:'smooth'`；iOS 上注意 `scroll-behavior` 与惯性滚动（`-webkit-overflow-scrolling: touch`）兼容。
5. **性能**：跟随逻辑只依赖轻量 watcher + 阈值判断，不做滚动监听节流外的重计算；大批量增量渲染依赖 F2 `part.delta` 真增量（见第六章）。

---

## 三、原则二：回到底部按钮 — 已落地模式设计要点

> 现状：`MultiAgentView.vue` 已实现悬浮「回到底部」按钮（`showScrollBtn = !isNearBottom && messages.length > 0`，`scrollToBottom()` 平滑回底并恢复跟随），样式见 `mobile.css` L481-506（第 6 轮触控适配，含 `@@CHAT_SCROLL_INJECTED@@` 标记），升级入口为 `scripts/upgrade_chat_scroll.py`（独立于 `upgrade_mobile_ui.py`）。用户上翻离开底部即淡入，点击一键回底并恢复自动跟随。以下为设计要点规范，与实现对齐后固化为知识库约束。

### 3.1 交互规范

| 项 | 设计要点 |
|---|---|
| **触发条件** | `isNearBottom === false`（用户离开底部）且存在「新内容」——流式生成中 / 新消息到达 / 未读数 > 0 |
| **显示时机** | 触发条件满足即淡入（CSS transition 150~200ms）；用户回到底部或点击后淡出 |
| **位置** | 消息列表右下角悬浮；避开输入区，底部距输入区上缘 ≥ 12px；右侧距边缘 16px |
| **形态** | 圆形胶囊（40~44px，触控友好）、毛玻璃底（`backdrop-filter: blur(10px)`）、品牌色 `--m-brand` 箭头图标（Vant `arrow-down`）、可选未读数 badge |
| **点击行为** | `scrollTo({ top: scrollHeight, behavior: 'smooth' })` → 恢复自动跟随 → 按钮淡出 |
| **层级** | z-index 高于 `.message-list`、低于 `.chat-header` 与输入区；不遮挡气泡内容（气泡 max-width 88%，右侧留白天然避让） |
| **无障碍** | `aria-label="回到底部"`、`role="button"`；支持键盘/读屏触发 |
| **深色模式** | 深色下用 `--m-card-bg`（#1c1f2b）毛玻璃底，不引入硬编码浅色（对齐 U2 规范） |

### 3.2 边界情况

1. **生成中上翻**：按钮常驻显示，badge 显示累积未读数；点击即跳到最新继续跟随。
2. **无新内容上翻**：仅浏览历史（非生成态）时不显示按钮，避免干扰（与 ChatGPT 一致）。
3. **短内容不显示**：消息列表高度不足一屏（`scrollHeight <= clientHeight`）时永不显示。

---

## 四、原则三：简洁整体布局 — 知识库规范清单

> 来源：mobile.css（第 5 轮微调）、docs/移动端UI改版源码修改说明.md、docs/移动端聊天UI规范检索报告.md 3.1/3.2。

### 4.1 页面骨架（适配架构）

| 项 | 规范 |
|---|---|
| 复用策略 | 移动端聊天页复用桌面端 `MultiAgentView`，移动适配全部由 `mobile.css` 覆盖（`!important` 声明级），不另开 scoped |
| 断点 | 768px / 480px |
| 头部 | 隐藏重复 `.chat-header` 标题、压缩为工具条；隐藏 `.ws-manager`；工具条允许换行。移动端另隐藏 `.model-selector` / `.toggle` / `.weather-alert-container` / `.chat-footer .btn-danger`（原则四，见第五节） |
| 输入 | iOS 输入框字号固定 16px（防聚焦缩放）；`padding-bottom: calc(10px + env(safe-area-inset-bottom))` |
| 流式状态 | Header 实时显示：排队中（⏳ #N）/ 流式传输中（● streaming） |

### 4.2 视觉规范（已落地，保持）

| 元素 | 规范 |
|---|---|
| 气泡 `.bubble` | 圆角 16px；用户右下角、助手左下角保留 6px 小尾巴；用户气泡 max-width 88% |
| 字号 | 气泡 15px、正文 15px、头像 30px（≤768px） |
| 输入区 `.chat-input` | 顶部品牌柔和阴影 `0 -2px 14px rgba(109,94,241,.08)` + 细分隔线（深色改黑阴影） |
| 发送按钮 `.send-btn` | `--m-brand-grad` 渐变胶囊（999px）+ 紫色投影；禁用态 opacity 0.45 |
| 会话头部 `.chat-header` | 毛玻璃 `backdrop-filter: blur(10px)`，白 `rgba(255,255,255,.72)` / 深色 `rgba(11,18,34,.78)` 双模式 |
| 设计令牌 | `--m-brand` #6d5ef1、`--m-brand-grad`（#6d5ef1→#8b5cf6→#38bdf8）、`--m-radius` 16px |

### 4.3 简洁性检查清单（ChatGPT 范式）

1. **单栏消息流**：无多余侧栏/双栏元素；工具面板（`.ws-panel`）移动端全宽抽屉化。
2. **输入区固定底部**：视觉上始终可见，safe-area 正确。
3. **头部极简**：标题 + 流式状态 badge（排队中 / 运行中），模型选择等控件收纳进左侧抽屉「设置」分组（见第五章），不堆叠。
4. **留白**：消息列表 padding 14px 16px，气泡间间距适中，避免贴边。
5. **克制动效**：仅高光时刻（新消息出现、按钮浮现）使用过渡，不做常驻动画。

---

## 五、原则四：功能收纳 — 聊天框只留对话，设置入左侧抽屉

> 现状：移动端聊天头部占用空间的控件（模型选择 `.model-selector` / 向量库检索 `.toggle` / 天气预警 `.weather-alert-container` / 工作目录 `.ws-manager` / 清空会话 `.chat-footer .btn-danger`）已全部收纳（`MobileShell.vue`），聊天框头部仅保留标题 + 流式状态 badge（排队中 / 运行中），视觉对齐 ChatGPT。移动端隐藏规则在 `mobile.css` 尾部（`@@CHAT_PANEL_CSS@@` 标记，`@media (max-width:768px)` 下 `display:none !important`），升级入口为 `scripts/upgrade_chat_panel.py`（独立于 `upgrade_mobile_ui.py` / `upgrade_chat_scroll.py`）。
>
> **实现机制（2026-08 实测校正）**：实际落地为「抽屉「设置」入口 → 弹出设置表单弹层（`van-popup` bottom sheet）」而非文档早期描述的「抽屉内联设置分组」——聊天框更干净、设置不挤占抽屉导航。注入标记：抽屉入口 `@@CHAT_SETTINGS_ENTRY@@`、表单弹层 `@@CHAT_SETTINGS_FORM_TEMPLATE@@` / `@@CHAT_SETTINGS_FORM_SCRIPT@@` / `@@CHAT_SETTINGS_FORM_STYLE@@`；`upgrade_chat_panel.py` 的幂等标记为 `@@CHAT_PANEL_SCRIPT@@` / `@@CHAT_PANEL_TEMPLATE@@` / `@@CHAT_PANEL_STYLE@@` / `@@CHAT_PANEL_CSS@@`（模板标记 `@@CHAT_PANEL_TEMPLATE@@` 已作为幂等注释置于表单弹层标记旁）。重跑脚本不会重复注入。

### 5.1 收纳映射（源码事实）

| 原位置 | 原控件 | 收纳去向 | 状态共享 |
|---|---|---|---|
| `.chat-header` | `.model-selector` 模型选择 | 抽屉「设置」分组模型胶囊 | `agent.selectedModel`（Pinia，`multiAgent.ts` L46 已放宽为 `ref<string>`） |
| `.chat-header` | `.toggle` 向量库检索 | 抽屉「设置」分组开关 | `agent.useVectorDb`（Pinia ref） |
| `.chat-header` | `.weather-alert-container` 天气预警 | 移动端纯隐藏（组件内 ref `isWeatherEnabled` 无法跨组件共享；插件页可管理） | — |
| `.chat-header` | `.ws-manager` 工作目录 | 抽屉「设置」分组只读摘要 | `perm.workspaces` / `perm.loadWorkspaces()`（permission store） |
| `.chat-footer` | `.btn-danger` 清空会话 | 抽屉「设置」分组清空按钮（二次确认） | `agent.deleteConversation()`（action） |

### 5.2 交互规范

| 项 | 设计要点 |
|---|---|
| **入口** | 顶部 NavBar 汉堡 → 左侧抽屉 → 「设置」分组（不新增入口，不侵入聊天框） |
| **模型选择** | 胶囊式选项（复用 `SUPPORTED_MODELS`，`../config/models` 同源），选中即写 `agent.selectedModel`，即时生效 |
| **向量库开关** | Vant Switch 视觉，切换即写 `agent.useVectorDb`，即时生效 |
| **工作目录** | 只读摘要（前 2 个 + 计数省略），点击可刷新 `perm.loadWorkspaces()`；本轮不做增删 UI（缩 scope） |
| **清空会话** | 红色按钮 + 二次确认（3 秒未确认自动复位），确认后调 `agent.deleteConversation()` |
| **视觉** | 复用现有 `.drawer-item` / `.drawer-group-label` scoped 样式，分组标题「设置」与「功能导航」同级 |

### 5.3 红线（ChatGPT 范式）

1. **聊天框内零菜单**：不新增任何抽屉/菜单入口，头部仅标题 + 状态 badge。
2. **状态共享走 Pinia**：设置项一律读写现有 store，不复制组件内局部状态，零后端改动。
3. **隐藏即合规**：无法跨组件共享的状态（如天气预警）移动端直接隐藏，不强行造开关。

---

## 六、知识库对齐矩阵与落地行动项

### 6.1 对齐矩阵

| 交互模式 | 知识库出处 | 状态 |
|---|---|---|
| 智能自动滚动（靠近底部才跟随） | README L37 / MultiAgentView.vue | ✅ 已落地 |
| 用户上翻暂停跟随 | MultiAgentView.vue `onScroll` | ✅ 已落地 |
| 气泡/输入区/发送按钮视觉规范 | mobile.css L431-478 | ✅ 已落地 |
| 头部毛玻璃 + 工具条压缩 | mobile.css / 检索报告 3.1 | ✅ 已落地 |
| **回到底部按钮** | `MultiAgentView.vue`（`showScrollBtn` / `scrollToBottom` / `isTrusted`）+ `mobile.css` L481-506 + `scripts/upgrade_chat_scroll.py` | ✅ 已落地 |
| **功能收纳（设置入抽屉，聊天框零菜单）** | `MobileShell.vue`（`@@CHAT_PANEL_*@@` 三处注入）+ `mobile.css` 尾部隐藏规则（`@@CHAT_PANEL_CSS@@`）+ `scripts/upgrade_chat_panel.py` | ✅ 已落地 |
| 重试幂等 / 断连落库（弱网可靠性） | 优化行动计划 F1（P1） | ✅ 已落地（B4 client_msg_id 幂等 + B10 统一 session.db 历史管线 + B11 断连兜底落库） |
| `part.delta` 真增量渲染（流式流畅度） | 优化行动计划 F2（P2） | ✅ 已落地（`text_delta` 经 `TaggedEventQueue` 直通 SSE 打上 agent_id，前端实时增量追加主回答） |
| 多 Agent 子会话 parts 落库（历史完整） | 优化行动计划 F3（P2） | ✅ 已落地（`_persist_multi_agent_parts` 主会话 assistant parts + agent/step/tool part 落库） |

### 6.2 落地建议（按优先级）

| 优先级 | 行动项 | 说明 |
|---|---|---|
| ✅ 已完成 | 「回到底部」悬浮按钮 | 已按第三节规范落地（经 `scripts/upgrade_chat_scroll.py` 执行，样式在 `mobile.css` L481-506）；规范固化见 6.3 |
| ✅ 已完成 | 功能收纳（原则四） | 设置入左侧抽屉「设置」分组（`MobileShell.vue` 经 `scripts/upgrade_chat_panel.py` 注入），聊天框零菜单；样式在 `mobile.css` 尾部 `@@CHAT_PANEL_CSS@@`；规范固化见第五节 |
| P1 | F1 聊天链路补全 | B4 重试幂等、B10 统一历史管线、B11 断连落库；成功指标：断连重连不丢消息 | ✅ 已完成（后端 B4/B10/B11 已落地，见 6.1） |
| P2 | F2/F3 流式增量 | `part.delta` 真增量渲染 + 子会话 parts 落库，是"跟随滚动流畅度"的技术基础 | ✅ 已完成（F2 `text_delta` 直通 SSE、F3 parts 落库，见 6.1） |

### 6.3 修改入口约束（知识库强制规范）

- 聊天页样式：**一律追加 mobile.css**（`!important` 声明级覆盖），不手工改 `MultiAgentView.vue` scoped 样式。
- 聊天页逻辑/模板：统一走 `scripts/upgrade_mobile_ui.py`（脚本执行前自动生成 `.bak` 备份，构建失败可回滚）。
- 回到底部按钮（滚动交互）：**独立脚本 `scripts/upgrade_chat_scroll.py`**（非 `upgrade_mobile_ui.py`；已执行，样式已注入 `mobile.css` L481-506，含 `@@CHAT_SCROLL_INJECTED@@` 标记），该按钮相关后续改动一律走此脚本。
- 功能收纳 / 设置入抽屉（原则四）：**独立脚本 `scripts/upgrade_chat_panel.py`**（非 `upgrade_mobile_ui.py` / `upgrade_chat_scroll.py`；已执行，含 `@@CHAT_PANEL_SCRIPT@@` / `@@CHAT_PANEL_TEMPLATE@@` / `@@CHAT_PANEL_STYLE@@` / `@@CHAT_PANEL_CSS@@` 标记），相关后续改动一律走此脚本。
- 新增规范须同步三处事实源：`AGENTS.md`（架构事实）+ `mobile.css`（落地代码）+ `docs/优化行动计划.md`（待办清单）。

---

## 七、结论

1. **原则一（用户滚动优先）已达标**：`isNearBottom` 100px 阈值 + 平滑跟随 + 用户介入即让位，与 ChatGPT 范式一致，建议固化进 AGENTS.md 作为架构事实。
2. **原则二（回到底部按钮）已落地**：按本文第三节规范实现（`showScrollBtn` + `isTrusted` 用户手势优先，经 `scripts/upgrade_chat_scroll.py` 注入 `mobile.css` L481-506），建议将交互要点固化进 AGENTS.md 作为架构事实。
3. **原则三（简洁布局）已达标**：知识库规范完备，仅需在新增按钮时保持"克制、不遮挡、双模式"三条红线。
4. **原则四（功能收纳）已落地**：模型选择 / 向量库开关 / 工作目录摘要 / 清空会话已迁入左侧抽屉「设置」分组（`MobileShell.vue`，经 `scripts/upgrade_chat_panel.py` 注入）；天气预警移动端纯隐藏；聊天框头部仅标题 + 状态 badge，视觉对齐 ChatGPT。
