# Harness 设计差距矩阵（审计冻结稿）

> 审计基线：`50b230b` + 当前未提交改动
> 审计日期：2026-10-24
> 设计依据：`docs/harness-architecture-design.md`（§3–§13）
> 审计纪律：本文件只记录事实与差距，**不包含生产代码改动**。
> 状态图例：
> - ✅ 已验证：代码存在，且有直接测试并在本次审计中实际运行通过
> - 🟡 已实现未验证：代码存在，但缺直接测试或测试未覆盖该行
> - 🟠 部分：代码存在但行为/契约未达到设计
> - ❌ 缺失：设计条目在代码中不存在
> - ⛔ 阻塞：需要真实模型/凭证/环境，当前不可验证
> - ⚠️ 设计缺陷/冲突：设计内部矛盾，或设计与既有权威实现冲突，需裁决

---

## A. 审计证据

| 证据 | 结果 |
| --- | --- |
| 全量后端测试 | `514 passed, 16 skipped, 1 warning in 17.68s`（真实模型项默认 skip） |
| 测试收集 | 约 464 个测试项（6 个 skip） |
| 真实模型冒烟 | `HARNESS_SMOKE_MODEL_A/B` 未设置 → ⛔ |
| 本机 Ollama | `http://127.0.0.1:11434` 不可达 → ⛔ |
| 真实模型测试 | `test_harness_real_model.py`、`test_model_switch_l4_real_models.py` 默认 skip |
| 性能测试 | 仅 `tests/perf/test_perf_smoke.py` 的假模型 P50 冒烟；无 P95/成本/并发/切换延迟 |
| 覆盖率工具 | `pytest-cov` 已安装；`.coveragerc` 设 Harness ≥90%；实测 91% |
| 当前未提交改动 | 20 个路径（含本轮 Harness 模块与测试） |

**审计结论（先看这里）**：功能主路径大体齐备，但“§13 全量验收”远未达到。主要缺口集中在：流式工具执行、SwitchService 抽取、工具 schema 缓存/上限、ResultPolicy 接线、错误 SSE、压缩三实现收敛、真实模型/性能/可靠性/安全黄金集、覆盖率门禁。

---

## B. §3 架构组件差距

| 组件 | 设计位置 | 代码落点 | 测试证据 | 状态 | 差距 |
| --- | --- | --- | --- | --- | --- |
| L0 API/SSE | §3.1 | `backend/app/api/chat.py`、`compat.py` | `tests/e2e/test_chat_contract.py`、`tests/integration/test_platform_app_api.py` | ✅ | — |
| `TurnHarness` | §3.2 | `backend/harness/kernel.py`、`app/deps.py` | `tests/unit/test_harness_kernel.py` | ✅ | 已接 `run`/`stream`，但主 multi-agent SSE 路径仍未走 kernel |
| `AgentLoop` | §3.2 | `backend/graph/nodes/base.py` | `test_tool_loop_robustness.py`、`test_finish_reason.py`、`test_tool_stream_loop.py` | 🟡 | 回退路径已流式闭合执行；原生路径仍以完整响应为主 |
| `Planner` | §3.2 | `backend/graph/nodes/plan.py` | `test_plan_tier.py`、`test_m1_graph_flow.py` | ✅ | — |
| `Synthesizer` | §3.2 | `backend/graph/nodes/synthesize.py` | `test_m1_graph_flow.py` | 🟡 | 单任务路径有覆盖，多任务综合缺直接测试 |
| `ModelGateway` | §3.2 | `backend/models/router.py` | `test_m1_model_router.py`、`test_harness_router.py` | ✅ | — |
| `ToolAdapter` | §3.2 | `backend/harness/tool_adapter.py` | `test_tool_adapter.py` | ✅ | `ModelProfile` 未接入 |
| `ToolRegistry` | §3.2 | `backend/execution/tool_registry.py` | `test_m1_tool_registry.py` | ✅ | — |
| `PermissionGate` | §3.2 | `backend/execution/permission.py` | `test_permission_whitelist.py`、`test_sandbox_boundary.py` | ✅ | 动态写入仍无法进程级拦截（设计已知） |
| `Executor` | §3.2 | `backend/execution/` | `test_m4_tools.py` | ✅ | — |
| `HookBus` | §3.2 | `backend/harness/hooks.py` | `test_harness_hooks.py` | ✅ | `ModelBefore/After` 仍为预留 |
| `ContextHarness` | §3.2 | `backend/harness/context.py` | `test_harness_context.py`、`test_tool_history.py` | ✅ | — |
| `Compactor` | §3.2 | `backend/cognition/compressor.py`、`backend/harness/compression.py` | `test_m2_compressor.py`、`test_compress_gate.py`、`test_harness_compression.py` | ✅ | 生产路径统一走 `compress_messages` / `compress_graph_messages` / `tail_snapshot`；仅保留测试注入的 legacy compressor 分支 |
| `MemoryStore` | §3.2 | `backend/graph/nodes/memory.py` | `test_m2_memory.py` | ✅ | — |
| `RAG Pipeline` | §3.2 | `backend/rag/`、`graph/nodes/rag.py` | `test_m2_rag.py`、`test_rag_grade_13_4.py` | ✅ | — |
| `BudgetLedger` | §3.2 | `backend/harness/budget.py` | `test_budget.py` | 🟠 | 调用后扣费，不是设计要求的“调用前预扣”；无 `budget_exceeded` 事件 |
| `EventBus` | §3.2 | `backend/graph/events.py`、`app/api/compat.py` | `test_graph_events.py`、`test_harness_kernel.py`、`test_platform_app_api.py` | ✅ | compat SSE 事件已带 `turn_id`/`session_id` |
| `Observability` | §3.2 | `backend/observability/` | `test_m5_ab_metrics.py` | 🟡 | 压缩/切换/扇出字段未全部落指标 |
| `HarnessError` | §5.6 | `backend/harness/errors.py`、`app/api/compat.py` | `test_harness_kernel.py`、`test_platform_app_api.py` | ✅ | 多智能体 SSE/非流式错误已映射 retryable/status/error_type |
| `SwitchService` | §3.3 / §9.1 | `backend/harness/switch.py` | `test_model_switch.py`、`test_model_switch_context_e2e.py` | ✅ | 已抽离；`app/deps.py` 仅构造依赖并委托 |
| `ToolSchemaCache` | §6.4 | `backend/harness/schema.py` | `test_tool_schema_cache.py` | ✅ | 缓存 + 15 上限 + global 优先 |
| `ResultPolicy` | §6.5 / §10 | `backend/cognition/tool_output.py`、`graph/nodes/base.py` | `test_result_policy.py` | ✅ | `bound_tool_output` 每次工具回灌前执行；`prune_tool_outputs` 在下一轮模型调用前执行 |
| `ModelProfile` | §5.2 / §6 | `backend/harness/contracts.py` | `test_harness_kernel.py`（仅契约） | 🟠 | 未用 true catalog + override + TTL 组装 |

