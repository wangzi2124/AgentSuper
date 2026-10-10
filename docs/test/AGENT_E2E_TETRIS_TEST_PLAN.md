# 后端 Agent 全链路长任务测试方案（React 俄罗斯方块）

**版本**: 1.0
**日期**: 2026-10-09
**被测系统**: AgentSuper 后端（FastAPI + LangGraph 多 Agent）
**测试类型**: 端到端集成测试（黑盒，走真实 HTTP + 真实 LLM）

---

## 1. 目的与范围

### 1.1 目的
用一条真实的「长任务 + 大文件写入」请求，验证后端 **Agent 全链路** 是否端到端可用：

1. 请求能被接收、路由、执行并流式回传（SSE 全事件）。
2. 主 Agent（build）能通过工具循环 **真实写盘** 多文件、较大文件的代码。
3. 写入内容 **不被截断 / 不被零进展掩码覆盖**，且语法可校验。
4. 轮次快照与消息落库正确，前端「查看改动 / 撤回」数据齐全。

### 1.2 范围
- **包含**：多 Agent 流式端点 `POST /api/chat/multi-agent/stream`、Supervisor 路由、build Agent、`tool_write_file`/`tool_edit_file`/`tool_apply_patch`/`tool_execute` 工具链、轮次快照、session 落库。
- **不包含**：知识库检索链路（`use_vector_db=false`，本次显式关闭；KB 不参与评判）、语音、图片、登录鉴权、前端 UI。
- **测试产物的代码技术栈**：React（Vite 项目结构，JSX），由 Agent 生成到指定目录。

---

## 2. 被测链路与架构

```
HTTP POST /api/chat/multi-agent/stream
  └─ chatmod.endpoints.chat_multi_agent_stream
       ├─ _resolve_multi_agent_parent  → 建/取 session（绑定 directory）
       ├─ _build_compressed_history     → 装配历史（本次为空）
       ├─ AgentEventCollector + event_queue → SSE
       ├─ AgentBus.send_and_wait(target="supervisor")
       │     └─ supervisor 路由 → build
       │            └─ RAGAgent._generate（LangGraph 工具循环，≤ MAX_TOOL_ROUNDS）
       │                   ├─ _llm_call（litellm → deepseek）
       │                   ├─ _execute_tool → fstools（写盘 + 权限 + 快照 record_write）
       │                   └─ _push_event（agent_step 等）
       ├─ _files_changed（before/after 快照 diff）
       ├─ _persist_multi_agent（写 session_messages / message_parts）
       └─ done 事件（answer / files_changed / agents / plan_path ...）
```

关键落点与验证入口：

| 机制 | 位置 | 验证方式 |
|---|---|---|
| SSE 事件 | `chatmod/endpoints.py` | 驱动捕获全部事件流 |
| 路由 | `agent/supermod/*` | `done.routed_to == "build"` |
| 工具循环 | `agent/graphmod/generate.py` | `agent_step` 事件含 tool_call/tool_end |
| 写盘 | `app/tools/fstools/writer.py` | 磁盘文件实际存在且非空 |
| 轮次快照 | `app/snapshot/` | `done.files_changed` + `/api/chat/multi-agent/diff` |
| 落库 | `app/session/*` | `GET /api/sessions/{id}/messages` |
| 全链路日志 | `app/chainlog`（`data/logs`） | `generate.zero_progress` / `tool_end` 节点 |

---

## 3. 前置条件与环境

| 项 | 要求 |
|---|---|
| Python | `backend/.venv/Scripts/python.exe`（>=3.13，依赖经 uv 同步） |
| 触发方式 | `uvicorn main:app`（本机 8000） |
| LLM | `backend/.env`：`LLM_MODEL=deepseek/deepseek-v4-flash` + 有效 `LLM_API_KEY`/`LLM_API_BASE` |
| 鉴权 | 测试 `.env` 不设 `AUTH_TOKEN_SECRET`（匿名，免 token） |
| 向量库 | 关闭（`use_vector_db=false`）；`ENABLE_RERANKER=false` 避免无关下载 |
| 写入目录 | `C:\tool\tetris-react`（作为会话工作目录 `directory`，免外部路径审批） |
| Node（校验用） | Node v24+（`node --check` / esbuild 解析 JSX） |

