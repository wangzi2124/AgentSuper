# AI 知识库系统 — 问题清单

> 记录时间: 2026-07-30

---

## 🔴 严重安全漏洞

### 1. API Key 明文泄露在仓库中（最严重）
- `backend/app/config.py` 从 `.env` 加载环境变量
- `.env` 包含真实 DeepSeek API Key: `LLM_API_KEY=sk-092d2931acc4464abc99087fc00efe2c`
- **没有 `.gitignore`**，`.env`、`__pycache__`、`.venv` 等都可能被提交

### 2. 无限制的 Shell 命令执行（命令注入）
- `backend/app/tools/filesystem.py:222` — `tool_execute` 使用 `subprocess.run(command, shell=True)`
- `command` 参数直接传递给 shell，权限系统允许工作区内路径自动放行

### 3. SSL 证书验证被禁用
- `backend/app/api/weather.py:43` — `_create_ssl_context()` 中设置了 `check_hostname = False` 和 `verify_mode = CERT_NONE`
- 所有 HTTPS 请求（IP 定位、天气数据）均不验证 SSL 证书，存在 MITM 风险

### 4. 文件上传无大小限制
- `backend/app/api/documents.py` — `upload_document` 未检查文件大小，可被用于填满磁盘

---

## 🟠 架构性问题

### 5. 双 Agent 循环架构 —— 大量重复代码
两套独立 Agent 执行引擎：

| 文件 | 方式 |
|------|------|
| `backend/app/agent/graph.py` | LangGraph 状态机（retrieve → rerank → generate） |
| `backend/app/context/task_runner.py` | 手写 while 循环（双循环模式） |

重复实现：工具调用循环、上下文压缩、工具去重、输出截断、重试逻辑、Token 计数。

### 6. README 路径描述错误
- README 中说 `cd backend`，但项目结构并非如此，新人配置时会困惑

### 7. requirements.txt 格式错误
- 包名前有前导空格，`pip install -r requirements.txt` 会报错

---

## 🟡 代码质量缺陷

### 8. 去重时只发送 tool_start 不发送 tool_end
- `graph.py` 中工具结果缓存命中时只发 `tool_start`，`continue` 跳过导致缺 `tool_end`
- 前端会看到该工具永远显示 "running" 状态
- `task_runner.py` 中有相同的 bug

### 9. 上下文被双重截断
- `chat.py:_truncate_history()` 先截断到 4000 tokens
- `graph.py:truncate_messages()` 再截断到 1,000,000 tokens
- 两个不同阈值和算法，可能导致重要上下文过度丢弃

### 10. `task_runner.py` 调用 `_execute_tool` 缺少 `state` 参数
- 调用 `self.agent._execute_tool(tool_name, args)` 未传 `state`
- 导致事件队列为空，权限审批推送、工具流式输出等功能在 TaskRunner 路径下失效

### 11. PermissionManager 临时授权永不清理
- `backend/app/permission/manager.py` 的 `_temp_approvals: set[str]` 只增不减，运行时无限膨胀

### 12. Shell 子进程 PIPE 可能死锁
- `graph.py:_execute_tool_streaming` 使用 `subprocess.Popen` + `threading.Thread` 读取 stdout/stderr
- 子进程输出大量数据时 PIPE 缓冲区填满可能导致死锁

### 13. 意图检测不支持较长中文人名
- `backend/app/rag/intent.py` — `DIALOGUE_PREFIX` 正则中 `[\u4e00-\u9fff\w]{1,8}` 限制 1-8 字符，较长人名可能超限

### 14. 非流式 Chat 端点的 TaskRunner 未真正使用
- `chat.py` 初始化了 `TaskRunner(agent)`，但 LangGraph 的 `agent.graph.ainvoke()` 已调度整个流程

### 15. Tavily API Key 含引号
- `.env` 中 `TAVILY_API_KEY="your-tavily-api-key"`，解析时值包含双引号字符

### 16. 硬编码的中文 UI 文案
- 所有步骤名称、状态描述、错误信息都硬编码为中文，无法国际化

### 17. Embedding/Reranker 模型路径可能不兼容
- `.env` 设置 HuggingFace 模型名，但下载逻辑通过 ModelScope，路径查找不一定正确

---

## 🔵 建议修复优先级

| 优先级 | 问题 | 建议 |
|--------|------|------|
| P0 | API Key 泄露 | 轮换 Key、添加 `.gitignore`、用环境变量注入 |
| P0 | 命令注入风险 | 限制命令白名单，或移除 `shell=True` |
| P0 | SSL 禁用 | 恢复 SSL 验证 |
| P1 | 重复的 Agent 循环 | 统一为单一架构，删除重复实现 |
| P1 | requirements.txt 空格 | 修复缩进 |
| P2 | Dedup tool_start/tool_end 不匹配 | 缓存命中时也发送 `tool_end` |
| P2 | 双重截断 | 统一截断策略 |
| P2 | README 路径 | 修复目录参考 |
| P3 | PIPE 死锁风险 | 使用 `asyncio.create_subprocess_exec` |
| P3 | 文件上传无限制 | 添加最大文件大小限制 |
| P3 | 权限集内存泄漏 | 添加定期清理或 LRU 缓存 |