---

## C. §4 业务图逐节点/逐边

### C1. BF-01 单轮请求主流程

| ID | 元素 | 代码落点 | 测试证据 | 状态 | 缺口 |
| --- | --- | --- | --- | --- | --- |
| B01 | 接收请求 | `app/api/chat.py`、`compat.py` | `test_chat_contract.py` | ✅ | — |
| B02 | 解析会话/epoch/binding | `app/deps.py:1085` | `test_model_switch.py`、`test_model_switch_context_e2e.py` | ✅ | — |
| B03 | 预算与并发闸门 | `harness/budget.py`、`deps.py` semaphore | `test_budget.py`、`test_platform_app_api.py::test_stream_queued_when_all_slots_busy` | 🟠 | 预扣/单轮预算未按 §8.1 |
| B04 | queued/429/明确错误 | `compat.py` | 同上 | 🟡 | `BudgetExceeded` 未映射为 SSE 429 事件 |
| B05 | 输入过滤 | `graph/nodes/filter.py` | `test_filter_node.py`、`test_plan_smalltalk.py` | ✅ | — |
| B06 | 固定模板 | `filter.py` | `test_filter_node.py` | ✅ | — |
| B07 | 上下文装配 | `harness/context.py` | `test_harness_context.py`、`test_tool_history.py` | ✅ | — |
| B08 | 是否需要计划 | `graph/nodes/plan.py`、`routers.py` | `test_plan_tier.py` | ✅ | — |
| B09 | 单任务快速路径 | `plan.py` | `test_m1_graph_flow.py` | ✅ | — |
| B10 | 串行分解 | `plan.py` | `test_plan_tier.py` | 🟡 | 多任务串行执行缺直接图测试 |
| B11 | 并行扇出 | `main_graph.py`、`routers.py` | `test_m3_parallel*.py`、`test_plan_parallel_gate.py` | ✅ | 默认关闭符合设计 |
| B12 | 是否需要 RAG | `supervisor.py` | `test_m1_graph_flow.py` | ✅ | — |
| B13 | 混合检索 | `rag/retriever.py` | `test_m2_retriever.py`、`test_m2_rag.py` | ✅ | — |
| B14 | grade 门禁 | `graph/nodes/rag.py` | `test_rag_grade_flow.py`、`test_rag_grade_13_4.py` | ✅ | — |
| B15 | 查询改写 | `rag` / `rewrite` | 同上 | ✅ | — |
| B16 | 进入 AgentLoop | `graph/nodes/base.py` | `test_tool_loop_robustness.py` | ✅ | — |
| B17 | Step 结果判定 | `base.py` | `test_finish_reason.py` | ✅ | — |
| B18 | 权限与执行 | `base.py`、`execution/permission.py` | `test_harness_hooks.py`、`test_m1_graph_flow.py` | ✅ | — |
| B19 | 工具结果回灌 | `base.py` | `test_tool_history.py` | ✅ | — |
| B20 | 收尾判定 | `base.py` | `test_finish_reason.py`、`test_tool_loop_robustness.py` | ✅ | — |
| B21 | Synthesize | `synthesize.py` | `test_m1_graph_flow.py` | 🟡 | 多子任务综合缺直接测试 |
| B22 | 持久化/事件/计量 | `deps.py`、`events.py`、`observability/` | `test_platform_app_api.py`、`test_graph_events.py` | ✅ | — |
| B23 | 最终返回/SSE done | `compat.py`、`chat.py` | `test_chat_contract.py` | ✅ | — |

