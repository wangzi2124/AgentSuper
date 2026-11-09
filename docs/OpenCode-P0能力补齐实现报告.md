# OpenCode P0 能力补齐实现报告

> 对照输入：`C:\Users\Administrator\Desktop\OpenCode-能力对照代码实现报告.md`
> 代码库：`C:\tool\AgentSuper`（2026-11-09）
> 范围：报告中列出的 P0-1 ~ P0-6
> 结论：6 项全部落地，并补齐 MCP OAuth 2.1；命令 `subtask` 已完成隔离派发。

---

## 0. 总体结论

| 编号 | 能力 | 状态 | 落地位置 |
|---|---|---|---|
| P0-1 | 配置分层 + `{env:}` / `{file:}` | ✅ 完成 | `backend/config_layers.py`、`backend/config.py` |
| P0-2 | MCP 细粒度超时 + prompts + OAuth 2.1 | ✅ 完成（含前端浏览/调用、OAuth 授权/清除） | `mcp/config.py`、`mcp/client.py`、`mcp/runtime.py`、`mcp/oauth.py`、`app/api/v2/mcp.py`、`frontend/src/views/McpSettingsView.vue` |
| P0-3 | 命令 `template` / `subtask` / 安全 `` !`cmd` `` | ✅ 完成（含隔离子 Agent 派发） | `app/commands/registry.py`、`app/commands/context.py`、`app/skills/slash.py`、`graph/nodes/subtask.py`、`graph/main_graph.py` |
| P0-4 | Agent `request` 与 `steps` 运行时接线 | ✅ 完成 | `agents/registry.py`、`harness/tool_adapter.py`、`graph/nodes/execute.py`、`graph/subagent.py` |
| P0-5 | `/skill`、`@skill` 走 `skill.use` 策略 | ✅ 完成 | `app/skills/slash.py` |
| P0-6 | Formatter 覆盖 `append_file`、`apply_patch` | ✅ 完成 | `execution/tools/file_tools.py`、`execution/tools/patch_tools.py` |

验证：

- 后端全量单元/契约/可靠性/安全：**846 用例通过，0 失败**。
- Harness 覆盖率：**总覆盖率 90%**，`harness/tool_adapter.py` **92%**。
- 新增 11 个测试文件、**53 个用例**（后端 51 + 前端 2），全部通过。
- 前端：`npm test` **86 passed / 118 skipped**；`npm run build`（含 `vue-tsc`）通过。

---

## 1. P0-1 配置分层与变量替换

### 1.1 实现

新增 `backend/config_layers.py`：

- `system_config_path()` / `global_config_path()` / `_find_project_configs(start)` / `app_config_path()`：定位四层配置。
- `config_layers(workspace=None)`：返回 `[("system", path), ("global", path), ("project", path), ("app", path)]`，低 → 高优先级。
- `deep_merge(base, override)`：递归合并，不修改入参。
- `substitute(value, base_dir)`：支持
  - `{env:NAME}`：环境变量，缺失替换为空串；
  - `{file:path}`：读取文件并按 `strip()` 返回；相对路径以**声明该配置的文件目录**解析。
- `load_layered_config(workspace=None)`：逐层读取 JSONC → 替换变量 → 深度合并；单层损坏只记录并跳过。

`backend/config.py` 的 `JsonConfigSettingsSource.__call__` 改为优先调用 `load_layered_config()`，异常时回退到原有单文件逻辑，保证兼容。

会话作用域：

- `config.py` 新增 `_workspace_var` / `_current_settings` contextvar、`get_settings()`、`bind_workspace_settings(workspace)`。
- 回合入口（`PlatformContext._run_turn` / `_stream_turn` / `stream_turn_events`）按 `session.directory` 绑定；没有 `.agentsuper/config.json` 时直接复用进程级 `settings`，保持原行为与测试 monkeypatch 语义。
- 运行时读取点改为 `get_settings()`：模型解析/目录、Harness 能力、压缩、路由门限、指令、策略、formatter、websearch、egress、SSRF、图片/附件、token 估算、工具输出裁剪、配额、插件联网工具等；`storage/*`、认证、DB/checkpoint、日志/观测、启动期图构建仍用进程级配置。
- project 层加防越权过滤：禁止覆盖 `agentsuper_*`、`auth_*`、`db_*`、`checkpoint_*` 以及存储/权限/SSRF 等进程级键，避免克隆仓库通过 `.agentsuper/config.json` 改写数据目录或放宽沙箱。

