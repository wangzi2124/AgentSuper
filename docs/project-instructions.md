# AgentSuper 项目指令

AgentSuper 是本地多智能体平台：后端 FastAPI + LangGraph，前端 Vue 3 + TypeScript。
本文件是精简规则；更完整的开发手册见仓库根目录 `AGENTS.md`，需要时再读取。

## 常用命令

后端（工作目录 `backend/`）：

- 启动：`.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000`
- 测试：`.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q`

前端（工作目录 `frontend/`）：

- 开发：`npm run dev`
- 类型检查：`npm run typecheck`
- 测试：`npm test`
- 构建：`npm run build`

## 结构

- `backend/app/`：FastAPI 路由、会话、依赖装配
- `backend/graph/`：LangGraph 节点、路由与状态
- `backend/harness/`：工具协议、压缩、模型切换、预算与错误治理
- `backend/prompts/`：提示词组装与项目指令解析
- `backend/models/`：模型运行时与目录
- `frontend/src/views/`、`frontend/src/v2/`：页面与 v2 会话运行时
- `frontend/src/components/`：可复用组件

## 约定

- Python 3.13，始终用 `backend\.venv\Scripts\python.exe`，不要用系统 Python。
- 新增配置项必须同步 `backend/config.example.json` 与 `tests/unit/test_config_example.py`。
- 新增 API：后端 `backend/app/api/` 加 router 并在 `app/main.py` 挂载；前端 `frontend/src/api/` 加 client。
- 新增前端事件：先加 `frontend/src/harness/events.ts` 类型 + adapter/reducer + 单测。
- 改聊天逻辑时确认移动端复用 `V2ChatView` 不被破坏。
- 提示词放 `backend/prompts/`，不要把大段规则塞进 Python。

## 注意

- 不要引用已删除的 `backend/scripts/`、旧 `backend/app/agent/**`、`app/context/**`。
- 后端启动约 30–40 秒；不要几秒无响应就判定失败。
- 改动后运行相关测试与 `npm run typecheck`，不要伪报通过。
- 不读取、不提交密钥；`.env*`、`*.db`、`config.json` 等敏感文件按权限规则处理。
