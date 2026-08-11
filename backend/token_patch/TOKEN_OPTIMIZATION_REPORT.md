# Token 优化体系现状与建议（v1–v6 审计 + 暴增根因分析 + v7 日志埋点方案）

日期: 2026-08-11（补齐 v5/v6 缺口后复核）
前置阅读: `README.md` / `PATCH2_CHANGELOG.md` / `PATCH6_CHANGELOG.md` / `app/context/budget.py` / `app/config.py` / `app/agent/graph.py` / `app/monitor.py` / `data/monitor_stats.json`

---

## 一、核心问题：为什么 token 从 2W 暴增到 40W、再暴增到 60W？

### 结论先行

**「40W / 60W」不是单次 LLM 调用超限，而是「单次请求内多次 LLM 调用的累计」；上限机制一直在工作，但每次请求最多可以发起 16 轮工具循环，每轮都是一次完整的 LLM 调用、把全部上下文重发一遍。**

### 数据对账（monitor_stats.json，100 次模型调用）

| 指标 | 值 | 说明 |
|---|---|---|
| model_calls_total | 100 | 全部 `deepseek/deepseek-v4-flash` |
| total_prompt_tokens | **1,849,731** | 累计 ≈ 185 万 |
| total_completion_tokens | 46,065 | 均值 ≈ 461/次 |
| prompt 均值 | ≈ 18.5K / 次 | 距 32K 上限尚有 40% 余量 → **单次调用上限生效** |
| tool_rounds_total / tool_calls_total | 68 / 112 | ≈1.65 次调用/轮 |
| 天气告警相关请求 | `weather-alert/call/*` ×20、`status` ×20、`workspaces` ×20 | 前端轮询密集，属工具循环伴生流量 |

### 数学解释（为何正好是 40W ≈ 60W）

- 单次请求上限：`max_tool_rounds = 16`（config.py），每轮上下文接近 `usable_context_tokens() = 23,808`（32K − 8K 输出预留）。
- **16 轮 × 23.8K ≈ 38.1 万 ≈ 40W** —— 与观测到的「暴增 40W」完全吻合。
- **60W ≈ 40W（第一次请求）+ 约 20W（第二次追问）**：第二次请求时，历史窗口（32K）里装着第一次请求的完整工具轮记录，随本轮循环再次累加 → 两请求合计 ≈ 60W。
- 触发场景是**工具密集型对话**：天气/台风查询（20 次 `tool_get_typhoon_info`）、子 Agent（supervisor 分解）等，轮次打满 16 轮，每轮重发 system(2-4K) + schema(2-4K) + 历史(增长至 19K+)。

### 逐轮为什么逼近 24K

1. system 稳定模板：2–4K（v2 后已稳定、可命中 DeepSeek 前缀缓存 0.1×）；
2. 工具 schema（按需挂载后）：2–4K，多轮工具循环中随 used_tools 累积缓慢增长；
3. 历史消息：每轮新增 assistant(tool_calls) + tool(结果 bound 1500 字符/条)；`prune_tool_outputs` 只回溯清理旧轮（保留 tail 2 轮），接近 19K 压缩阈值前历史仍持续增长；
4. 压缩（19,046 阈值）触发后：LLM 摘要本身是一次额外调用（计入 model_calls），且 tail 轮次保留，压缩后上下文仍在 10K+ 水平。

### 关于「就一个 session，难道还有历史数据吗」

**有，而且每次请求都会重放历史。** 会话历史持久化在 `session_messages / message_parts / context_epoch`（app/session/db.py）。每次「问助手」：
`chat.py _session_history_for`（全量拉取 → 32K 窗口截断）→ summarizer 压缩（可选）→ 注入 Agent；
Agent 工具循环中每一轮 `_truncate_messages(usable=23.8K)` 硬截断后发送。
**同一个 session 的多轮请求之间，历史是共享且累积的** —— 单 session 完全可以制造出 40W/60W 级别的累计消耗，不需要多个会话。

---

## 二、v1–v6 演进清单（现状核查）