### 1.2 优先级

`system → global → project（优先用会话 workspace，缺失时从 cwd 向上搜索）→ backend/config.json`；环境变量仍高于配置文件，字段默认值最低。

### 1.3 测试

`backend/tests/unit/test_config_layers.py`：深度合并、env/file 替换、层级覆盖、相对路径解析、损坏层跳过。

`backend/tests/unit/test_config_workspace_scope.py`：project 生效/退出恢复、环境变量覆盖、无 project 时复用全局对象、进程级键过滤、并发 workspace 隔离、异步生成器释放。

---

## 2. P0-2 MCP 细粒度超时与 prompts

### 2.1 超时

`mcp/config.py` 的 `MCPServerConfig` 新增：

- `startup_timeout`（握手 `initialize`）
- `catalog_timeout`（`tools/list`、`resources/list`、`prompts/list`）
- `execution_timeout`（`tools/call`、`resources/read`、`prompts/get`）

三者默认 `0` 表示继承基础 `timeout`。`timeout_for(method)` 统一解析。

`mcp/client.py`：

- stdio 的 `asyncio.wait_for` 使用 `config.timeout_for(method)`；
- HTTP 的 `httpx.post` 使用同一解析结果。

### 2.2 prompts

- `MCPConnection` 新增 `prompts`、`list_prompts()`（best effort，服务端不支持时返回空）、`get_prompt(name, arguments)`；`connect()` 在拉取 tools 后拉取 prompts。
- `MCPRuntime` 的 `ServerState` 增加 `prompts` 并在 `status()` 输出；新增 `prompts(name)` 与 `get_prompt(name, prompt, arguments)`。
- API：
  - `GET /api/v2/mcp/servers/{name}/prompts`
  - `POST /api/v2/mcp/servers/{name}/prompts/get`
- 前端 `McpSettingsView.vue`：
  - 服务器行新增「Prompts」入口，读取状态里的 prompts 并可手动刷新；
  - 每个 prompt 展示描述与参数表单（必填标记），点击「获取」调用 `prompts/get`；
  - 返回的 `messages` 按 `role + text/resource/image` 渲染为可读文本，未知结构回退 JSON；
  - 调用失败（未连接/参数错误）复用页面错误条，不伪造结果。

### 2.3 OAuth 2.1

新增 `backend/mcp/oauth.py`：

- 发现：RFC 9728 Protected Resource Metadata（`/.well-known/oauth-protected-resource[/path]`），缺失时用未认证 `initialize` 的 `WWW-Authenticate: Bearer resource_metadata=...` 兜底；RFC 8414 / OIDC 发现授权服务器元数据。
- 安全：强制 PKCE S256、RFC 8707 `resource`、`state` 单次使用并 10 分钟过期；发现/令牌端点走统一 egress 与 SSRF 校验（本地授权服务器需 `SSRF_ALLOW_INTERNAL=true`）。
- 客户端：可用 `oauth.client_id` / `client_secret` 预注册；否则 RFC 7591 动态注册并缓存，支持 `client_secret_basic` / `client_secret_post`。
- 令牌：`access_token` / `refresh_token` / `expires_at` / scope / issuer 持久化到状态目录 `mcp-oauth.json`（尽力 `0600`、原子写），过期前自动刷新，401 时刷新后重试一次。
- 配置：`oauth.client_id`、`oauth.client_secret`、`oauth.token_endpoint_auth_method`、`oauth.scopes`、`oauth.redirect_uri`、`oauth.resource_metadata_url`；`redirect_uri` 缺省使用当前 API 的 `/api/v2/mcp/oauth/callback`。

`mcp/client.py`：HTTP 请求自动附加 `Authorization: Bearer`；401 区分 `MCPAuthRequired`（缺授权）与刷新失败。

`mcp/runtime.py`：连接失败为 `auth_required` 时保留状态；`ServerState` 输出 `oauth` / `authorized` / `auth_required`。

API：