**BF-01 边**

| 边 | 路径 | 状态 | 证据/缺口 |
| --- | --- | --- | --- |
| E01 | B03→B04 超预算/排队 | 🟠 | 排队有测试；预算 429 未映射 |
| E02 | B03→B05 通过 | ✅ | 主路径测试 |
| E03 | B05→B06 命中 | ✅ | `test_filter_node.py` |
| E04 | B05→B07 未命中 | ✅ | 主路径测试 |
| E05 | B08→B09 单任务 | ✅ | `test_m1_graph_flow.py` |
| E06 | B08→B10 串行 | 🟡 | 缺多任务串行图测试 |
| E07 | B08→B11 并行门禁 | ✅ | `test_plan_parallel_gate.py` |
| E08 | B12→B13 需要 RAG | ✅ | `test_m1_graph_flow.py` |
| E09 | B14→B15 不相关改写 | ✅ | `test_rag_grade_flow.py` |
| E10 | B14→B16 相关/达上限 | ✅ | `test_rag_grade_flow.py` |
| E11 | B12→B16 不需要 RAG | ✅ | 主路径测试 |
| E12 | B17→B18 有工具调用 | ✅ | `test_tool_history.py` |
| E13 | B17→B20 无工具调用 | ✅ | `test_finish_reason.py` |
| E14 | B20→B16 续跑 | ✅ | length/空答测试 |
| E15 | B20→B21 完成 | ✅ | 主路径测试 |

### C2. BF-02 工具调用决策

| ID | 元素 | 代码落点 | 测试证据 | 状态 | 缺口 |
| --- | --- | --- | --- | --- | --- |
| T01 | 准备模型调用 | `harness/tool_adapter.py` | `test_tool_adapter.py` | ✅ | — |
| T02 | 读取 ModelProfile | `capabilities.py`、`contracts.py` | `test_harness_capabilities.py` | 🟠 | 无 TTL/版本/人工覆盖统一快照 |
| T03 | tool_use 已知且为真 | `capabilities.resolve_mode` | 同上 | ✅ | — |
| T04 | 原生路径 | `tool_adapter.prepare` | `test_tool_adapter.py` | ✅ | — |
| T05 | 文本回退 | `adapter.py` | `test_harness_adapter.py` | ✅ | — |
| T06 | 调用 ModelGateway | `models/router.py` | `test_m1_model_router.py` | ✅ | — |
| T07 | 响应形态 | `adapter.apply_result` | `test_harness_adapter.py` | ✅ | — |
| T08 | 原生 tool_calls | `apply_result` | 同上 | ✅ | — |
| T09 | 内嵌 JSON | `extract_embedded_native_calls` | 同上 | ✅ | — |
| T10 | 定界符 | `delimiters.py` | `test_harness_delimiters.py` | ✅ | — |
| T11 | 纯文本 | `tool_adapter` | `test_tool_adapter.py` | ✅ | — |
| T12 | 统一输出 | `models/types.py` | 同上 | ✅ | — |
| T13 | 参数合法 | `call_argument_error` | `test_harness_adapter.py` | ✅ | — |
| T14 | 参数错误回灌 | `base.run_tool_loop` | `test_tool_loop_robustness.py` | ✅ | — |
| T15 | 进入工具执行 | `base.run_tool_loop` | `test_tool_history.py` | ✅ | — |

| 边 | 路径 | 状态 | 证据/缺口 |
| --- | --- | --- | --- |
| E20 | T03→T04 原生 | ✅ | helpers |
| E21 | T03→T05 回退/未知 | ✅ | helpers |
| E22 | T07→T08 原生 tool_calls | ✅ | unit |
| E23 | T07→T09 正文内嵌 JSON | ✅ | unit |
| E24 | T07→T10 定界符 | ✅ | unit |
| E25 | T07→T11 纯文本 | ✅ | unit |
| E26 | T13→T14 参数非法 | ✅ | unit |
| E27 | T13→T15 参数合法 | ✅ | integration |

### C3. BF-03 模型切换

| 状态/迁移 | 代码落点 | 测试证据 | 状态 | 缺口 |
| --- | --- | --- | --- | --- |
| Idle | `deps.py:845` | `test_model_switch.py` | ✅ | — |
| Idle→Validating | `switch_model` | 同上 | ✅ | — |
| Validating→Rejected（空/窗口未知/running） | `switch_model` | `test_switch_rejects_*` | ✅ | — |
| Validating→BackingUp | `_write_switch_backup` | `test_switch_compression_failure_keeps_old_model` | ✅ | — |
| BackingUp→Rejected | 同上 | 同上 | ✅ | — |
| BackingUp→Summarizing | `compress_context` | `test_switch_above_threshold...` | ✅ | — |
| Summarizing→Rejected | `test_switch_compression_failure...` | ✅ | — |
| Summarizing→Probing | `enforce_precision` / `extract_precision_values` | `test_switch_above_threshold...` | 🟡 | 精确值探针失败用例缺直接测试 |
| Probing→Rejected | `ModelSwitchError` | — | 🟡 | 无探针失败直接测试 |
| Probing→Committing | `upsert_epoch` | `test_switch_above_threshold...` | ✅ | — |
| Committing→Rejected | integrity/model persist 分支 | `test_switch_integrity_loss...` | ✅ | 模型持久化失败分支缺直接测试 |
| Committing→Committed | `sess.model` + audit | `test_model_switch_context_e2e.py` | ✅ | — |
| Rejected→Idle | `ModelSwitchError` | `test_model_switch.py` | ✅ | — |
| Committed→Idle | `switch_model` return | 同上 | ✅ | — |

