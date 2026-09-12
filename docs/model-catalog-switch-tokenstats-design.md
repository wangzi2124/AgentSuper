# 模型目录、会话级模型切换与 Token/成本统计 设计

> 依据：`E:\project\opencode-dev` 源码调研（模型配置 / 模型切换 / token 统计三份报告）。
> 目标：把 opencode 的"模型包 + 会话级切换 + usage 归一化 + 成本核算"这套模型治理能力搬进本项目，
> 同时复用本项目已有的 session.db / SSE / 监控基建，做最小侵入式改造。

## 1. 背景与目标

当前本项目（AgentSuper）模型能力分散在三处、互不打通：

- 前端 `frontend/src/config/models.ts` 用**静态常量** `SUPPORTED_MODELS` 驱动选择器；
- 后端每次请求靠 `ChatRequest.model` 透传给 supervisor → 子 Agent，`session.model` 只在建会话时写入，之后不再同步；
- token 只在 **assistant 消息 `data.tokens`** 上持久化，`sessions` 表的 `tokens_*` 聚合列**从未被写入**
  （`add_session_usage` 定义了却没有任何生产调用点），成本 `cost` 全链路**不存在**。

目标（对齐 opencode v2）：

1. **模型目录（model catalog）**：单一事实来源，后端持有 `provider/model → 能力/上下文/限长/价格`，
   前端选择器由 `GET /api/models` 动态驱动；支持用户 JSON 覆盖自定义模型（私有/本地模型）。
2. **会话级模型切换**：会话持有当前 model，切换即刻生效并持久化，后续轮次使用新模型；
   多 Agent（supervisor + build/explore/plan）统一走会话 model。
3. **Token/成本统计闭环**：usage 按 opencode 口径归一化（input=非缓存输入、output=可见输出、reasoning 单列、
   cache read/write 单列），落到 assistant 消息 `data.tokens` + `sessions` 聚合列 + **真实 cost**（按目录价格表计算），
   前端会话/监控展示。

## 2. opencode 实现要点（调研结论）

### 2.1 模型配置 / 注册

- `packages/core/src/config.ts:36-38`：顶层 `model` 是 `"provider/model"` 字符串；`config/provider.ts:65-71`
  `ConfigProvider.Info{provider, status, env[], api[], request[], models[]}` 汇总**可用模型**；`Model` 类（L47-63）
  持有 `{family, name, api, capabilities, request}`。`agents` 记录允许 agent 级覆盖 model。
- v2 已移除 `small_model` 概念；价格走各 provider 的 usage 换算（`provider.ts` cost 计算）。
- provider 启动时探测 env/API 可用性 → 不可用的模型不进目录（`status` 标记）。

### 2.2 模型切换

- TUI：`/models` 命令（`dialog-model.tsx`）、`f2` 快捷键、`model.json` 局部状态（`local.tsx`）、
  关键绑定见 `config/keybind.ts:41,119-133`。
- Legacy v1 路径：**模型随每条 prompt 消息携带**，与 `SessionTable.model` 不同则持久化。
- v2 REST：`POST /api/session/{sessionID}/model` → `switchModel` → 事件 `session.next.model.switched`；
  `session-ui` 无模型选择器（在 `packages/app` 的 dialog）。

### 2.3 Token / 成本统计

- **不引入 tiktoken**，全部来自 provider 返回的 usage。
- `packages/llm/src/schema/events.ts:51-74` `LLM.Usage`：
  `inputTokens`（含全部缓存）、`outputTokens`、`nonCachedInputTokens`、`cacheReadInputTokens`、
  `cacheWriteInputTokens`、`reasoningTokens`。
- **会话面向的口径**：`input = nonCachedInputTokens`、`output = outputTokens - reasoningTokens`（"可见输出"）。
- 每条 assistant 消息持久化 `tokens: {input, output, reasoning, cache: {read, write}}` + `cost`。
- 当前 cost 在 provider 端未接入计价（硬编码 0）；前端上下文条（`session-context-breakdown.ts:12`）用 `chars/4` 估算。

## 3. 现状盘点（AgentSuper）

### 已具备（可直接复用）