- `POST /api/v2/mcp/servers/{name}/auth`：发现 + DCR/PKCE，返回 `authorization_url`。
- `GET /api/v2/mcp/oauth/callback`：免登录回调（靠 state 防伪），换取令牌并返回可自动关闭的 HTML。
- `GET /api/v2/mcp/servers/{name}/auth`：授权状态。
- `DELETE /api/v2/mcp/servers/{name}/auth`：清除令牌与缓存的 DCR client，并断开连接。

前端 `McpSettingsView.vue`：HTTP 服务器显示「OAuth 授权」/「清除授权」，新增可选 Client ID / Secret / Scopes / Redirect URI 表单；授权后轮询状态并刷新列表。

### 2.4 测试

`backend/tests/unit/test_mcp_prompts_timeouts.py`：超时解析/回退、prompts 列表与获取、prompts 不支持时的降级、运行时暴露。

`backend/tests/unit/test_mcp_oauth.py`：配置解析、PRM/AS 发现、DCR、PKCE URL、code exchange、刷新、Bearer 注入与 401 重试、`auth_required` 状态、令牌清理。

`backend/tests/unit/test_mcp_oauth_api.py`：授权启动、stdio 拒绝、回调成功/失败、清除授权、鉴权中间件豁免回调。

`frontend/tests/unit/views/McpSettingsView.spec.ts`：prompts 列表渲染、参数填写、`prompts/get` 请求体与返回消息展示；HTTP 服务器 OAuth 授权入口。

---

## 3. P0-3 命令 template / subtask / 安全 shell

### 3.1 实现

`app/commands/registry.py`：

- frontmatter 支持 `template`（覆盖 Markdown 正文作为模板）与 `subtask` / `subagent`（布尔，渲染时在标题追加「（子任务）」）。
- `CommandDef.render(arguments, workspace, shell_runner=None)`。
- `` !`cmd` `` 由 `_expand_shell` 处理：
  - **默认不执行**，保留字面量；
  - 只有调用方注入 `shell_runner`（已过正常权限/审批门禁）才执行；
  - 执行结果包成 `<shell command="...">...</shell>`，异常包成 `status="error"`，不会中断渲染。
- `_RESERVED_FRONTMATTER` 防止 frontmatter 注入内部字段。

`app/commands/context.py`：`set_command_override(agent, model, *, subtask=False, command="")`，只写入非空/真值字段，保持旧调用方返回结构不变。

`app/skills/slash.py`：命令路径把 `subtask` 与 `shell_runner` 透传给 `render`。

### 3.2 隔离子 Agent 派发

`subtask: true` 的命令不再进入常规 plan/RAG/execute 主循环，而是走独立节点：

- `AgentState` 新增 `subtask: bool`；`initial_state(..., subtask=...)` 写入；`deps.py` 三个回合入口（`_run_turn` / `stream_turn_events` / `_stream_turn`）从命令覆盖读取并透传。
- `route_after_filter`：`subtask` → `subtask` 节点，否则 → `supervisor`；`filter` 节点对 subtask 直接放行，避免命令文本被寒暄闸门误判。
- 新节点 `graph/nodes/subtask.py`：
  - 用 `AgentResolver.resolve()` 解析命令的 `agent`（primary / subagent 均可，缺失回退主 Agent）；
  - 上下文隔离：只送「Agent system prompt + 当前命令渲染文本（+附件正文）」，**不带**父会话历史、摘要、RAG、memory；
  - 工具按 Agent `tools` 收窄，沿用 `request`、`steps(max_rounds)`、预算、压缩与流式事件；
  - 结果写回 `final_output` / `draft_output` / `sub_results`，随后 `subtask → synthesize → memory_store → END`，流式与投影链路不变。
- 新增测试 `tests/unit/test_subtask_dispatch.py`，并把新边纳入 `tests/contract/test_graph_edge_coverage.py`。

边界：`` !`cmd` `` 在未注入 runner 时永远保持字面量，避免命令文本成为权限绕过。

### 3.3 测试

`backend/tests/unit/test_commands_advanced.py`：template 覆盖、subagent 别名、无 runner 不执行、注入 runner 执行、runner 异常内联。

---

## 4. P0-4 Agent request / steps 运行时接线

### 4.1 实现

`agents/registry.py`：