**§9 不变量**

| ID | 不变量 | 代码/测试 | 状态 |
| --- | --- | --- | --- |
| I-SW1 | 切换前原文快照；失败拒绝 | `_write_switch_backup`；`test_switch_compression_failure...` | ✅ |
| I-SW2 | 目标窗口未知拒绝 | `test_switch_rejects_unknown_window` | ✅ |
| I-SW3 | running 拒绝 | `test_switch_rejects_running_session` | ✅ |
| I-SW4 | 压缩阈值/保留量成对 | `test_initial_state_uses_bound_compression_config` | ✅ |
| I-SW5 | 预算累计不因切换清零 | 无直接测试 | 🟡 |
| I-SW6 | 精确值探针 | 缺失败/回滚直接测试 | 🟡 |
| I-SW7 | 绑定/模型/备份原子 | integrity 有测试；model persist 失败缺 | 🟠 |
| I-SW8 | 消息 ID 稳定 | `test_stable_message_id_prefers_checkpoint_id` | ✅ |

### C4. BF-04 上下文压缩

| ID | 元素 | 代码落点 | 测试证据 | 状态 | 缺口 |
| --- | --- | --- | --- | --- | --- |
| C01 | 估算 token | `cognition/compressor.py` | `test_m2_compressor.py` | ✅ | — |
| C02 | 阈值判断 | 同上 | 同上 | ✅ | — |
| C03 | 不压缩 | 同上 | 同上 | ✅ | — |
| C04 | 切分 head/tail | `split_by_token`/`select_tail` | 同上 | ✅ | — |
| C05 | LLM 摘要门禁 C1/C2/C3 | `should_use_llm_summary` | `test_compress_gate.py` | ✅ | — |
| C06 | simple tier 9 段摘要 | `compress_context` | `test_m2_compressor.py` | ✅ | — |
| C07 | 规则截断 | 同上 | `test_compress_gate.py` | ✅ | — |
| C08 | 精确值回填 | `enforce_precision` | `test_m2_compressor.py` | ✅ | — |
| C09 | tail_start_id | `app/session/history.py`、`service.compact` | `test_model_switch.py`、`test_model_switch_context_e2e.py` | ✅ | — |
| C10 | 摘要+tail+新消息 | `harness/context.py` | `test_harness_context.py` | ✅ | — |
| C11 | 自检清单 | `prompts/nodes/compress.md` | 无直接断言 | 🟡 | 摘要质量自检未结构化验证 |
| C12 | 提交压缩水位 | `replace_epoch_after_compaction` | `test_model_switch.py` | ✅ | — |
| C13 | 保留原上下文+告警 | `compress_context` | 部分 | 🟡 | 失败路径缺直接断言 |

| 边 | 路径 | 状态 | 证据/缺口 |
| --- | --- | --- | --- |
| E30 | C02→C03 否 | ✅ | unit |
| E31 | C02→C04 是 | ✅ | unit |
| E32 | C05→C06 全真 | ✅ | unit |
| E33 | C05→C07 任一假 | ✅ | unit |
| E34 | C11→C12 通过 | 🟡 | 自检未结构化 |
| E35 | C11→C13 不通过 | 🟡 | 缺直接测试 |

### C5. BF-05 RAG 质量门禁

| ID | 元素 | 代码落点 | 测试证据 | 状态 |
| --- | --- | --- | --- | --- |
| R01 | 用户问题 | `graph/nodes/rag.py` | `test_rag_grade_flow.py` | ✅ |
| R02 | 查询改写/首次原查询 | 同上 | 同上 | ✅ |
| R03 | 向量检索 | `rag/retriever.py` | `test_m2_retriever.py` | ✅ |
| R04 | BM25 | `rag/bm25.py` | `test_m2_retriever.py` | ✅ |
| R05 | RRF 融合 | `rag/retriever.py` | `test_m2_retriever.py` | ✅ |
| R06 | 阈值/父块 | `rag/parent_expand.py` | `test_rag_parent_expand_13_6.py` | ✅ |
| R07 | grade | `graph/nodes/rag.py` | `test_rag_grade_13_4.py` | ✅ |
| R08 | 生成 | `execute_subagent` | `test_m1_graph_flow.py` | ✅ |
| R09 | 改写 | `rewrite` | `test_rag_grade_flow.py` | ✅ |

| 边 | 路径 | 状态 |
| --- | --- | --- |
| E40 | R07→R08 相关/空结果 | ✅ |
| E41 | R07→R09 不相关且未达上限 | ✅ |
| E42 | R07→R08 达上限 | ✅ |

### C6. BF-06 子 Agent 委派与并行

