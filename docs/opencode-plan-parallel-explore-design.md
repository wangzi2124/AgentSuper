# plan→build 并行 explore 委派（探索事实喂给 build）设计

> ⚠️ **已废弃（SUPERSEDED，2026-09-12）**：本方案曾实现（plan 与 explore 在
> `_route_plan_then_build` 中 `asyncio.gather` 并行、事实注入 build），随后按产品决定**回退**，
> 改为严格对齐 opencode：**顶层只有 `build` / `plan` 两个命令，explore 一律作为委派目标**
> （由 `build` 的 `tool_task` 委派），supervisor 不再直连 explore。
> 保留本文仅作决策记录；现行架构见 `docs/opencode-explore-plan-alignment.md`。

> 调研依据：`backend/app/agent/supermod/`、`backend/app/agent/graphmod/`、`backend/app/agent/sub_tools.py`、
> `backend/app/agent/agent_specs.py`、`backend/app/agent/bus.py`。
> 本文只描述设计，不含实施；评审通过后按「五、分阶段实施」执行。
> 关联文档：`docs/opencode-task-delegation-design.md`（task 工具整体对齐）、
> `docs/opencode-explore-plan-alignment.md`（explore/plan 规格对齐）。

## 一、背景与目标

### 1.1 已具备的委派基建（本设计的前提，非缺口）

经源码核实，opencode 风格的委派基建**已经完整存在**，本设计不重复造：

| 基建 | 位置 | 状态 |
|---|---|---|
| task 工具真实现（schema + 深度守卫 + 权限审批 + task_id resume + background） | `graphmod/tools.py:100 _tool_task` | ✅ 完整 |
| 子会话往返（send_and_wait / 子 thread / 用量独立核算） | `bus.py:21 AgentBus` | ✅ 完整 |
| 子 Agent 规格注册表（build 全量 / explore 只读 / plan 纯 LLM） | `agent_specs.py:45-65` | ✅ 完整 |
| 子 Agent 工具循环 + allowlist 裁剪 + 运行时硬拒绝 | `sub_tools.py:334 run_tool` / `:365 tool_loop_chat` | ✅ 完整 |
| supervisor 并行子任务执行（asyncio.gather） | `supermod/parallel.py:53 _execute_parallel`（gather 于 `:140`） | ✅ 完整 |
| 顺序路由到单个子 Agent（收唯一回复） | `supermod/core.py:236 _collect_route` | ✅ 完整 |

### 1.2 缺口：plan→build 交接是「串行且 plan 盲探」

`supermod/core.py:252 _route_plan_then_build` 当前流程（逐行核实）：

```
252: async def _route_plan_then_build(self, payload, original_thread_id):
264:     plan_reply = await self._collect_route("plan", payload, original_thread_id)   # ← 串行
277:     plan_answer = plan_reply.payload["answer"]
279:     build_question = "…plan agent 已产出以下实施计划…" + plan_answer
287:     build_reply = await self._collect_route("build", build_payload, original_thread_id)  # ← 串行
295:     yield self._merge_plan_build_reply(plan_reply, build_reply)
```

问题：

1. **plan 是纯 LLM**（`agent_specs.py:59-64`，`tools=()`），**自身无工具、无法 spawn explore**；
2. plan 拿到的是**未经探索的用户原问题**，只能凭模型先验「盲写」计划，易与真实代码结构脱节；
3. 委派基建（explore 只读子 Agent）在**这条路径上从未被使用**——委派能力有，但 plan→build 流程没接。

**这正是「委派基建的实际用例」空缺所在**：不是缺工具，而是缺「plan→build 交接时用委派补齐代码库事实」这一格。

### 1.3 目标

在 `plan→build` 交接中插入一次**并行只读 explore 委派**，并**打破串行**——plan 与 explore 同时委派，
把探索到的代码库事实**喂给 build**：

- **并行**：plan 与 explore 并发（`asyncio.gather`），不额外增加一次串行往返；
- **喂给 build**：执行阶段直接引用事实定位文件/接口，减少盲搜与重复探索。

**非目标**（见「六、不做」）：不改 plan 的纯 LLM 契约、不新增工具注册、不改前端、不引入 plan 自 spawn。

## 二、opencode 对齐依据

`packages/opencode/src/session/prompt/plan-mode.txt`：

