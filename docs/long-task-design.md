# Long Task Design — 长任务不截断设计（对齐 opencode）

## 1. 背景与问题

AgentSuper 执行长任务（生成长文、批量代码、长文档）时，经常触发 `finish_reason == "length"`：
模型回答达到输出 token 上限被截断，`_generate` 在答案尾部追加
「⚠️ 输出因达到 token 上限被截断，内容可能不完整」，任务"没完成"。

根因不是单个 bug，而是三个叠加因素：

1. **输出上限太低**：`_llm_call` 硬编码 `max_tokens=4096`（后来提到 8192），远低于模型原生上限。
2. **系统提示缺少"长内容写文件"的硬性约束**：模型习惯一次性把长文输出在回复里，而不是写入文件。
3. **截断后不续写**：`finish_reason == "length"` 直接退出循环（对齐 opencode），长输出丢尾巴。

## 2. opencode 长任务机制精读

参考：`E:\project\opencode-dev\packages\opencode\src\`

### 2.1 多步循环拆解任务（`session/prompt.ts` runLoop，1090-1336）

整个任务在 runLoop 中反复迭代。每次循环：
- 读最新消息，判定退出（`prompt.ts:1111-1130`）或继续；
- 每轮一次 LLM 调用，产出**短文本 + 工具调用**；
- 工具结果回填后进入下一轮。

任务 = 大量短步，不是一次生成完。单轮输出量小 → 天然不易触发输出上限。

### 2.2 长内容强制写文件

系统提示引导助手：长篇内容用 `write`/`edit` 工具写进文件，回复只留摘要。
所以单轮回复几乎总是几百 token，而不是几千。

### 2.3 高输出上限（`provider/transform.ts:1394-1395`）

```
OUTPUT_TOKEN_MAX = 32_000
maxOutputTokens(model, outputTokenMax = OUTPUT_TOKEN_MAX) =
  Math.min(model.limit.output, outputTokenMax) || outputTokenMax
```

- 默认 `min(模型原生上限, 32000)`，可用 `--output-token-max` 覆盖。
- 模型原生上限通常 8K-32K，比 AgentSuper 硬编码的 4K/8K 高一个量级。

### 2.4 子任务委派（task 工具 / subtask，`prompt.ts:1142-1147, 290-364`）

超大任务由父代理拆给 subagent（各自独立会话）并行/递归执行，
父会话等子会话结果后汇总。AgentSuper 已有 supervisor，本文不做。

### 2.5 输入溢出自动压缩（`prompt.ts:1161-1168, 1320-1328`）

上下文接近上限时自动 compact（总结旧消息）后 `continue`，不失败。
AgentSuper 已有 `SummarizationMiddleware` / `compaction`，本文不做。

### 2.6 MAX_STEPS 收尾（`prompt.ts:1178-1281`）

`agent.steps` 到最后一轮注入 MAX_STEPS_PROMPT + 禁用工具。
AgentSuper 已对齐（`config.py: max_steps`），本文不做。

### 2.7 截断（length）时：退出循环，不自动续写

`prompt.ts:1113`：`!["tool-calls"].includes(finish)` 即 break。
opencode **没有**"截断续写"机制——它靠 2.1-2.3 防患于未然，
而不是截断后补救。AgentSuper 的 `finish_reason == "length"` 退出语义已与此一致。

## 3. AgentSuper 现状与差距

| 维度 | opencode | AgentSuper 现状 | 差距 |
|------|----------|------------------|------|
| 输出上限 | `min(模型上限, 32000)`，可配置 | `_llm_call` 硬编码 8192 | **不可配置且偏低** |
| 长文写文件 | 系统提示强约束 | 只有"Writing large files"分块提示，无"回复只留摘要"约束 | **缺硬性规则** |
| 截断后行为 | 退出循环 + 用户可见 | 退出循环 + 尾部提示 | 一致（已对齐） |
| 多步循环 | 有 | 有（工具循环） | 一致 |
| 子任务委派 | 有 | 有（supervisor） | 一致 |
| 输入压缩 | 有 | 有 | 一致 |

## 4. 目标设计

对齐 opencode 的 2.2 + 2.3，其余机制已具备。

### P1：输出上限配置化

- `config.py` 新增 `llm_max_tokens: int = 16_384`，读取 `.env` 的 `LLM_MAX_TOKENS`。
- `graph.py _llm_call` 用 `settings.llm_max_tokens` 替换硬编码 `max_tokens=8192`。
- 备注：对齐 opencode `maxOutputTokens` 的"默认给足、可覆盖"设计。

### P2：系统提示增加"长内容写文件"硬性约束

在 `tools.py build_system_prompt_no_kb` 新增一条 `IMPORTANT` 规则（对齐 opencode 2.2）：

- 内容超过约 500 字（约 1000 token）时，**必须**把完整内容写入文件：
  - 文本/代码 → `tool_write_file`（大文件配合 `tool_append_file` 分块）；
  - 结构化文档 → docx/pdf/xlsx/pptx 生成插件。
- 回复中只输出：文件路径 + 要点摘要 + 结构概述，**不**粘贴全文。
- 用户明确要求"直接输出全文"时才允许在回复中粘贴长文。

同时给 KB 路径 `_system_prompt_with_kb` 追加同样的约束段落（复用同一段文案）。

### P3：截断续写（明确不做）

不实现"length 自动续写"。原因：
- opencode 无此机制，靠写文件 + 高上限解决；
- 自动续写会与"长文必须写文件"的约束互相干扰（模型会倾向直接长输出然后被续写）。

保留现有行为：`finish_reason == "length"` 退出循环 + 尾部提示。
若用户遇到截断，提示中已明确"内容不完整"，用户可要求拆分或改让模型写文件。

## 5. 实施计划

1. P1：`config.py` 加 `llm_max_tokens`；`graph.py` `_llm_call` 引用配置。
2. P2：`tools.py` 加"长内容写文件"IMPORTANT 段落；`graph.py` `_system_prompt_with_kb` 复用该段落。
3. 更新 `AGENTS.md` 记录配置与约束。
4. 验证：`py_compile` + `import main` + 配置值打印。

## 6. 验收标准

- [ ] `.env` 配 `LLM_MAX_TOKENS` 后 `_llm_call` 实际使用该值（默认 16384）。
- [ ] 系统提示包含"长内容写文件"规则（无 KB 与 KB 两条路径均含）。
- [ ] 长任务（>500 字）实测：模型主动 `tool_write_file` 写文件，回复只给摘要，不再触发 `length` 截断。
- [ ] 仍触发 `length` 时（用户强制全文输出），尾部提示照常显示，行为与 opencode 一致。