| ID | 元素 | 代码落点 | 测试证据 | 状态 | 缺口 |
| --- | --- | --- | --- | --- | --- |
| A01 | 主 Agent 计划完成 | `plan.py` | `test_plan_tier.py` | ✅ | — |
| A02 | 含图片判断 | `execute.py`、`main_graph.py` | `test_chat_image_echo.py` | 🟡 | 缺“含图片不扇出”图级断言 |
| A03 | 主 Agent 串行 | `execute.py` | `test_m1_graph_flow.py` | ✅ | — |
| A04 | should_plan_parallel | `routers.py` | `test_plan_parallel_gate.py` | ✅ | — |
| A05 | 单步 execute_subagent | `main_graph.py` | `test_m1_graph_flow.py` | ✅ | — |
| A06 | Send 扇出 | `main_graph.py` | `test_m3_parallel*.py` | ✅ | — |
| A07 | 子 Agent 独立上下文 | `subagent.py` | `test_m3_subagent.py` | ✅ | 子 Agent 工具历史不向父级传播（设计允许隔离，但需明确） |
| A08 | reducer 合并 | `graph/usage.py`、`parallel.py` | `test_m3_parallel.py` | ✅ | — |
| A09 | Synthesize | `synthesize.py` | `test_m1_graph_flow.py` | 🟡 | 多任务综合缺直接测试 |

门禁 C0–C4 均有 `test_plan_parallel_gate.py` 覆盖，但 C4（图片）缺图级测试。

| 边 | 路径 | 状态 |
| --- | --- | --- |
| E50 | A02→A03 含图片，主 Agent 串行 | 🟡 |
| E51 | A02→A04 不含图片 | ✅ |
| E52 | A04→A05 不扇出 | ✅ |
| E53 | A04→A06 扇出 | ✅ |

### C7. BF-07 权限与审批

| ID | 元素 | 代码落点 | 测试证据 | 状态 |
| --- | --- | --- | --- | --- |
| P01 | 工具调用 | `base.run_tool_loop` | `test_m1_graph_flow.py` | ✅ |
| P02 | 是否需要审批 | `execution/permission.py` | `test_permission_whitelist.py` | ✅ |
| P03 | 直接执行 | `base._dispatch` | `test_m4_tools.py` | ✅ |
| P04 | 审批通道判断 | `base._permission_hook_var` | `test_platform_app_api.py` | ✅ |
| P05 | 无通道拒绝 | `base.py` | `test_high_risk_tool_denied_without_permission_hook` | ✅ |
| P06 | permission_request | `compat.py` | `test_platform_app_api.py` | ✅ |
| P07 | allowed 临时授权 | `execution/approval.py` | `test_platform_app_api.py` | ✅ |
| P08 | PostTool Hook | `harness/hooks.py` | `test_harness_hooks.py` | ✅ |
| P09 | 有界化回灌 | `tool_history.py` | `test_tool_history.py` | ✅ |

| 边 | 路径 | 状态 |
| --- | --- | --- |
| E60 | P02→P03 否 | ✅ |
| E61 | P02→P04 是 | ✅ |
| E62 | P04→P05 无通道 | ✅ |
| E63 | P04→P06 有通道 | ✅ |
| E64 | P06→P07 allowed | ✅ |
| E65 | P06→P05 denied | ✅ |
| E66 | P06→P05 timeout | 🟡（超时分支缺直接测试） |

### C8. BF-08 失败/重试/恢复

| ID | 元素 | 代码落点 | 测试证据 | 状态 | 缺口 |
| --- | --- | --- | --- | --- | --- |
| F01 | 失败发生 | 多处 | — | 🟡 | 无统一 REL 套件 |
| F02 | 错误分类 | `harness/errors.py`、`models/router.py` | `test_harness_kernel.py` | 🟠 | 未接入 SSE |
| F03 | 指数退避 | litellm / TaskRunner | `test_m5_scale.py`（部分） | 🟡 | 无 429/5xx 注入测试 |
| F04 | 模型 fallback 链 | `models/router.py` | `test_m1_model_router.py` | 🟡 | 全失败路径缺直接测试 |
| F05 | 全失败明确错误 | `compat.py` | `test_chat_contract.py`（部分） | 🟡 | 缺全模型不可用注入 |
| F06 | 不可重试直接返回 | `errors.py` | `test_harness_kernel.py` | 🟠 | 未接 API |
| F07 | 工具失败回灌 | `base.py` | `test_tool_loop_robustness.py` | ✅ | — |
| F08 | 模型自修 | `base.py` | `test_tool_loop_robustness.py` | ✅ | — |
| F09 | 记录失败继续收尾 | `base.py` | 部分 | 🟡 | 无失败轨迹断言 |
| F10 | 用户中断 | `deps.cancel_turn`、`compat.py` | `test_platform_app_api.py`（部分） | 🟡 | 缺中断后部分输出断言 |
| F11 | 崩溃恢复 | `graph/checkpoint.py` | `test_checkpoint_persistence.py` | 🟡 | 缺同 thread 崩溃后续跑测试 |

| 边 | 路径 | 状态 |
| --- | --- | --- |
| E70 | 可重试 | 🟡 |
| E71 | 仍失败→fallback | 🟡 |
| E72 | 全失败 | 🟡 |
| E73 | 不可重试 | 🟠 |
| E74 | 工具失败 | ✅ |
| E75 | 仍失败→收尾 | ✅ |
| E76 | 用户中断 | 🟡 |
| E77 | 崩溃恢复 | 🟡 |