> Phase 1: Initial Understanding —— **Launch up to 3 explore agents IN PARALLEL**, single message,
> multiple tool calls, minimum number of agents necessary (usually just 1).

要点：

- plan 阶段**先并行只读探索**（≤3，通常 1）→ 聚合探索结果 → 再产出计划；
- 探索是**只读**的（explore 的 ruleset deny 所有写/执行工具，`agent.ts`）；
- 探索结果作为 plan 的**输入上下文**，plan 本身仍是纯规划（不执行变更）。

本实现因 plan `tools=()`（纯 LLM）无法自 spawn，且按需求「探索事实喂给 build」，
故由 **supervisor 代劳并把 explore 与 plan 并行**：explore 事实注入 build 的执行消息。

## 三、设计

### D1 打破串行：plan 与 explore 并行委派

`_route_plan_then_build` 原来是**串行** plan→build（先 plan 再 build）。现改为
**plan 与只读 explore 同时委派**（`asyncio.gather`），省去一次串行往返：

```python
plan_reply, explore_facts = await asyncio.gather(
    self._collect_route("plan", payload, original_thread_id),        # 出计划
    self._collect_explore_facts(payload, original_thread_id, q),     # 并行只读探索代码库
)
```

- **并行语义**：`plan` 与 `explore` 各自独立子会话、独立 thread，同时进入 `send_and_wait`；
  二者都完成后才进入 build。这是本设计相对原「串行 plan→build」的核心改动。
- **explore 之间也可并行**：`_collect_explore_facts` 内部以 `asyncio.gather` 并发 N 个探索子任务
  （N = `plan_parallel_explore_fanout`，1..3，默认 1；对齐 opencode「usually just 1」）。
- **只读保证**：目标 agent = `"explore"`，其 allowlist = `_READONLY_TOOL_NAMES`（`agent_specs.py:53-58`），
  schema 裁剪 + 运行时硬拒绝双层保证**结构只读**，无需额外权限代码。
- **explore 未注册**：`_collect_explore_facts` 先查 `self._bus.list_agents()`，无 `explore` 直接返回空串，
  不发起路由（避免白等子 Agent 超时）。

### D2 事实注入 build（执行阶段复用探索结果）

因 plan 与 explore **并行**，plan 不再消费探索事实；事实在 build 组装处注入：

```python
explore_banner = (
    f"【代码库探索事实（只读考察所得，未做任何修改）】\n{explore_facts}\n\n"
    if explore_facts else ""
)
build_question = (
    explore_banner
    + "用户请求先规划再执行。plan agent 已产出以下实施计划"
    + (f"（计划文件: {plan_path}）" if plan_path else "")
    + "，请按计划逐步执行并报告完成情况（可参考上述探索事实定位文件/接口）：\n\n"
    + plan_answer
)
```

- build 仍走原 `_collect_route("build", …)`，**合并回复契约不变**；
- 事实仅存在于喂给 build 的输入，不进入最终 answer 正文，避免回复膨胀。

### D3 与 opencode 的取舍说明

opencode `plan-mode.txt` 是「plan primary 自己并行 spawn explore → 用结果写计划」（explore **喂 plan**）。
本实现因 plan 是纯 LLM（`tools=()`）无法自 spawn，且按需求「探索事实喂给 build」，
故由 supervisor 代劳并**并行化**：explore 与 plan 同时跑，事实只喂 build。

- 收益：一次并行往返（不额外串行）；build 执行有代码库事实，省去盲搜。
- 代价：plan 本身不消费探索事实（保持高层规划）。若后续要「explore 先于 plan 以喂养计划」，
  把 `gather` 拆回「先 explore 再 plan」即可（D1 代码单点可切）。

### D4 增强（可选，二期）：LLM 分解多探索子任务

`_collect_explore_facts` **已支持** `plan_parallel_explore_fanout`（1..3）——同题并行发 N 个探索。
二期可把它升级为「LLM 分解成 ≤3 个**不同**只读子问题」（对齐 plan-mode.txt「up to 3」）：

```
explore_questions = decompose_explore(question)[:fanout]   # 而非同一问题重复 N 次
```

- 默认 `fanout = 1`；仅当问题明确涉及「大范围/多模块代码理解」时才建议调高，避免小任务过度探索。

### D5 静默回退与契约保持