| 能力 | 位置 |
|---|---|
| `sessions.model`（ModelRef JSON）+ PATCH 可改 | `app/session/db.py:50`、`repository.py:192-200`、`models.py:201-207` |
| `SessionInfo.cost/tokens_input/output/cache_read/cache_write` | `app/session/models.py:35-39` |
| `add_session_usage()`（但无生产调用点） | `app/session/repository.py:338-346` |
| assistant 消息 `data.tokens`（含 reasoning/cache） | `app/api/chatmod/persist.py:234` |
| `ChatRequest.model` → supervisor → 子 Agent 全链路 | `app/models/schemas.py:63`、`endpoints.py:153/301` |
| 统一 usage 结构 `_ZERO_USAGE=input/output/reasoning/cache_read/cache_write` | `app/agent/graphmod/state.py:131` |
| 监控 per-model tokens | `app/monitor.py` → `data/monitor_stats.json`、`GET /api/monitor/stats` |

### 缺失（本次改造范围）

1. **模型目录**：后端没有模型注册表；能力/上下文/限长/价格全部缺失；前端选择器是静态常量。
2. **切换闭环**：`session.model` 建会话后不再同步；无 `model_switched` 事件；前端无 "会话默认模型" 概念。
3. **用量聚合**：`reasoning` 从未累计（RAG 主 Agent `_usage_accum` 里 reasoning 恒 0）；
   `add_session_usage` 未接线路由；`sessions.tokens_*` 恒 0。
4. **成本**：全链路无 cost——主 Agent `_generate` 返回 dict 不含 cost，supervisor 汇总也丢弃。

## 4. 目标设计

### A. 模型目录（ModelCatalog）

新增 `backend/app/models/catalog.py`（与 `schemas.py` 同包）：

```text
ModelEntry = {
  id: "provider/model",       # 主键，同时配 normalization 源
  provider: "openai"|"deepseek"|"ollama"|...,
  family: "qwen2.5"|"deepseek-chat"|...,
  name: "显示名",
  description: "一句话说明",
  capabilities: { tool_use: bool, vision: bool, reasoning: bool },
  context_length: int,                    # 上下文窗口
  limits: { max_output_tokens: int|None },
  cost: {                                  # 每 1M tokens 美元；缺省 0（opencode 式）
    input_per_1m, output_per_1m,
    cache_read_per_1m, cache_write_per_1m,
  },
  default: bool,                          # 前端选择器默认项
}
```

- **内置目录**：内置常量表（覆盖本项目已知模型 —— 注入现有 `SUPPORTED_MODELS` 的全部 11 项 + 价格字段，价格未知记 0）。
- **用户覆盖**：`data/model_catalog.json`（不存在则忽略）。顶层 `{"overrides": {...}, "extra": [...]}`
  按 `id` merge（可改价格/能力，或新增本地/私有模型）。启动时由 `runtime.py` 加载进 `app.state.model_catalog`。
- **解析**：`normalize(model: str|ModelRef|None) -> str`（补 `provider/` 前缀，逻辑复用 `graphmod/core.py` 现有
  `_llm_call` 的 ollama 分支：`ollama/*` → `api_base=None`、`api_key="ollama"`）。
- **计价**：`resolve_cost(model_id, usage) -> float`：
  `input = nonCached = pt - cache_read`（litellm usage 无显式 non-cached 字段时估算），
  `cost = input*in_rate + cache_read*read_rate + cache_write*write_rate + output*out_rate`（单位 1e6 归一）。

**API**：`GET /api/models`（无需鉴权，返回目录快照）——

```json
{ "code": 0, "data": {
    "models": [ ModelEntry... ],
    "default_model": "deepseek/deepseek-v4-flash",
    "small_model": null }
}
```

前端 `frontend/src/api/models.ts` 新增 fetch；`stores/multiAgent.ts` 加载一次驱动选择器。

### B. 会话级模型切换

- **请求时同步**：`POST /api/chat/multi-agent[/stream]` 收到 `body.model` 后，若与 `session.model` 不同，
  在进入 supervisor 前调 `service.update(session.id, model=<ModelRef>)` 持久化，并**并发注入 SSE 事件**
  `model_switched`（`{model: current}`，前端把选择器同步成当前值）。
  —— 与 opencode "切换持久化 + `model.switched` 事件" 对齐；切换动作本身由前端选择器发起，后端只在请求入口兜底同步。
- **supervisor / 子 Agent**：维持现状（payload.model 全链路透传），不改路由逻辑。
- **PATCH `/api/sessions/{id}` 已支持 model**，前端选择器变更直接复用（新增 `updateSessionModel` 便捷调用）。