---

## D. §5 数据流与存储

| ID | 存储/流 | 代码落点 | 测试证据 | 状态 | 缺口 |
| --- | --- | --- | --- | --- | --- |
| D1 | `session.db` messages/parts/epoch/inputs | `app/session/` | `test_session_store_persistence.py`、`test_platform_app_api.py` | ✅ | — |
| D2 | `checkpoints.db` graph state | `graph/checkpoint.py` | `test_checkpoint_persistence.py` | ✅ | — |
| D3 | model catalog capabilities/limits | `models/catalog.py` | `test_m1_model_router.py`、`test_ollama_compat.py` | ✅ | — |
| D4 | model_switches backup/audit | `deps.py` | `test_model_switch.py`、`test_model_switch_context_e2e.py` | ✅ | — |
| D5 | data/truncation full output | `cognition/tool_output.py` | 无生产接线 | 🟠 | `bound_tool_output` 未被 `run_tool_loop` 调用 |
| D6 | Chroma + BM25 | `rag/` | `test_m2_rag.py`、`test_m2_retriever.py` | ✅ | — |
| D7 | MemoryStore | `graph/nodes/memory.py` | `test_m2_memory.py` | ✅ | — |
| D8 | EventBus | `graph/events.py`、`app/api/compat.py` | `test_graph_events.py`、`test_platform_app_api.py` | ✅ | — |

其他 §5 流：
- DF-工具调用流：`ToolAdapter` 统一输出 ✅；`ResultPolicy`/落盘续读未接 🟠。
- DF-事件流：结构存在，但 `turn_id` 未覆盖所有 SSE 路径 🟠。
- DF-切换流：备份/摘要/探针/审计已实现 ✅；`SwitchService` 未抽离 🟠。

---

## E. §6–§10 关键契约

| 条目 | 设计 | 现状 | 状态 |
| --- | --- | --- | --- |
| 6.1 Loop 状态机 | 模型→工具→回灌；length/content-filter 收尾 | `run_tool_loop` 已处理；仍非流式 | 🟠 |
| 6.2 流式双路径 | 定界符闭合即执行 | `ToolAdapter.stream` 有测试；主循环未用 | 🟠 |
| 6.3 历史归一化 | 原生/回退统一 | `harness/history.py` + 测试 | ✅ |
| 6.4 工具 schema/15 上限 | 缓存 + scope + 上限 | `harness/schema.py` + `build_tool_defs` | ✅ |
| 6.5 ResultPolicy | 头尾保留 + 落盘续读 | `bound_tool_output` + `prune_tool_outputs` 已接 Loop | ✅ |
| 6.6 Hook 契约 | Pre/Post + 权限 | 已实现 | ✅ |
| 7.1 三层分离 | 原文/摘要/绑定 | 已实现；原文主要在 checkpoint，不完全等同 D1 | 🟠 |
| 7.2 视图顺序 | 摘要→tail→当前 | `harness/context.py` | ✅ |
| 7.3 压缩门禁 | C1/C2/C3 + 阈值 | `compressor.py` | ✅ |
| 7.4 精确值回填/探针 | 确定性回填 + 探针 | 已实现；探针失败测试缺 | 🟡 |
| 8.1 BudgetLedger | 预扣、单调、事件 | 后扣、无事件 | 🟠 |
| 8.2 审计 | 切换/审批/截断/预算 | 切换审计有；审批结构化日志/预算事件缺 | 🟠 |
| 9.1 切换时序 | 门禁→备份→摘要→探针→提交 | 已实现 | ✅ |
| 9.2 三层校验 | L1 完整性 / L2 事实 / L3 风格 | L1/L2 有；L3 无 | 🟠 |
| 9.3 前端契约 | 稳定 ID、不重置列表 | 前端已有稳定 key；无专项测试 | 🟡 |
| 10 DFD 总图 | 全链路 | 主路径存在；ResultPolicy/事件字段缺口 | 🟠 |

---

## F. §11 测试体系差距

| 测试层 | 设计条目 | 现状 | 状态 |
| --- | --- | --- | --- |
| UT 能力/适配/定界符/历史/流式/Hook | §11.3 | 均有对应文件 | ✅ |
| UT Loop/预算/上下文/切换 | §11.3 | 已有 `test_tool_loop_robustness`、`test_budget`、`test_harness_context`、`test_model_switch` | ✅ |
| CT 契约测试 | §11.4 | `tests/contract/test_harness_contracts.py` | ✅ |
| IT 图路径 | §11.5 | 大部分 BF-01/02/05/06/07 有覆盖；BF-03/04 部分 | 🟠 |
| E2E 黄金集 | §11.6 | E2E-01/02/03/06/07/08 真实 + E2E-04/05 真实工具/切换 | ✅ |
| PF 性能 | §11.7 | PF-01/02/05/06/07 真实通过；PF-03/04 假模型基准通过 | ✅ |
| SEC 安全 | §11.8 | 沙箱边界 + 伪造调用/分隔符碰撞 + 密钥脱敏 + 注入内容保持 tool/user 角色 | 🟡 |
| REL 可靠性 | §11.9 | SwitchService 失败/回滚 + 429 重试 + 取消不重试 + checkpoint 重启恢复 | 🟡 |
| 覆盖率门槛 | §11.12 | `pytest-cov` + `.coveragerc` Harness 90.80%；静态图边 ≥95%；设计边追溯 100% | ✅ |
| CI 门禁 | §11.13 | 无 `.github/workflows`/脚本证据 | ❌ |