| 场景 | 行为 |
|---|---|
| `explore` 未注册（`list_agents()` 无 explore） | 直接返回 `explore_facts = ""`，不发起路由 |
| explore 路由失败 / 返回 None / 超时 / 返回 error | 跳过该条，聚合其余；全失败 `explore_facts = ""` |
| `explore_facts == ""` | plan 与 build 输入**逐字**退回原实现（串行 plan→build 行为） |
| 最终回复契约 | 不变：仍为单条 `AgentMessage`（`_merge_plan_build_reply` 产出），`plan_path`/`sources`/`steps`/`tokens` 合并逻辑不变 |

**关键**：本设计是**纯增量**——所有新增路径失败都回落到现有行为，不引入新的失败模式。

### D6 用量与可观测

- explore 的 token/cost 经 `_collect_route` → `_route_to`（`core.py:164-166` 的 `_merge_usage`）计入本次请求汇总；
- 事件：explore 走 supervisor 内部路由（非顶层 multi-agent 流），**不额外推 SSE 面板事件**；
  如需可见性，可在 `steps` 中追加一条 `{"agent":"explore","status":"explored"}`（对齐现有 steps 汇总，可选）。

## 四、落点清单（精确到文件/函数）

| # | 文件 | 落点 | 改动 |
|---|---|---|---|
| 1 | `backend/app/agent/supermod/core.py` | 新增 `_collect_explore_facts` | 并行只读探索聚合（`list_agents` 守卫 + fanout + 截断） |
| 2 | `backend/app/agent/supermod/core.py` | `_route_plan_then_build` | plan 与 explore **并行**委派（`asyncio.gather`）；事实注入 build_question |
| 3 | `backend/app/config.py` | Settings | `plan_parallel_explore_enabled`(true) / `plan_parallel_explore_fanout`(1) / `plan_explore_facts_max_chars`(5000) |
| 4 | `backend/tests/test_supermod_extra.py` | 用例 | explore 并行委派且事实入 build；explore 出错静默回退；原 handoff 用例关开关保契约 |

**不改动**：`graphmod/*`、`sub_tools.py`、`agent_specs.py`、`bus.py`、前端（全部保持现状）。

## 五、分阶段实施

1. **P0（核心格）✅ 已实现**：`_collect_explore_facts` + `_route_plan_then_build` 中 `asyncio.gather(plan, explore)`，
   事实注入 build；静默回退（D5）。
2. **P1（测试）✅ 已实现**：`tests/test_supermod_extra.py`
   - `test_plan_parallel_explore_facts_injected`：plan/explore 并行（顺序无关）→ build question 含探索事实；
   - `test_plan_parallel_explore_fallback_on_error`：explore error → 无事实前缀、仍产出单条合并回复；
   - 原 `test_handle_plan_then_build_handoff` 显式关开关，保留串行契约断言。
3. **P2（可观测，可选）**：steps 追加 explore 条目。
4. **P3（增强，可选）**：D4 LLM 分解 ≤3 并行探索。

## 六、不做（本次范围外）

- 不实现 plan 自 spawn explore（保持 plan `tools=()` 纯 LLM 契约，由 supervisor 代劳）；
- 不改前端 `agentMode`（explore 降级为 subagent 属 `opencode-task-delegation-design.md` 的 P3，另议）；
- 不新增 `tool_task` 到 `sub_tools._TOOL_SCHEMAS`（子 Agent 侧委派注册是另一条线，且 explore 结构只读不应有 task）；
- 不做跨会话上下文共享（explore 上下文独立，父仅取事实文本）；
- 不引入 subagent 级 cost 表（沿用子会话独立 usage 汇总）。

## 七、核心实现（最终代码）

### 8.1 Settings（`backend/app/config.py`）

在 `sub_task_fresh_history`（`:203`）之后追加：

```python
    # [opencode plan-mode 对齐] plan→build 交接前的并行只读探索：把代码库事实喂给 plan/build。
    plan_parallel_explore_enabled: bool = True
    # 并行 explore 数量（1..3，对齐 opencode plan-mode「up to 3, usually just 1」）
    plan_parallel_explore_fanout: int = 1
    # 注入 plan/build 的探索事实文本上限（字符），超限截断
    plan_explore_facts_max_chars: int = 5000
```

### 8.2 探索事实聚合 helper（`backend/app/agent/supermod/core.py`，新增方法）