| 波次 | 核心动作 | 落地文件 | 效果 |
|---|---|---|---|
| v1 | max_steps 40→24、max_tool_rounds 24→16、上下文 64K→48K、工具输出保护 40K/20K→24K/12K；子 Agent 轮数 8→5、结果 4000→1500 字符、`_trim_messages` 按轮裁剪；supervisor 分解重试 2→1、短问题直路由；chat 历史 80K→48K | config / sub_tools / supervisor / chat | 单请求理论上限 224 万 → 64 万；multi-agent 输入降 50%+ |
| v2 | system 恒为稳定模板（RAG 上下文移入 user 前缀）→ 命中 DeepSeek 前缀缓存(0.1×)；检索 k=5→3；子 Agent 16K/4 轮 | graph / sub_tools | 再降 20%~40%，总体较原始 60%~75% |
| v3 | chat 历史窗口 48K→32K | chat | 每次请求输入更小 |
| v4 | 压缩优先于硬截断：ContextCompactor（阈值 80% usable = 19,046，tail_turns=2，摘要模板锚定） | app/context/compaction + graph | 先总结后丢弃，信息不丢 |
| v5 | 工具 schema 按需挂载：核心常驻 + 意图关键词命中(15 类) + 已使用保留；`_bound_plugin_result` 天气/台风结果 1500 字符截断；每轮重挂载 | graph / config | schema 固定开销 8-12K → 2-4K |
| v6 | 自定义工具接口：脚本型写 plugins/custom_*.py + 固定型写 data/pinned_tools.json；`_pinned_tool_names` 循环外只读一次；`/api/custom-tools/*` | skills/custom_tools / api/custom_tools / runtime / main / graph | 消除「模型看不到工具」风险，pin 工具 schema 常驻 |

### 落地验证（本轮补齐的缺口）

- `budget.py` 实际数值：usable = 32,000 − 8,192 = **23,808**；compaction 阈值 = 0.8 × 23,808 = **19,046**（配置为 0 时自动）✓
- `config.py`：max_context_tokens=32_000、context_reserve_tokens=8_192、compaction_threshold_tokens=0、max_steps=24、max_tool_rounds=16、doom_loop_threshold=3、doom_loop_max_strikes=2 ✓
- `graph.py` 与 `bak_token_patch6` 对比：v6 符号（CustomToolStore / custom_tools / _pinned_tool_names）只存在于当前版本 → v6 已落地 ✓
- `graph.py` 与 `bak_token_patch4` 对比：v5 符号（_INTENT_RULES / used_tools / _bound_plugin_result）只存在于当前版本 → v5 已落地 ✓
- 链路完整性：入口历史窗口 → summarizer 可选 → entry 压缩→截断(23.8K) → while 循环内 prune→压缩→截断→按需重挂载 → LLM 调用 → `record_model_call` 记账 ✓

---

## 三、全流程日志埋点方案（v7）

回答「从问助手开始到 LLM 返回结束全流程记录下来」的需求。交付：
`token_patch/add_token_trace_logging.py`（埋点补丁，本机运行、可回滚） + `token_patch/analyze_token_trace.py`（分析脚本）。

### 埋点事件清单（JSON Lines → `logs/token_trace_YYYYMMDD.jsonl`）

| 事件 | 触发点（graph.py / monitor.py 插入） | payload |
|---|---|---|
| `graph.entry_ready` | 入口压缩→截断后、首轮 LLM 前 | batch / msg_count / tokens |
| `graph.round_start` | 工具循环每轮 `prune_tool_outputs` 前 | batch / msg_count / tokens |
| `graph.pre_compact` | 每轮 prune 后、压缩判断前 | batch / msg_count / tokens / threshold |
| `graph.round_ready` | 每轮截断后、发送 LLM 前 | batch / msg_count / tokens |
| `llm.usage` | 每次 LLM 返回（成功/异常/汇总 4 处） | where / model / pt / ct / duration_ms |
| `monitor.record_model_call` | monitor.py 记账总入口（兜底，防漏） | model / prompt_tokens / completion_tokens / duration_ms / tool_rounds / tool_calls |
| `graph.finish` | 请求结束 | rounds / tool_calls / duration_ms |

`batch` 字段：每次 `entry_ready` 自动 +1，把同一请求内的所有事件归组 → 分析时直接看「单请求累计 prompt」。

### 使用步骤

```bash
cd E:\AgentSuper\backend
python token_patch/add_token_trace_logging.py            # 应用（自动备份 *.bak_token_trace）
python token_patch/add_token_trace_logging.py --verify   # 校验
# 重启后端 → 复现一次长工具对话（问助手）→
python token_patch/analyze_token_trace.py                # 输出每请求/每轮 token 画像
python token_patch/add_token_trace_logging.py --rollback # 回滚
```

---

## 四、下一波优化建议（P7 候选，按性价比排序）