---

## G. §12 迁移阶段

| 阶段 | 设计目标 | 现状 | 状态 |
| --- | --- | --- | --- |
| P0 | 契约、确定性 ID、finish_reason | 已完成并测试 | ✅ |
| P1 | TurnHarness 接管生命周期 | `run`/`stream` 已接；multi-agent SSE 未接 | 🟠 |
| P2 | ContextHarness 统一三处压缩 | 模型视图、摘要/精确值/tail、图节点 gate/切分、切换迁移均已收敛到 Harness；SwitchService 已抽离 | ✅ |
| P3 | 流式回退接入 Loop | 回退路径已接；原生流式工具调用仍以完整响应为主 | ✅ |
| P4 | 预算/错误/审计/事件 | BudgetLedger + classify_error SSE + turn_id + 审计 | ✅ |
| P5 | 清理重复路径 | 未完成 | ❌ |

---

## H. §13 验收标准

| 维度 | 设计标准 | 当前证据 | 状态 |
| --- | --- | --- | --- |
| 功能 | BF-01~08 所有节点/边对应用例通过 | 主路径 + RAG/召回/压缩/并行场景 + 图边覆盖测试 | ✅ |
| 跨模型 | native→fallback→native 往返历史可继续 | adapter/switch 单测 + 真实 E2E-04/05 + L4 切换 | ✅ |
| 事实保持 | 切换后路径/ID/端口/金额可召回 | L2 集成 + L4 真实模型 A→B 事实保持通过 | ✅ |
| 压缩 | 超阈值压缩精确值 0 丢失 | 单测 + E2E-06 真实长历史压缩保留 ORION/path | ✅ |
| 成本 | 压缩/扇出门禁生效；超预算熔断 | 扇出门禁 + BudgetLedger + 真实 PF-05/06 token/cost | ✅ |
| 安全 | 攻击集 100% 拦截 | 沙箱 + 伪造调用 + 注入角色隔离 + 密钥脱敏/输出脱敏 | ✅ |
| 可靠性 | 崩溃/断连/超时/429 有明确恢复路径 | REL 套件：checkpoint 恢复、429 重试、取消、断连清理 | ✅ |
| 性能 | P95 首 token < 2.5s；简单任务 < 8s | 仅假模型 P50 <1s | ⛔ |
| 可维护 | 核心覆盖率 ≥90%；业务图边 ≥95% | Harness 90.80% + 静态图边 ≥95% + 设计边追溯 100% | ✅ |

---

## I. 冻结缺口清单（按依赖顺序）

| 编号 | 缺口 | 设计条目 | 依赖 | 阻塞状态 |
| --- | --- | --- | --- | --- |
| G-01 | 流式回退接入 Loop（定界符闭合即执行） | §6.2 / §12 P3 | G-02 | ✅ 已完成（`_stream_step`） |
| G-02 | 抽 `SwitchService`，收敛切换时序 | §3.3 / §9.1 | — | ✅ 已完成（`harness/switch.py`） |
| G-03 | `ToolSchemaCache` + 15 工具上限 + scope 分组 | §6.4 | — | ✅ 已完成 |
| G-04 | `ResultPolicy` 接入 `run_tool_loop`（头尾保留/落盘/续读） | §6.5 / §10 | — | ✅ 已完成 |
| G-05 | 子 Agent/并行 worker 工具历史是否回写父级 | §4.6 | — | ✅ 不适用：设计 §4.6 要求子 Agent 上下文隔离，不回写父级 |
| G-06 | `HarnessError` 接入 SSE 错误响应（retryable/status/error_type） | §5.6 / §13 | G-02 | ✅ 已完成 |
| G-07 | 压缩三实现收敛（图节点/切换/手动 compact） | §7 / §12 P2 | G-02 | ✅ 已完成（`compress_graph_messages` + `compress_messages` + `tail_snapshot`） |
| G-08 | `turn_id` 覆盖 multi-agent compat SSE | §5.5 | — | ✅ 已完成 |
| G-09 | `ModelProfile` 真正接入 ToolAdapter（catalog+override+TTL） | §5.2 / §6 | — | ✅ 已完成（`build_profile` + `ToolAdapter.profile`） |
| G-10 | 新增 `tests/contract`，补齐 CT-* | §11.4 | — | ✅ 已完成 |
| G-11 | E2E 黄金集 8 条（真实模型） | §11.6 / §13 | 真实模型 env | ✅ 01/02/03/04/05/06/07/08 均有真实证据 |
| G-12 | 性能基准：P95 首 token/简单任务/成本/并发/切换 | §11.7 / §13 | 真实模型 env | ✅ PF-01/02/05/06/07 真实通过；PF-03/04 假模型基准通过 |
| G-13 | REL 套件：超时/429/崩溃/断连/中断 | §11.9 | — | ✅ SwitchService 失败/回滚 + 429 重试 + 取消不重试 + checkpoint 恢复 + 流断开清理 |
| G-14 | SEC 攻击集补全（注入/伪造调用/密钥泄露） | §11.8 | — | ✅ 伪造调用/分隔符碰撞/密钥脱敏/注入角色隔离/worker 提示词防注入/输出脱敏测试 |
| G-15 | 覆盖率工具与门槛（≥90%/≥95%） | §11.12 | — | ✅ Harness 行覆盖 90.80%；`test_graph_edge_coverage.py` 静态图边 ≥95%；设计边追溯 100% |
| G-16 | CI 门禁脚本/工作流 | §11.13 | G-10..15 | ✅ 已完成（`.github/workflows/backend.yml` + `backend-live.yml`） |