```python
    async def _collect_explore_facts(
        self,
        payload: dict,
        original_thread_id: str,
        question: str,
    ) -> str:
        """[opencode plan-mode 对齐] 并行只读探索代码库，聚合「探索事实」文本。

        plan 是纯 LLM（agent_specs: plan tools=()），自身无法 spawn explore；
        由 supervisor 代劳：并行路由到只读 explore 子 Agent（allowlist 结构只读），
        把各探索结果聚合成事实块，供 plan/build 引用真实文件/接口。

        纯增量、静默回退：未启用 / 未注册 / 超时 / 报错一律跳过，全失败返回空串，
        调用方据此退回原串行 plan→build 行为，不引入新失败模式。
        """
        if not settings.plan_parallel_explore_enabled:
            return ""
        # explore 未注册（无只读子 Agent）时直接跳过，避免白等子 Agent 超时
        try:
            if "explore" not in self._bus.list_agents():
                return ""
        except Exception:  # noqa: BLE001
            pass
        try:
            fanout = max(1, min(3, int(settings.plan_parallel_explore_fanout or 1)))
        except (TypeError, ValueError):
            fanout = 1

        explore_question = (
            "请只读探索当前代码库，收集与本任务相关的文件位置、关键接口/函数、"
            "目录结构与实现事实，供后续规划与执行直接引用。"
            "不要修改任何文件，不要执行任何命令，只输出结构化的代码库地图。\n\n"
            "任务：\n" + str(question)
        )

        async def _one() -> str:
            explore_payload = dict(payload)
            explore_payload["question"] = explore_question
            try:
                reply = await self._collect_route("explore", explore_payload, original_thread_id)
            except Exception as e:  # noqa: BLE001
                logger.warning("plan→build explore delegation failed: %s", e)
                return ""
            if reply is None or reply.type == "error":
                return ""
            return str((reply.payload or {}).get("answer", "")).strip()

        answers = await asyncio.gather(*[_one() for _ in range(fanout)])
        facts = "\n\n---\n\n".join(a for a in answers if a)
        try:
            limit = int(settings.plan_explore_facts_max_chars or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit and len(facts) > limit:
            facts = facts[:limit] + "\n…[探索事实过长，已截断]"
        return facts
```

### 8.3 交接改造（`_route_plan_then_build`）

```python
        # [opencode plan-mode 对齐] 打破串行 plan→build：plan 与只读 explore **并行**委派，
        # explore 事实注入 build 的执行消息（执行直接定位文件/接口，省去盲搜）。
        question = str(payload.get("question", ""))
        plan_reply, explore_facts = await asyncio.gather(
            self._collect_route("plan", payload, original_thread_id),
            self._collect_explore_facts(payload, original_thread_id, question),
        )
        # …（plan_reply 校验原样保留）…

        plan_answer = str(plan_reply.payload.get("answer", ""))
        plan_path = plan_reply.payload.get("plan_path", "")
        explore_banner = (
            f"【代码库探索事实（只读考察所得，未做任何修改）】\n{explore_facts}\n\n"
            if explore_facts else ""
        )
        build_question = (
            explore_banner
            + "用户请求先规划再执行。plan agent 已产出以下实施计划"
            + (f"（计划文件: {plan_path}）" if plan_path else "")
            + "，请按计划逐步执行并报告完成情况（可参考上述探索事实定位文件/接口）：\n\n"
            + plan_answer
        )
        build_payload = dict(payload)
        build_payload["question"] = build_question
        # …（build 路由与合并原样保留）…
```

**回退不变式**：`explore_facts == ""` 时，`build_question` 与原实现逐字一致（仅去掉探索前缀）。

## 八、风险与缓解

| 风险 | 缓解 |
|---|---|
| 增加一次 explore 往返 → 端到端变慢 | 默认 fanout=1 且可开关；explore 走短超时（`_timeout_for("explore")`）；小任务可关 |
| explore 事实块过长撑大 plan/build 输入 | 事实块截断上限（建议 4–6K 字符，对齐 `attachment_context_text` 预算风格） |
| plan 误把「事实」当「已完成」 | 事实块明确标注「只读考察所得，未做任何修改」 |
| 与 supervisor 顶层多 agent 事件混淆 | explore 走内部路由，不推顶层 SSE 面板事件 |