启动命令：
```powershell
cd C:\tool\AgentSuper\backend
.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

---

## 4. 测试用例

### TC-01 服务健康
- **步骤**：`GET /health`
- **期望**：`{"status":"ok"}`（初始化中为 `initializing`，需轮询直到 ok）
- **判定**：status == ok

### TC-02 全链路连通（冒烟）
- **输入**：`POST /api/chat/multi-agent/stream`，`message="你好，请用一句话自我介绍"`，`use_vector_db=false`
- **期望**：
  - 收到 `routing` → ... → `done`
  - `done.routed_to == "build"`
  - `done.answer` 非空，且 **不是** 零进展/部分完成失败文案
- **判定**：SSE 正常闭合 + answer 有效

### TC-03 长任务多文件写入（React 俄罗斯方块）—— 核心
- **输入**：`POST /api/chat/multi-agent/stream`
  ```json
  {
    "message": "在 C:\\tool\\tetris-react 目录下，用 React + Vite 写一个可运行的俄罗斯方块（Tetris）游戏。要求：\n1) 使用函数组件 + Hooks；\n2) 拆分为多个文件：package.json、vite.config.js、index.html、src/main.jsx、src/App.jsx、src/game/tetrominoes.js、src/game/useTetris.js、src/game/tetrisReducer.js、src/styles.css；\n3) 实现方块生成/旋转、左右移动、下落、消行、计分、暂停/重开、键盘控制；\n4) 每个文件用 tool_write_file 落盘，不要只把代码贴在回答里；\n5) 完成后给出文件清单与运行方法。",
    "model": "deepseek/deepseek-v4-flash",
    "use_vector_db": false,
    "directory": "C:\\tool\\tetris-react",
    "agent_mode": null,
    "client_msg_id": "<uuid>"
  }
  ```
- **期望**：
  - 完成 ≥5 个文件写入（`done.files_changed` 或磁盘核对）
  - 关键文件齐全：`package.json`、`src/main.jsx`、`src/App.jsx`、含游戏逻辑的 js/jsx
  - `done.answer` 为完成总结（含文件清单/运行方法）
- **判定**：见第 5 节验证

### TC-04 大文件写入未截断
- **观察对象**：单个较大文件（预期 > 4KB，理想 > 8KB）
- **期望**：文件内容 **结构完整**（如 JS 花括号/括号平衡、末尾未被腰斩、非 0 字节、非仅一行占位）
- **判定**：见 5.3 语法/结构校验

### TC-05 产物语法校验
- **`.js` 文件**：`node --check <file>`
- **`.jsx` 文件**：用 esbuild 解析转换（`frontend/node_modules` 内 esbuild）验证 JSX 语法
- **可选加强**：`npm install && npm run build`（需 npm registry 可达；失败不判负，仅记录）
- **判定**：所有生成文件通过语法解析

### TC-06 落库与轮次快照
- **步骤**：
  1. `GET /api/sessions/{conversation_id}/messages` → 最后一条 assistant 消息存在且 `data.files_changed` 非空
  2. `POST /api/chat/multi-agent/diff` `{conversation_id, message_id}` → 返回本轮 diff
- **期望**：均成功返回，diff 含新增文件
- **判定**：两条接口均 200 且数据非空

### TC-07 收尾质量（零进展守卫）
- **期望**：`done.answer` **不含**以下征兆文案：
  - `本次任务已执行部分工作，但最后一轮收尾失败`
  - `零进展`
- **判定**：answer 为正常完成总结；否则记为 **失败并引用 chainlog 的 `cause`**

---

## 5. 验证方法

### 5.1 文件清单核对
递归列出 `C:\tool\tetris-react`，与 TC-03 要求清单比对，统计文件数与总字节数。

### 5.2 快照核对
以 `done.files_changed` 与磁盘实际文件交叉验证（数量、路径）。

### 5.3 语法/结构校验
对每个 `.js`/`.jsx`：
- `.js` → `node --check`
- `.jsx` → esbuild transform（无输出错误即通过）
同时做启发式结构检查：非空、括号/花括号平衡、文件末尾非明显截断。

### 5.4 全链路日志核对
在 `backend/data/logs` 的 chainlog 中检索本次 `conversation_id`，确认：
- `chat.received` → `agent.reply` → `persist.done` → `chat.done` 齐全
- 无 `generate.zero_progress`（若有，记录 cause）

---

## 6. 通过标准

| 用例 | 通过条件 |
|---|---|
| TC-01 | `/health` = ok |
| TC-02 | SSE 闭合，routed_to=build，answer 有效 |
| TC-03 | ≥5 文件，关键文件齐全 |
| TC-04 | 无 0 字节 / 明显截断文件 |
| TC-05 | 所有生成文件语法解析通过 |
| TC-06 | 落库 + diff 接口 200 且非空 |
| TC-07 | 回答非零进展/部分完成失败文案 |

**总判定**：TC-01~TC-07 全部通过 = PASS；任一失败 = FAIL（记录原因与 chainlog 线索）。

---

## 7. 产物与记录

| 产物 | 路径 |
|---|---|
| 本测试方案 | `docs/test/AGENT_E2E_TETRIS_TEST_PLAN.md` |
| 测试驱动脚本 | `backend/scripts/e2e_agent_tetris_test.py` |
| SSE 原始事件日志 | `docs/test/runs/<ts>_events.jsonl` |
| 运行结果 JSON | `docs/test/runs/<ts>_result.json` |
| 生成游戏 | `C:\tool\tetris-react\` |
| 测试报告 | `docs/test/AGENT_E2E_TETRIS_TEST_REPORT.md` |

---

## 8. 风险与说明
- LLM 输出具随机性：文件数量/命名可能偏离要求，以「可运行性与覆盖度」判定，非逐字匹配。
- 若 `MAX_TOOL_ROUNDS=8`（用户 .env 值）导致轮次不足，任务可能未写完 —— 这本身是**被观测的结论**（长任务轮次上限），报告如实记录，不掩盖。
- 首次启动需下载嵌入模型（`BAAI/bge-small-zh-v1.5`，ModelScope），仅影响启动耗时。