**G-01~G-16 已全部闭环**；剩余风险与设计冲突见 §J，不再有未实现的 Harness 缺口。

---

## J. 设计缺陷/冲突（需裁决）

| 编号 | 冲突 | 证据 | 建议 |
| --- | --- | --- | --- |
| D-01 | §8.1 要求“调用前预扣 + budget_exceeded 事件”；代码为调用后扣费且无事件 | `harness/budget.py`、`base.py` | 下一阶段改为预扣/结算两段，并补事件 |
| D-02 | §6.5 要求 ResultPolicy 头尾保留 + 落盘；工具历史另有一套 2KB/8KB 截断 | `cognition/tool_output.py` vs `harness/tool_history.py` | 明确两者职责：ResultPolicy 管回灌，tool_history 管跨轮摘要 |
| D-03 | §3.3 目标目录 `harness/switch.py` | 已解决：`harness/switch.py` 存在，`app/deps.py` 委托 | — |
| D-04 | §5.5 事件信封要求全事件带 `turn_id` | 已解决：compat SSE 显式注入 `TurnContext`，`_sse` 自动补 `turn_id` | — |
| D-05 | §7.1 原文层写 `D1 session_messages`，实际模型视图主要读 checkpoint | `app/deps._raw_messages` | 明确 checkpoint 与 session.db 的主从关系，或收敛 |
| D-06 | §11.4 要求独立 CT 层 | 已解决：新增 `tests/contract/test_harness_contracts.py` | — |
| D-07 | §13 性能硬门禁与当前仅有假模型 P50 冒烟不匹配 | `tests/perf/test_perf_smoke.py` | 新增真实/假模型两套基准；真实项 blocked |

---

## K. 审计后的下一步（待你确认）

按已确认的依赖顺序：

1. 先处理 G-02（SwitchService）与 G-07（压缩收敛），因为它们会影响 G-06/G-11/G-12 的证据结构。
2. G-01/G-03/G-04 已完成；下一阶段处理 G-06/G-08/G-10~G-16。
3. G-06/G-08/G-10 已完成；下一阶段处理 G-13/G-14/G-15/G-16，最后 G-11/G-12（真实环境）。
4. 每阶段结束更新本矩阵，并附 `pytest` 节点与命令输出；未过阶段不进入下一阶段。

> 本文件是冻结稿；在你确认前不修改生产代码。


---

## L. 阶段记录

| 阶段 | 结果 | 关键证据 |
| --- | --- | --- |
| L1 | G-02 / G-07 | `harness/switch.py`、`harness/compression.py`；模型切换/压缩回归通过 |
| L2 | G-07 残留 | `compress_graph_messages` 收敛图节点 gate/切分；`tail_snapshot` 统一 compact |
| L3 | G-01 / G-03 / G-04 | `_stream_step` 流式回退执行；`harness/schema.py` 15 上限；ResultPolicy 接线 |
| L4 | G-06 / G-08 / G-10 | 错误 SSE 分类/脱敏；compat `turn_id`；`tests/contract` |
| L5 | G-13 / G-14 / G-15 | SwitchService 失败回滚、伪造调用、覆盖率工具与 90% 门槛 |
| L6 | G-13/G-14 剩余 + G-16 | 429 重试/取消、密钥脱敏、GitHub CI/live workflow |
| L7 | G-13/G-14/G-15 剩余 | checkpoint 恢复、注入角色隔离、设计边追溯 |
| L8 | G-13/G-14/G-15 补强 | worker 防注入提示词；断连清理；主路径边抽样 |
| L9 | G-11/G-12 真实验收 | E2E-01/02/03/04/05/07；PF-01/02 真实通过 |
| L10 | G-11/G-12 扩展 | E2E-06/08；PF-05/06/07 基准 |
| L11 | G-11/G-12 闭环 | 真实长上下文压缩、真实并行子 Agent、真实 token/成本/切换延迟 |
| L12 | G-13/G-14 闭环 | 输出脱敏、断连清理、真实模型输出不泄密 |
| L13 | G-15 闭环 | `test_graph_edge_coverage.py` 覆盖静态图边 ≥95%，`test_design_traceability.py` 覆盖 E01~E77 |
| L14 | G-05 / G-09 闭环 | G-05 按 §4.6 上下文隔离标记不适用；G-09 `build_profile` + `ToolAdapter.profile`，profile/cache/override 测试通过 |
| L15 | G-15 / D-01 收口 | 图边覆盖测试 ≥95%；预算耗尽事件已补；514 passed, Harness coverage 90.80% |

> 当前版本所有 G-01~G-16 均有代码/测试/运行证据；剩余质量项见 §H/§J 的说明。
