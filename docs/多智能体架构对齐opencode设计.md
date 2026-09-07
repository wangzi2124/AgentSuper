# 多智能体架构对齐 opencode 设计 — 执行计划

## 背景

当前多智能体系统有 4 个 agent：`supervisor`（路由）、`rag`（知识库）、`web_search`（网络搜索）、`code`（代码分析）。
opencode 有 7 个内置 agent：`build`（默认）、`plan`（规划）、`general`（通用子 agent）、`explore`（探索）、`compaction`、`title`、`summary`。

目标：对齐 opencode 的 agent 架构，补齐缺失的 agent 和能力。

---

## 差距分析

| opencode agent | 当前状态 | 需要做的事 |
|---|---|---|
| `build`（默认） | `rag` + `code` 已覆盖 | 无需改动 |
| `plan`（规划模式） | **缺失** | 新增 `PlanAgent`，禁用编辑工具，只输出结构化计划 |
| `general`（通用子 agent） | `task_bus` 已注入但未注册工具 | 在 system prompt 注册 `tool_task`，让主 agent 可委派 |
| `explore`（只读探索） | **缺失** | 新增 `ExploreAgent`，只读工具（read/glob/grep） |
| `compaction` | 已实现 | 无需改动 |
| `title` | 规则截取（前 20 字符） | 改为 LLM 生成 |
| `summary` | 无 | 可选：完成后生成摘要 |

---

## 执行步骤（已完成 ✅）

### Phase 1：Explore Agent（只读探索子 agent）✅

**文件变更：**
- 新建 `backend/app/agent/explore_agent.py`
- 修改 `backend/app/agent/stream_events.py` — 添加 `explore` 的 label/avatar
- 修改 `backend/app/runtime.py` — 注册 ExploreAgent
- 修改 `backend/app/agent/supermod/base.py` — `ROUTABLE_AGENTS` 添加 `"explore"`

**ExploreAgent 设计：**
- `agent_id = "explore"`
- 动作：`chat`（搜索+回答）
- 工具白名单：复用 `sub_tools.tool_loop_chat`（read/glob/grep/ls 等只读工具）
- system prompt：快速代码探索，只读，不修改文件

### Phase 2：Plan Agent（规划模式）✅

**文件变更：**
- 新建 `backend/app/agent/plan_agent.py`
- 修改 `backend/app/agent/stream_events.py` — 添加 `plan` 的 label/avatar
- 修改 `backend/app/runtime.py` — 注册 PlanAgent
- 修改 `backend/app/agent/supermod/base.py` — `ROUTABLE_AGENTS` 添加 `"plan"`

**PlanAgent 设计：**
- `agent_id = "plan"`
- 动作：`chat`（生成结构化计划）
- 不执行任何工具，纯 LLM 输出
- system prompt：分析用户需求，输出分步骤实施计划（Markdown 格式）
- 输出格式：`## 实施计划` + 步骤列表 + 每步的文件/工具/预期结果

### Phase 3：注册 tool_task（主 agent 委派子 agent）✅

**文件变更：**
- 修改 `backend/app/agent/graphmod/constants.py` — `_TASK_TOOL_SUBAGENTS` 添加 `"explore"`, `"plan"`
- 修改 `backend/app/agent/tools.py` — 更新 system prompt 添加 explore/plan 工具说明

**tool_task 设计：**
- 参数：`prompt`（任务描述）、`subagent_type`（`web_search`/`code`/`explore`/`plan`）
- 执行：通过 `agent.task_bus` 发送 `AgentMessage` 到目标子 agent
- 返回：子 agent 的回答文本
- 限制：`subagent_depth` 检查（默认 1，子 agent 不能再嵌套）

### Phase 4：Title 生成改用 LLM ✅

**文件变更：**
- 修改 `backend/app/api/chatmod/helpers.py` — `_generate_title` 改用 LLM

**设计：**
- 第一条用户消息发送后，异步调用 LLM 生成标题
- prompt：`用 10 个字以内概括对话主题，只输出标题，不要引号或标点。`
- temperature=0.5，max_tokens=30
- 失败回退到当前的规则截取

### Phase 5：前端 Agent 模式选择器 ✅

**文件变更：**
- 修改 `frontend/src/views/MultiAgentView.vue` — 添加模式切换 UI
- 修改 `frontend/src/stores/multiAgent.ts` — 添加 `agentMode` 状态
- 修改 `frontend/src/styles/chat/multiAgentView.css` — 添加模式选择器样式
- 修改 `backend/app/models/schemas.py` — ChatRequest 添加 `agent_mode` 字段
- 修改 `backend/app/api/chatmod/endpoints.py` — 支持 `agent_mode` 直接路由

**设计：**
- 聊天输入框上方添加模式切换按钮组：`默认` / `规划` / `探索`
- `默认` = 当前行为（supervisor 路由）
- `规划` = 直接发给 plan agent
- `探索` = 直接发给 explore agent
- 模式选择影响请求的 `target` 字段

---

## 文件清单

| 操作 | 文件路径 |
|---|---|
| 新建 | `backend/app/agent/explore_agent.py` |
| 新建 | `backend/app/agent/plan_agent.py` |
| 修改 | `backend/app/agent/__init__.py` |
| 修改 | `backend/app/agent/supermod/base.py` |
| 修改 | `backend/app/agent/supermod/constants.py` |
| 修改 | `backend/app/agent/stream_events.py` |
| 修改 | `backend/app/agent/graphmod/constants.py` |
| 修改 | `backend/app/agent/tools.py` |
| 修改 | `backend/app/runtime.py` |
| 修改 | `backend/app/models/schemas.py` |
| 修改 | `backend/app/api/chatmod/helpers.py` |
| 修改 | `backend/app/api/chatmod/endpoints.py` |
| 修改 | `frontend/src/views/MultiAgentView.vue` |
| 修改 | `frontend/src/stores/multiAgent.ts` |
| 修改 | `frontend/src/styles/chat/multiAgentView.css` |

---

## 验证方式

1. 启动后端 `uvicorn main:app --host 0.0.0.0 --port 8000`
2. 检查 `agent_bus.list_agents()` 包含所有 6 个 agent：`rag`, `web_search`, `code`, `explore`, `plan`, `supervisor`
3. 发送消息验证 supervisor 能路由到 explore/plan
4. 验证 tool_task 可委派子 agent
5. 验证 title 由 LLM 生成
6. 验证前端模式选择器正常工作