### C. Token / 成本统计闭环

1. **主 Agent 归一化**（`graphmod/generate.py` `_llm_call` 同步 + 流式累积）：
   - 累计 `reasoning`：litellm usage 的 `completion_tokens_details.reasoning_tokens`（无则 0）。
   - 计算 `cost`（目录价格 → `resolve_cost`），塞进 `_generate` 返回 dict（新增 `"cost"` 键）。
2. **supervisor 汇总**（`supermod/parallel.py` / `core.py` `_merge_plan_build_reply`）：
   `tokens` 合并改为**逐键求和**（现只保留第一份）；`cost` 一并求和，进 reply payload `{"tokens":..., "cost":...}`。
3. **持久化**（`_persist_multi_agent` / `_ensure_child_pair`）：
   - assistant 消息 `data` 增加 `cost` 字段（与 opencode message.cost 对齐）。
   - 主/子会话落库后调用扩展版 `add_session_usage(session_id, input, output, cache_read, cache_write, reasoning, cost)`。
4. **DB 迁移**：`sessions` 增加 `tokens_reasoning INTEGER NOT NULL DEFAULT 0` 列。
   db.py 启动时 `PRAGMA table_info(sessions)` 检查缺列则 `ALTER TABLE ... ADD COLUMN`（`_ensure_column`）。
5. **展示**：
   - `GET /api/monitor/stats` 增加 `total_cost`、`cost_by_model`、`reasoning_tokens_total`（监控页 per-model 卡片补齐）。
   - 前端：会话列表/详情显示 tokens+成本；assistant 消息渲染模型名（已有 `data.model`）+ token 摘要；
     监控页按模型维度列出 input/output/reasoning/cache/成本。

## 5. 数据模型变更

| 变更 | 说明 |
|---|---|
| `sessions.tokens_reasoning`（新增列） | `ALTER TABLE ... ADD COLUMN`，启动时 ensure |
| `add_session_usage(..., cache_read, cache_write, reasoning, cost)` | 扩展签名，兼容旧调用（默认 0） |
| assistant 消息 `data.cost` | 新增字段，断线/重试按 client_msg_id 幂等复用不变 |

## 6. API 变更

- `GET /api/models`（新增）：模型目录（含默认/小模型）。
- `POST /api/chat/multi-agent(/stream)`：新增 SSE 事件 `model_switched`；请求入口同步 `session.model`。
- `GET /api/monitor/stats`：扩展 `total_cost` / `cost_by_model` / `reasoning_tokens_total`。

## 7. 前端变更

- `src/api/models.ts`：`fetchModels()`；`stores/multiAgent.ts` 持有 `models` 与 `smallModel`，
  选择器改用它（`SUPPORTED_MODELS` 降级为离线兜底）。
- 模型选择组件：从接口 options；选择变更 → `updateSessionModel(conversationId, model)`。
- 会话/消息：标题区/消息头部显示模型名；会话详情或列表示 tokens（in/out/reasoning/cache）+ cost。
- 监控页：per-model 卡片补齐 reasoning/成本列。

## 8. 分阶段实施（按序）

1. **P0 数据层**：`tokens_reasoning` 列 + `_ensure_column` + `add_session_usage` 扩展（带 pytest）。
2. **P1 模型目录**：`app/models/catalog.py`（内置表 + 覆盖文件 loader + normalize + resolve_cost）+
   `GET /api/models`（带 pytest）。
3. **P2 用量/成本闭环**：reasoning 累计 + cost 计算 → supervisor 逐键求和 → `_persist_multi_agent` 聚合
   → `add_session_usage` 落库。
4. **P3 切换**：multi-agent 入口同步 session.model + SSE `model_switched`。
5. **P4 前端**：models 接口驱动选择器 + 切换持久化 + 会话/消息/监控展示。
6. **验证**：`pytest tests/ -q`、`npm run check`；杀 python 重启 uvicorn 手动回归。

## 9. 不做（本迭代范围外）

- provider 启动探测自动注册（本地/私服模型靠覆盖文件手动登记）；
- 前端上下文 token 估算条（核心 token 已收敛到 usage，`chars/4` 估算可后续补）；
- opencode 的 `small_model` 自动选用（保留字段/接口为 null，不引入消费逻辑）。