1. **chat.py 历史窗口对齐 budget**：`MAX_HISTORY_TOKENS=32K` 仍大于 usable 23.8K，graph 内会被二次截断 → 直接对齐 24K，减少每次请求的无效拉取与截断开销。
2. **工具结果 bound 再收紧**：天气/台风高频场景，1500 字符 → 800 字符（`_bound_plugin_result`），多轮重发收益显著。
3. **压缩阈值下调 19K→16K**：更早压缩、压低每轮峰值（牺牲少量压缩调用成本）。
4. **schema 顺序稳定化**：按需挂载时保持工具名排序稳定，避免每轮 schema 文本抖动影响前缀缓存命中。
5. **前端轮询降频**：weather-alert/status、workspaces 各 20 次/观察期 → 加节流（如 5s），降低并发与无效请求。
6. **trace 报告驱动**：v7 埋点跑一轮真实长对话后，按 `analyze_token_trace.py` 输出精准定位「哪一轮、哪个环节」贡献最大，再决定是否继续收紧。
7. **子 Agent 历史裁剪**：supervisor 分解出的子 Agent（sub_tools 12K/4 轮）历史目前独立循环，可进一步共享主 Agent 的压缩摘要。

---

## 五、附：审计中确认无问题的点

- Skill / Plugin 加载仅在启动与 toggle 时触发（无每次请求重复扫描）——PATCH2 已验证；
- `sanitize_tool_messages` 保证 tool 消息与 tool_calls 配对，截断不产生孤儿消息（API 不会 400）；
- `ToolResultDedup` 跨轮哈希去重，同一工具结果不重复注入；
- doom-loop 检测 + MAX_STEPS 收尾 + finish_reason 归一化（length/content-filter 显式告警）均在位。


---

# P8：估算低估 / 压缩过晚 / 收尾缺闭环 —— 修复记录

> 依据 `analyze_token_trace.py` 实测（batch 1，9 次调用，累计 pt 160,950）：
> - 单次最大 pt 25,779 **超出 usable 23,808**；
> - round 8 估算 20,851 vs 实际 23,599（**低估 +13.2%**）；
> - 压缩 8 次判断仅触发 1 次（round 8: 19,601 > 19,046），触发后下一轮仍超限；
> - round 9 无 round_start 事件 = 强制收尾路径，裸发 25,779。

## 三个根因

| # | 根因 | 证据 | 后果 |
|---|------|------|------|
| A | `estimate_tokens` 用 cl100k_base，对 DeepSeek tokenizer 系统性低估 ~13% | round8 估算 20,851 vs 实际 23,599 | 截断/压缩判断"以为没超"→ 该截断的不截断，实际 25,779 超 usable |
| B | 压缩阈值 0.8×usable=19,046 太晚 | round 8 才首次触发 | 压缩（LLM 摘要）后下一轮仍超限，兜底失效 |
| C | 强制收尾路径（max_tool_rounds 兜底）仅有截断、无 prune/压缩 | round 9 无 round_start 事件 | 收尾调用成为单请求最大单次 pt |

## 修复内容（`token_patch/fix_token_budget_p8.py`，4 文件 5 处）

1. **config.py**：新增 `token_estimate_correction = 1.13`、`compaction_threshold_ratio = 0.65`
2. **token_counter.py**：`estimate_tokens` 结果 × 校正系数（一处修改，`truncate_messages` / `compactor.should_compact` / `_select` / `_ensure_within_budget` 全链路自动修正）
3. **budget.py**：`compaction_threshold_tokens()` 默认比例 0.8 → `usable × compaction_threshold_ratio`（0.65 ≈ 15,475，提前 2-3 轮介入）
4. **graph.py**：强制收尾路径补齐 `prune_tool_outputs → should_compact → compact → truncate` 闭环（与主循环同款），新增 `graph.final_round_start/final_round_ready` trace 事件

## 预期效果（修复后复测验证项）

- 单次 pt 上限：估算值≈实际值，`_truncate_messages` 在真正超限前触发 → **任何轮次 pt ≤ 23,808 × ~1.0**
- 压缩轮次：round 5-6 首次触发（阈值 ~15,475），曲线提前下压
- 收尾轮：与主循环同款闭环，不再出现"最后轮裸发最大 pt"
- 累计曲线：每轮发送前 pt 被压在预算内 → 单请求累计 ≈ 轮数 × ≤24K 的可控上限

## 使用步骤

```bash
cd E:\AgentSuper\backend
python token_patch/fix_token_budget_p8.py            # 应用（自动备份 *.bak_p8）
python token_patch/fix_token_budget_p8.py --verify   # 校验
# 重启后端 → 复现长工具对话 →
python token_patch/analyze_token_trace.py            # 复测：pt_max 应 ≤ usable 且压缩提前
python token_patch/fix_token_budget_p8.py --rollback # 如需回滚
```

## 待确认（下次复测观察）

- 压缩提前后，LLM 摘要调用次数增加（每次 ~2-5K pt 成本）与"每轮发送更小"之间的权衡
- 若仍需更紧封顶：`token_estimate_correction` 可调至 1.2，或 `context_reserve_tokens` 提高
- 若压缩过于频繁（每轮都触发）：`compaction_threshold_ratio` 回调至 0.7