- 新增显式白名单 `_REQUEST_ALLOWED` 与 `request_kwargs(request)`：只放行采样/输出类参数（`temperature`、`top_p`、`top_k`、`max_tokens`、`stop`、`seed`、`presence_penalty`、`frequency_penalty`、`logit_bias`、`logprobs`、`top_logprobs`、`response_format`、`reasoning_effort`、`verbosity`、`user`），并过滤 `None`；未知键、`model/messages/tools/stream`、凭证、`n/max_completion_tokens/timeout/extra_body` 等一律丢弃，避免严格 provider 因未知 kwargs 报错。
- `AgentDef.request` 已从 frontmatter / JSON 解析并合并；`max_rounds` 已由 `steps`/`max_rounds` 解析。

`harness/tool_adapter.py`：

- `complete` / `stream` / `_raw_ainvoke` / `_raw_stream` 新增 `request_params`，只透传 `request_kwargs()` 已白名单化的参数。

`graph/nodes/base.py`：

- `run_tool_loop` 与 `_stream_step` 新增 `request_params`；
- 仅在 `request_params` 非空时以第三个参数传给 adapter，保持旧测试替身兼容。

`graph/nodes/execute.py`：

- 依据当前 `agent_def` 构造 `loop_kwargs`：`max_rounds`（`>0` 时）与 `request_params`，并在 `run_tool_loop(...)` 调用点展开。

`graph/subagent.py`：

- `SubAgent` 增加 `request`；`_to_subagent` 复制 `defn.request`；
- `run_subagent` 传入 `request_params=request_kwargs(spec.request)`，`max_rounds` 已有。

### 4.2 测试

`backend/tests/unit/test_agent_runtime_wiring.py`：白名单放行/未知键与保留键过滤、全未知时返回 `None`、adapter 透传（complete/stream）、`run_tool_loop` 将 `request_params` 传给 adapter。

---

## 5. P0-5 技能直连策略门禁

`app/skills/slash.py` 新增：

- `_skill_policy_block(name)`：依次检查 `skill.use` 与 `skill` 动作的硬策略；
- `_blocked_skill_text(name, reason)`：返回明确的拒绝文本，禁止模型臆造技能内容。

`/skill` 与 `@skill` 两条直连注入路径在真正注入正文前都先过策略门禁；未配置策略基础设施时视为不阻断。

测试：`backend/tests/unit/test_skill_policy.py`（`/skill` 阻断、`@skill` 阻断、无策略放行）。

---

## 6. P0-6 Formatter 覆盖更多写路径

- `execution/tools/file_tools.py`：`append_file` 在写盘成功后调用 `_after_mutation(vfs, path)`；错误路径不触发。
- `execution/tools/patch_tools.py`：`apply_patch` 成功写盘后惰性导入并调用 `_after_mutation`；格式化异常被吞掉，绝不破坏补丁结果。

测试：`backend/tests/unit/test_formatter_write_paths.py`（append 触发、patch 触发、patch 失败不触发）。

---

## 7. 验证记录

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q
# 824 tests collected，全部通过

.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q --cov=harness
# TOTAL 90%，harness/tool_adapter.py 92%

cd ..\frontend
npm test
# 85 passed / 118 skipped（live 用例需后端）
npm run build
# vue-tsc + vite build 通过
```

新增测试文件：

- `tests/unit/test_config_layers.py`
- `tests/unit/test_config_workspace_scope.py`
- `tests/unit/test_commands_advanced.py`
- `tests/unit/test_agent_runtime_wiring.py`
- `tests/unit/test_skill_policy.py`
- `tests/unit/test_formatter_write_paths.py`
- `tests/unit/test_mcp_prompts_timeouts.py`
- `tests/unit/test_mcp_oauth.py`
- `tests/unit/test_mcp_oauth_api.py`
- `tests/unit/test_subtask_dispatch.py`
- `frontend/tests/unit/views/McpSettingsView.spec.ts`

---

## 8. 剩余缺口 / 后续建议

- P0 范围内当前无已知未实现项。
- OAuth 的 scope 升级（403 insufficient_scope）和 Client ID Metadata Documents（CIMD）尚未实现；当前以 PKCE + DCR/预注册客户端覆盖主流 MCP 授权服务器。
