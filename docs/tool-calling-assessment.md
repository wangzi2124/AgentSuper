# 工具调用系统 · 评估报告

日期：2026-10-09 ｜ 数据来源：真实运行（`deepseek/deepseek-v4-flash`，会话 `ses_2095bczucxt8132db`）+ 离线 schema 计量（`estimate_tools`）。

## 1. 实测基线（一次真实「建 hello.py + README.md」任务）

| 指标 | 值 |
|---|---|
| 轮次 / 工具调用 | 3 轮 / 4 次 |
| 末轮 completion | 40 tok（真实总结，非空） |
| prompt_tokens（末轮） | 9056 |
| cache_hit / cache_miss | 8832 / 224 → **97.5% 命中** |
| `graph.round_ready`: message_tokens | 2703 |
| `graph.round_ready`: **schema_tokens** | **7581** |
| `graph.pre_compact`: threshold | 58982 |
| usable_context（DeepSeek） | 98304 |

结论：**schema 是本轮 prompt 的主体（7581 vs 2703，占 74%）**，命中缓存后按 0.1x 计费，但**仍全额占用上下文窗口**（直接影响压缩阈值与可用历史）。

## 2. Schema 成本拆解（常驻 vs 惰性）

| 工具组 | 工具数 | tokens | 是否常驻 | 备注 |
|---|---:|---:|---|---|
| 技能 `load_skill_*` | 45 | **4802** | ✅ 常驻 | 占常驻 schema **63%** |
| 文件系统 `tool_*` | 11 | 1618 | ✅ 常驻 | 描述偏长 |
| 插件 `plugin_*` | 17 | 3814 | ❌ 意图门控 | 仅命中关键词时挂载 |
| `tool_web_search` | 1 | 90 | ✅ 常驻 | |
| `tool_task` | 1 | 459 | ✅ 常驻 | 枚举裁剪后 |
| `tool_memory_*` | 3 | ~300 | ✅ 常驻 | |
| **常驻合计** | ~61 | **≈7300–7600** | | 占 usable **≈7.7%** |

现状**已实现的优化**（对照你贴的原则）：
- 插件=惰性（意图门控）；`_remember_conversation_tools` 会话内**只增不减、尾部追加** → 前缀缓存稳定（命中 97.5%，正是「动态追加不破坏前缀缓存」）。
- 工具结果截断+落盘续读（`bound_tool_output`/`prune_tool_outputs`）、上下文压缩（compaction+epoch+tail replay）、预算/熔断（`MAX_STEPS`/`MAX_TOOL_ROUNDS`/`DOOM_LOOP_*`）均已在位。

## 3. 缺口与优化空间（按性价比排序）

| 优先级 | 优化点 | 预期收益 | 风险 |
|---|---|---|---|
| P0 | **技能懒加载**：45 个 `load_skill_*` 改「1 个工具搜索元工具 + 命中后尾部注入」 | 每请求省 ~4800 tok（常驻 schema ↓63%，usable 占比 7.7%→~3.5%） | 中（需保证前缀缓存语义不破） |
| P1 | **Schema/描述压缩**：文件系统 11 工具的冗长描述 + 参数示例精简 | 省 ~500–900 tok | 低 |
| P2 | **CodeAct 沙盒**：脚本一次跑多工具 | 减少轮次（本次 3 轮→1~2 轮），降延迟与总 token | 高（需沙盒 + 权限模型） |

## 4. 设计建议（结合 opencode）

- **惰性 schema 加载**：opencode 用「工具按需挂载 + 尾部追加」；本仓库插件已如此，技能是最后一块常驻大户，建议同法处理（保留 `disable-model-invocation` 语义）。
- **前缀缓存优先**：任何按需注入都必须是**尾部追加**，绝不动 system/tools 前缀，否则 97.5% 的命中率会掉。
- **CodeAct 谨慎**：需要进程级沙盒与写权限审计，落在本仓库的权限模型（`PermissionManager`）之上，先做只读/受控执行再放开写。

## 5. 附：验证脚本

- `%TEMP%\opencode\assess.py`（schema 分组成本）
- `%TEMP%\opencode\assess2.py`（静态工具成本）
- `%TEMP%\opencode\verify_cfg.py`（DeepSeek 思考/输出上限/上下文预算）
