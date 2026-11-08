# opencode 能力差距矩阵（严格实现 · 允许重构）

> 生成日期：2026-11-08
> 参考基线：`C:\Users\MAC\Desktop\opencode-docs-full.md`（opencode.ai 官方文档全集，抓取 2026-11-07，68 页）
> 代码基线：当前工作区（行号为 2026-11-08 核实值）
> 用途：回答「对照 opencode，系统还缺什么、缺到什么程度、下一步怎么严格补齐」。
> 范围：本项目是 Web 端 RAG + Agent 系统。CLI / TUI / Desktop / Console / ACP / 插件 GUI 构建体系默认**不在范围**（见 §6 G-20），除非单独立项。

## 实现进度（2026-11-08）

| 项 | 状态 | 落点 |
| --- | --- | --- |
| G-01 项目指令 | 已落地 | `prompts/instructions.py`、`prompts/assembly.py`、`api/instructions.py`、Agent 配置页 |
| G-02 Commands | 已落地（shell 插值按安全差异不执行） | `app/commands/registry.py`、`app/skills/slash.py`、`api/commands.py`、Composer 斜杠面板 |
| G-03 Formatters | 已落地 | `execution/formatters/`、`file_tools.py`、事件 `formatter_applied/failed` |
| G-04 MCP | 已落地（OAuth 明确 501） | `mcp/{config,client,runtime}.py`、`api/v2/mcp.py`、`/mcp` 页；真实 stdio 集成测试 `tests/integration/test_mcp_stdio_real.py` |
| G-05 Agents | 已落地 | `agents/{registry,resolver,roles}.py`、内置 build/plan/general/explore/compaction + role_*、CRUD、request 跨源合并、会话 agent 影响主循环 |
| G-06 Policies | 已落地 | `policy/engine.py`、`execution/{actions,access}.py`、`api/v2/policies.py`、provider.use 运行时拦截 |
| G-07 References | 已落地 | `references/`、`api/v2/references.py`、配置页、Composer `@ref:` 选择器 |
| G-08 Providers/Variants | 部分 | `models/variants.py`、本地发现、Provider package/headers/body/timeout、variant 编辑；聊天 `#variant` 选择器未做 |
| G-09 Plugins | 部分 | manifest/options/hooks（`extensions/hooks.py`）、unload、`/plugins` 导航；JS/TS 明确不支持（ADR-0003） |
| G-10 权限 UI | 部分 | 动作模型/工具硬拦/per-agent 规则、规则编辑器、explain API；`always` 仍保留差异 |
| G-11 Warming | 已落地 | `harness/warming.py`、lifespan 调度、活动重置 |
| G-12 Network | 已落地 | `net/egress.py`、Web/MCP 共用代理/CA/超时、`/api/v2/network` |
| G-13 Websearch | 已落地 | `execution/tools/websearch_policy.py`、provider/随机/429 冷却、`/api/v2/websearch` |
| G-14 Themes | 已落地 | schema/应用/回退/对比度检查、UserMenu 导入、localStorage 持久化与启动恢复 |
| G-15 工具面 | 部分 | `apply_patch`、`ask_question`；browser/execute/session/worktree 见 ADR-0002 |
| G-16 附件 URI | 部分 | `file://#Lx-Ly`、`image_max_bytes`、`image_max_dimension`、read/list API；统一契约仍未全接 |
| G-17 Skills | 已落地（基础） | 全局/项目工作区发现、`@skill-id`、HTTP catalog 安装 + Skills 页入口 |
| G-18 配置 API/UI | 已落地 | `api/v2/config.py` schema/PATCH/持久化/多层 rollback、配置页 |
| G-19 OpenAI API | 部分 | `/v1/chat/completions`、`/v1/models`、会话内 model 切换；SDK 生成未做 |
| G-21 Compaction 增强 | 保留 | 主路径不变；background/native 约束见 ADR-0003 |

---

## 0. 纪律：什么叫「严格实现」，什么叫「打补丁」

### 0.1 严格实现的判定

一个能力只有同时具备以下六面，才算完成：

| 面 | 要求 |
| --- | --- |
| 配置 | 进入 `backend/config.py` + `config.example.json` + 完整性测试；默认值、优先级、关闭开关明确 |
| 运行时 | 落在正确抽象层（registry / resolver / adapter / gate），不是某个节点或某个 API 里的特判 |
| API | 有稳定的内部契约与错误码；不依赖前端拼装 |
| UI | 有真实的用户入口；服务端是唯一真相源 |
| 测试 | 单元 + 契约/集成；失败路径与安全边界必须覆盖 |
| 文档 | 更新本文件、`AGENTS.md`；新子系统补 `docs/adr/` 或设计文档 |

### 0.2 明确禁止的补丁形态

| 补丁形态 | 为什么禁止 | 必须改成 |
| --- | --- | --- |
| 在生产路径保留 in-memory/假实现（如当前 MCP stub） | 看似可用，实际无协议能力，后续接手必踩坑 | 真实 transport；假实现只允许存在于测试 fixture |
| 在节点/工具里写 `if name == "xxx"` 特判 | 能力不可配置、不可扩展、不可测试 | 声明式 spec + registry/resolver |
| 把外部内容直接字符串拼进 prompt | 顺序、缓存、预算、权限全部失控 | 走 instruction/context 组装管线 |
| UI 自行维护状态或在前端做权限判断 | 服务端不再是真相源 | 服务端契约 + 前端只渲染 |
| 新增第二套压缩 / tokenizer / 权限 / 工具循环 | 与既有 canonical 实现漂移 | 复用 `harness/compression.py`、`cognition/context_meter.py`、`execution/permission.py`、`graph/nodes/base.py` |
| 用空实现或静默 fallback 冒充成功 | 故障不可观测 | 返回类型化错误 + 事件；降级必须显式 |
| 硬编码厂商 / Agent / 工具 / Provider 名单 | 新增一个就要改代码 | 目录/配置驱动 |
| 绕过 `ToolRegistry` / `PermissionGate` / `AccessController` 直接执行 | 审计与安全失效 | 所有动作统一入口 |

### 0.3 本次允许的重构（在实现对应能力时执行）

重构不是额外任务，而是「不打补丁」的前提。允许并鼓励：

1. `backend/prompts/assembly.py`：从「静态拼字符串」重构为「稳定头 + 有序上下文段管线」。
2. `backend/models/runtime.py` / `catalog.py`：拆分「声明式 provider 目录」与「运行时 adapter」，去掉厂商分支堆积。
3. `backend/execution/permission.py`：升级为 `AccessController = PermissionGate + PolicyGate`，统一 action/resource 求值。
4. `backend/execution/tools/file_tools.py`：抽出 `FileMutationService`，为 formatter / snapshot / 事件提供统一后置钩子。
5. `backend/graph/subagent.py`：从静态 `SUBAGENTS` 字典改为消费 `AgentRegistry`。
6. `backend/app/skills/slash.py`：删除技能/插件二分派发，改为 `CommandRegistry` 统一派发（旧路径删除，不保留影子实现）。
7. `backend/extensions/plugins.py`：拆成 `PluginLoader`（发现/装载）+ `PluginHost`（生命周期/注册/卸载）。
8. `backend/app/api/v2/session.py`：把 revert/snapshot/compact 的编排抽到 service，路由只做校验与转发。
9. 前端 `stores/*` + `views/*`：新增的 Agent/Command/Provider/MCP/Policy 状态统一走 API，不在 store 内写业务规则。

**重构底线**：不得改变既有外部契约（SSE 事件字段、`turn_id + seq`、v2 路由、`session.db` 真相源），不得绕过投影/压缩/权限的 canonical 路径。

---

## 1. 总览矩阵

状态：`已实现` / `部分` / `缺失`；优先级：P0 > P1 > P2。

| ID | 能力 | 现状 | 优先级 | 结论 |
| --- | --- | --- | --- | --- |
| G-01 | 项目指令（AGENTS.md / instructions） | 缺失 | P0 | 产品不会读取项目级 `AGENTS.md`，只有静态提示词 |
| G-02 | 自定义 Commands | 部分 | P0 | 只有 `/技能名`、`/插件名`，无命令注册表 |
| G-03 | Formatters | 缺失 | P0 | `write/edit` 后无自动格式化 |
| G-04 | MCP 真实接入 | 部分（占位） | P0 | `backend/mcp/adapter.py` 是内存容器，无 transport/API/UI |
| G-05 | 用户自定义 Agents | 部分 | P0 | 固定 3 个子 Agent；无定义文件、模式、per-agent 配置、CRUD |
| G-06 | Policies 硬策略层 | 缺失 | P1 | 只有权限规则；无 `provider.use/tool.use/mcp.use/...` 策略 |
| G-07 | References（@ 外部目录 / Git） | 缺失 | P1 | 只有附件；无命名引用、远程仓库缓存 |
| G-08 | Provider 矩阵与模型 Variants | 部分 | P1 | 仅 `openai` + `ollama`；无变体/别名/超时/编码/云厂商 |
| G-09 | 插件生命周期与 JS 插件 | 部分 | P1 | 仅 Python 函数；无 hook、无 manifest、无 JS/TS |
| G-10 | 权限 UI / per-agent 规则 / 动作覆盖 | 部分 | P1 | 引擎存在，但动作覆盖与配置入口不全 |
| G-11 | Warming（prompt cache 保温） | 部分 | P2 | 只有 Ollama 预热，无周期保温 |
| G-12 | Network（代理 / 自定义 CA） | 部分 | P2 | 只有 LiteLLM proxy URL |
| G-13 | Web search 选择/冷却/权限 | 部分 | P2 | 搜索可用，缺 provider 策略与 UI |
| G-14 | 自定义 Themes | 部分 | P2 | 只有深浅色 + 8 背景，无主题文件 |
| G-15 | 工具面补齐 | 部分 | P2 | 缺 patch/question/session/worktree/browser/execute/image |
| G-16 | 附件 URI / 行范围引用 | 部分 | P2 | 附件可用，缺 URI 契约与行范围 |
| G-17 | Skills 远程源/发现/权限 | 部分 | P2 | 本地技能可用，缺 catalog 与多源发现 |
| G-18 | 通用配置 API / UI | 部分 | P2 | 只有摘要配置接口，无通用设置页 |
| G-19 | OpenAI 兼容 API / SDK | 缺失 | 可选 | 如需对外提供 API 再做；不是内部必需 |
| G-20 | CLI / TUI / Desktop / Console / ACP | 不适用 | — | Web 产品，明确不做 |
| G-21 | Compaction 增强（background / native） | 部分 | P2 | 自动/手动压缩已强；缺后台摘要与 provider 原生压缩 |

已对齐（不计入缺口）：Compaction 主路径、Snapshots、Attachments、Permissions 核心、Skills 本地闭环、Tools 注册表、模型目录/Ollama 适配、Web search 核心、RAG/Memory/Budget。

---

## 2. P0：先补，否则「像 opencode」只是表面

### G-01 项目指令（Instructions / AGENTS.md）

**opencode 语义**
- 全局指令文件 + 从工作区向 home 逐级向上的 `AGENTS.md`；嵌套 `AGENTS.md` 在 Agent 读取文件/列目录时按需加载。
- 配置 `instructions` 支持文件、glob、`~/`、绝对路径、HTTP(S) URL。
- 注入顺序：系统提示 → Code Mode 指引 → MCP/reference/skill 指引 → `AGENTS.md` + 配置指令 → 日期/环境 → 会话条目。
- 全局/向上文件在下次模型请求前检测变更；URL 5s 超时，缺失文件跳过。

**现状与证据**
- `backend/prompts/assembly.py:21` 的 `compose_system` 只接收 `vision_prefix/memory/rag/skills`，没有任何指令源。
- `backend/prompts/loader.py` 只按角色/provider 加载 `prompts/**`。
- 仓库自己的 `AGENTS.md` 与产品运行无关；全仓 grep `AGENTS.md` 只命中文档注释。

**缺口**
- 无发现顺序、无作用域、无变更检测、无预算/截断、无 URL 源、无嵌套按需加载。
- 无「指令来源」可观测性，用户无法知道本轮注入了哪些文件。

**严格实现要求**
1. 新增 `backend/prompts/instructions.py`：
   - `InstructionSource`（id、kind=`global|project|nested|config|url`、path/url、scope、mtime/hash、content）。
   - `InstructionResolver`：发现、排序、去重、按 token 预算截断（复用 `cognition/context_meter.py`，禁止第二套计数）。
   - `note_access(path)`：文件工具访问路径时登记，下轮注入嵌套 `AGENTS.md`；已加载条目不重复。
   - 变更检测：mtime/hash 在每轮开始前检查；删除后标记失效。
2. 改造 `compose_system`：新增 `instructions: list[InstructionBlock]`，**放在稳定头之后**，不得破坏前缀缓存（`prompts/assembly.py` 现有 stable head 逻辑保留）。
3. `PlatformContext` 在回合前按会话工作区解析；把解析结果写入 TurnContext/state，节点不得自行读盘。
4. 配置：`instructions_enabled`、`instructions: list[str]`、`instructions_max_chars`（或 token）；同步 `config.example.json` 与 `tests/unit/test_config_example.py`。
5. API：`GET /api/instructions?session_id=` 返回来源、作用域、是否加载、字符数（不回传正文或按权限脱敏）。
6. 前端：设置页只读展示当前会话指令来源与「已加载/已跳过」；不提供在线编辑（编辑走文件系统）。

**允许重构**
- 把 `prompts/assembly.py` 重构为 `stable_head + context_sections` 管线；现有 vision/memory/RAG/skills 全部变成 section，行为不变。

**落地位置**
- 新增：`backend/prompts/instructions.py`、`backend/tests/unit/test_instructions.py`、`backend/tests/integration/test_instructions_turn.py`
- 修改：`backend/prompts/assembly.py`、`backend/prompts/loader.py`、`backend/app/deps.py`、`backend/config.py`、`backend/config.example.json`、`frontend/src/views/*Settings*`

**验收标准**
- 单测：发现顺序（global → 向上 → 项目 → 配置）、去重、嵌套按需加载、mtime 变更、删除失效、URL 超时跳过、超长截断。
- 集成：工作区放 `AGENTS.md`，发起一轮请求，断言模型输入包含正文且顺序在 skills 之后；删除后下一轮不再包含。
- 回归：stable head 的字节哈希不变（prompt cache 不被破坏）。
- 安全：指令正文不通过 API 泄露给无权限客户端。

**禁止补丁**
- 禁止在 `api/compat.py` 或 `execute` 节点里 `open("AGENTS.md")` 拼接。
- 禁止把指令塞进 `skills_catalog` 或 system prompt 常量。
- 禁止只做全局不做项目作用域、或只做启动时一次读取不做变更检测。

---

### G-02 自定义 Commands

**opencode 语义**
- Markdown（`~/.config/opencode/commands/`、`.opencode/commands/`）或 JSONC `commands`；嵌套路径成为 `/team/review`。
- 字段：`template`、`description`、`agent`、`model`、`subagent`。
- 参数：`$ARGUMENTS`、`$1..$n`（引号分组、最高位消费剩余、缺失为空）；无占位符时非空参数追加。
- `!` + 反引号在提交前执行 shell 并插入输出；可切换 agent；可作为子会话后台执行。
- 模型优先级：命令 model → 命令 agent 的 model → 当前会话 model。

**现状与证据**
- `backend/app/skills/slash.py:49` 的 `apply_slash_skill` 只解析 `/技能名` 与 `/插件名`，返回注入文本。
- 前端斜杠面板在 `frontend/src/views/V2ChatView.vue:65` / `:823`；无命令注册表/命令 API。

**缺口**
- 无命令文件/JSON 注册、无参数展开、无命令级 agent/model、无 shell 插值、无后台子会话、无热重载。
- 现有 slash 实现是「技能/插件特判」，不是命令系统。

**严格实现要求**
1. 新增 `backend/app/commands/`：
   - `model.py`：`CommandSpec`（name、template、description、agent、model、subagent、source、priority、disabled）。
   - `registry.py`：全局 + 项目发现，嵌套路径 → `/a/b`，后加载覆盖同名；自动重载（mtime/watch）。
   - `expander.py`：纯函数做 `$ARGUMENTS`/位置参数/引号分组/fallback；输出 `ExpandedCommand`（text、agent、model、subagent）。
   - `service.py`：通过 v2 coordinator 提交 durable user prompt；不得新建消息通道。
2. 删除 `slash.py` 的技能/插件二分派发，改为 `CommandRegistry` 统一 dispatch；技能/插件作为内置命令来源注册（保持现有 `/技能名` 行为）。
3. Shell 插值：必须走审批/沙箱（这是与 opencode「shell 绕过工具权限」的**有意安全差异**，在文档标注），禁止直接 `subprocess`。
4. API：`GET /api/v2/commands`（列表、来源、参数提示）、`POST /api/v2/session/{id}/command`（或复用 prompt 契约并带 `command` 字段，二选一，不得两套）。
5. 前端：斜杠面板改为从 API 拉取；命令选中后填充 composer，参数提示；命令 agent/model 在提交前可见。

**允许重构**
- `app/skills/slash.py` 整体退役；技能/插件命令通过 `BuiltinCommandSource` 适配。

**落地位置**
- 新增：`backend/app/commands/*`、`backend/app/api/v2/commands.py`、`backend/tests/unit/test_commands_*`、`frontend/src/v2/commands.ts`、`frontend/tests/unit/commands.spec.ts`
- 修改：`backend/app/api/v2/__init__.py`、`backend/app/deps.py`、`frontend/src/views/V2ChatView.vue`

**验收标准**
- 单测：参数展开（含引号、越界、fallback）、注册表覆盖顺序、嵌套命令名、热重载、非法 command 报错。
- 集成：`/review file.ts` 生成的 user prompt 与直接输入一致；命令指定 agent/model 生效；shell 插值未授权时拒绝并返回可读错误。
- 回归：现有 `/技能名` 行为不变（技能仍被强制加载）。

**禁止补丁**
- 禁止继续在 `slash.py` 里追加 `elif`。
- 禁止前端自己展开参数后当普通文本发送。
- 禁止把命令实现成「往 system prompt 里塞字符串」。

---

### G-03 Formatters

**opencode 语义**
- `write`/`edit`/`patch` 后按扩展名运行格式化器；默认关闭。
- 内置（prettier/biome/ruff/gofmt/rustfmt…）+ 自定义命名条目（`command` 数组、`environment`、`extensions`、`disabled`）。
- `$FILE` 替换为绝对路径；多个匹配按注册顺序尝试，第一个成功即停；失败记录并尝试下一个。
- `formatter: true` 全部启用；`formatter: {}` 保留继承配置。

**现状与证据**
- 全仓无格式化执行器；`formatter` 仅出现在日志/argparse 上下文中。
- 文件写入路径：`backend/execution/tools/file_tools.py`（`write_file`/`edit_file`/`append_file`），直接返回结果。

**缺口**
- 无变更后钩子、无匹配表、无命令执行/超时/输出捕获、无事件。

**严格实现要求**
1. 新增 `backend/execution/formatters/`：
   - `spec.py`：`FormatterSpec`（name、command: list[str]、extensions、environment、disabled、requires）。
   - `builtin.py`：内置表，但只声明可执行文件与扩展名；不得在代码里写厂商特判。
   - `runner.py`：`run(path, workspace)`，`subprocess.run(command + [$FILE], shell=False, cwd=workspace, timeout=..., env=...)`；捕获 stdout/stderr；返回 `FormatterResult(status, formatter, duration, output)`。
   - `service.py`：按扩展名匹配、顺序尝试、首成功停止；失败写结构化日志与事件。
2. 抽出 `FileMutationService`（允许重构 `file_tools.py`）：写入成功后统一调用 formatter；只对真实工作区文件生效（VFS 路径 → 真实路径必须显式映射，映射失败则跳过并记录，不得猜）。
3. 触发范围严格限定：`write_file` / `edit_file` / 未来的 `patch`；不得对 `shell_exec` 的副作用、`append_file` 之外的读取做格式化。
4. 配置：`formatter: bool | dict`；同步 example 与完整性测试。
5. 事件：`formatter_applied` / `formatter_failed`，带 path、formatter、duration；前端时间线可选展示，不进入正文。
6. 安全：formatter 命令只来自服务端配置；不接收模型输入；`shell=False`；限制 env；超时终止。

**允许重构**
- `file_tools.py` 中的写操作收敛到 `FileMutationService`，snapshot 与 formatter 都挂同一后置钩子，避免两处重复。

**落地位置**
- 新增：`backend/execution/formatters/*`、`backend/tests/unit/test_formatters.py`、`backend/tests/integration/test_formatter_on_write.py`
- 修改：`backend/execution/tools/file_tools.py`、`backend/config.py`、`backend/config.example.json`、`backend/graph/nodes/base.py`（事件透传，如需要）

**验收标准**
- 单测：扩展名大小写敏感匹配、多 formatter 顺序、首个成功停止、失败回落、超时、禁用。
- 集成：`write_file` 命中 prettier/ruff 后文件内容变化；formatter 失败不影响工具结果但产生事件。
- 安全：命令数组中不允许 shell 元字符解释；路径注入不可改变命令结构。

**禁止补丁**
- 禁止在 `write_file` 内直接 `subprocess.run("prettier ...", shell=True)`。
- 禁止前端触发格式化（服务端真相源）。
- 禁止「格式化整个仓库」或对未变更文件运行。

---

### G-04 MCP 真实接入

**opencode 语义**
- 本地（stdio：`command`、`cwd`、`environment`）与远程（Streamable HTTP：`url`、`headers`、OAuth）。
- 工具以 `<server>_<tool>` 暴露；resources 通过 `list_mcp_resources` / `read_mcp_resource` 访问。
- 服务端 instructions 注入系统提示；超时（startup/catalog/execution）；连接池；OAuth（DCR/预注册/CIMD）。
- 权限动作：`<server>_<tool>`、`opencode_list_mcp_resources`、`opencode_read_mcp_resource`。

**现状与证据**
- `backend/mcp/adapter.py:20` 的 `MCPServer` 是**纯内存工具容器**，没有 JSON-RPC、进程、HTTP。
- `backend/execution/tools/mcp_client.py:17` 只是把内存容器包装成 `mcp_<name>` 工具。
- `backend/config.py`、`app/main.py`、`app/api/**` 均无 MCP 配置/路由；前端无入口。

**缺口**
- 无真实 transport、无生命周期、无配置、无鉴权、无 resources、无 instructions、无超时/重连、无 UI。
- 当前占位实现不能被描述为「支持 MCP」。

**严格实现要求**
1. 新增 `backend/mcp/` 真实子系统：
   - `config.py`：`MCPServerSpec`（type=local/remote、command/args/cwd/env、url/headers/oauth、timeout、enabled、protocol）。
   - `transport_stdio.py`：JSON-RPC over stdio，进程启动/退出/超时/日志；禁止 shell。
   - `transport_http.py`：Streamable HTTP；SSE 兼容可选；超时与重试。
   - `client.py`：`initialize`、`tools/list`、`tools/call`、`resources/list`、`resources/read`、`ping`；能力协商。
   - `session.py`：连接生命周期、重连、并发调用、关闭；一个 server 一个 session（池化后续再做，不得假装支持）。
   - `registry.py`：工具命名 `<server>_<tool>`、schema 转换、权限映射、指令收集。
2. `PlatformContext` 启动时装载并连接启用的 server；注册工具到 `ToolRegistry`（异步函数受支持，`graph/nodes/base.py:402` 已处理 coroutine）；应用关闭时优雅断开。
3. 工具结果必须过 `bound_tool_output`；不可信内容隔离；审批策略可配置。
4. 服务端 instructions 走 G-01 的 `InstructionResolver`，不得字符串拼到 system prompt。
5. API：`GET/POST/PATCH/DELETE /api/v2/mcp/servers`、`POST .../auth`、`POST .../reload`、`GET .../tools`、`GET .../resources`。
6. 前端：设置页新增 MCP 管理（local/remote 表单、连接状态、工具列表、启用/禁用、重新连接、OAuth 状态）。
7. 删除内存 stub 作为生产路径；只保留测试用 fake transport。

**允许重构**
- `execution/tools/mcp_client.py` 保留为「MCP tool → ToolRegistry」桥接层，但底层必须换成真实 session；不再导出 `create_mcp_server(enabled=False)` 这种假入口。

**落地位置**
- 新增：`backend/mcp/{config,transport_stdio,transport_http,client,session,registry}.py`、`backend/app/api/v2/mcp.py`、`backend/tests/unit/test_mcp_*.py`、`backend/tests/integration/test_mcp_stdio.py`、`frontend/src/views/McpView.vue`
- 修改：`backend/app/main.py`、`backend/app/deps.py`、`backend/config.py`、`backend/config.example.json`、`backend/execution/tools/mcp_client.py`

**验收标准**
- 集成：用本地 fake stdio server 完成 initialize → tools/list → tools/call；HTTP fake server 同理。
- 失败路径：启动超时、调用超时、进程崩溃、非法 JSON-RPC、工具不存在、权限拒绝、重连。
- 资源：list/read 返回结构化内容；instructions 进入模型输入。
- 安全：server 输出截断/脱敏；命令与 URL 只来自配置；未授权工具不可见且不可调用。

**禁止补丁**
- 禁止继续用 `MCPServer.register()` 手工注册冒充 MCP。
- 禁止把 MCP 工具实现成 `shell_exec` 包装。
- 禁止只做 stdio 不做错误/超时/关闭，或只做 UI 不做真实连接。

---

### G-05 用户自定义 Agents

**opencode 语义**
- Markdown（全局 `~/.config/opencode/agents/`、项目 `.opencode/agents/`）或 JSONC `agents`；嵌套路径成为 ID。
- 模式：`primary`（主 Agent，自定义默认）、`subagent`（子会话）、`all`。
- 内置：`build`、`plan`（primary）；`general`、`explore`（subagent）；隐藏 `compaction`、`title`、`summary`。
- 选项：`description`、`mode`、`model`（含 variant）、`system`、`permissions`、`steps`、`hidden`、`color`、`disabled`、`request`。
- 合并：标量后者覆盖、request 按 key 合并、权限规则追加；全局规则先、Agent 规则细化。

**现状与证据**
- `backend/graph/subagent.py:51` 是硬编码 `SUBAGENTS = {research, code, file}`，每项写死 prompt/tier/tools。
- `backend/app/api/admin.py:42` 只有只读 `GET /api/admin/agents`。
- `backend/app/api/v2/session.py:796` 只有会话级 agent 切换；无 Agent CRUD，无前端选择器。
- 主图角色（supervisor/plan/rag/compress）是固定节点，不是可配置 Agent。

**缺口**
- 无定义文件/JSONC、无 mode 语义、无 per-agent model/system/permissions/steps/hidden/color/disabled、无合并规则、无 CRUD/UI。
- 现有 `max_steps` 等是全局配置，无法按 Agent 覆盖。

**严格实现要求**
1. 新增 `backend/agents/`：
   - `spec.py`：`AgentSpec`（id、description、mode、model、system、permissions、steps、hidden、color、disabled、request、source）。
   - `registry.py`：全局 + 项目发现、JSONC/Markdown 解析、嵌套 ID、合并规则、校验（mode 合法、model 可解析、id 冲突）。
   - `resolver.py`：`default_agent` 选择（必须存在/可见/支持 primary，否则回退 `build`，再回退首个可见 primary）、subagent 能力校验。
   - `builtin.py`：把现有 supervisor/plan/rag/compress 映射为内置 spec；`compaction` 映射现有压缩 Agent，`title` 暂不实现则显式 `disabled`，不得假装存在。
2. 改造 `graph/subagent.py`：删除静态 `SUBAGENTS` 真相源，改为从 `AgentRegistry` 取 spec；现有 research/code/file 作为内置 spec 保留，行为不变。
3. 每轮：`AgentResolver` 产出生效 Agent；`steps` 传入 `run_tool_loop`；`permissions` 进入 G-10 的 per-agent 规则；`model` 走现有 `ModelRuntime` 解析。
4. API：`GET/POST/PATCH/DELETE /api/v2/agents`（含校验与冲突）、`GET /api/v2/agents/{id}`。
5. 前端：新增「Agent 管理」页（列表/编辑/模式/模型/权限/步骤/隐藏/颜色/禁用）；聊天 Composer 增加 Agent 选择器；默认 Agent 设置。
6. 与 G-01/G-02 共用文件发现与热重载机制，不重复造轮子。

**允许重构**
- `graph/subagent.py`、`graph/main_graph.py` 的 `task` 工具描述改为从注册表生成；`app/api/admin.py` 的只读列表合并进新 API（保留旧路径一个版本作为兼容，标注弃用）。

**落地位置**
- 新增：`backend/agents/*`、`backend/app/api/v2/agents.py`、`backend/tests/unit/test_agents_registry.py`、`backend/tests/integration/test_agent_selection.py`、`frontend/src/views/AgentsView.vue`、`frontend/src/v2/agents.ts`
- 修改：`backend/graph/subagent.py`、`backend/graph/main_graph.py`、`backend/app/api/v2/session.py`、`backend/config.py`、`backend/config.example.json`、`frontend/src/config/navigation.ts`、`frontend/src/views/V2ChatView.vue`

**验收标准**
- 单测：Markdown/JSONC 解析、嵌套 ID、合并顺序、非法 mode、未知 model、default 回退链、hidden 不可选。
- 集成：自定义 subagent 可被 `task` 调用；per-agent model 实际用于该子会话；per-agent steps 生效；per-agent 权限拒绝/放行。
- API/UI：CRUD 后立即对下一轮生效；禁用后不可选；会话切换 Agent 不重置消息。
- 回归：现有 research/code/file 调用行为不变。

**禁止补丁**
- 禁止继续往 `SUBAGENTS` 字典里加条目。
- 禁止在 `subagent.py` 里按 Agent 名字写 `if` 分支。
- 禁止只在 UI 层过滤而没有服务端校验。

---

## 3. P1：治理与扩展面

### G-06 Policies（硬策略层）

**opencode 语义**
- 二进制、永不询问的策略；只能收紧权限/Provider。
- 动作：`provider.use`、`tool.use`、`integration.connect`、`integration.use`、`mcp.use`、`skill.use`、`plugin.use`、`websearch.use`。
- `action/resource/effect(allow|deny)`；通配符；后匹配胜出；无匹配默认允许；硬拒绝返回 `Blocked by configuration policy`。
- 生效点：Provider 目录/选择、工具执行、MCP/skill/plugin 注册、websearch 工具。

**现状与证据**
- `backend/config.py:284` 有有序 `permissions` 规则；`backend/execution/permission.py:23` 有通配匹配。
- 但没有独立的 policy 层；工具/Provider/MCP/技能/插件注册处没有统一拒绝入口。
- 无 API/UI/文档。

**缺口**
- 无 `provider.use`/`tool.use`/`mcp.use`/`skill.use`/`plugin.use`/`websearch.use` 语义。
- 权限与策略混在一起，无法表达「策略不可被审批放宽」。

**严格实现要求**
1. 新增 `backend/policy/`：`PolicyStatement`、`PolicyEngine.evaluate(action, resource)`、来源优先级（全局/项目/Agent/托管）、后匹配胜出。
2. 重构 `execution/permission.py` 为 `AccessController`：先权限规则 → 审批 → 再 Policy 硬拒绝；所有调用点统一走它。
3. 接线点（缺一不可）：
   - Provider 解析/目录（`models/catalog.py`、`models/runtime.py`）
   - 工具执行（`graph/nodes/base.py` / `execution/tool_registry.py`）
   - MCP 注册（G-04）
   - Skill 加载（`app/skills/*`）
   - Plugin 装载（`extensions/plugins.py`）
   - Web search（`execution/tools/web_tools.py`）
4. API：`GET /api/v2/policies`（只读或可编辑按产品决定）、冲突解释接口；错误码 `blocked_by_policy`。
5. UI：设置页展示生效策略与来源；不允许前端自行判断放行。

**验收标准**
- 单测：匹配/顺序/通配、无匹配允许、allow 只解除更早的 deny、Provider 被隐藏、工具硬拒绝、MCP/skill/plugin/websearch 全覆盖。
- 集成：审批通过后仍被 policy 拒绝；policy 修改后下一轮生效。

**禁止补丁**
- 禁止在各调用点散落 `if settings.xxx_disabled`。
- 禁止 policy 只在前端过滤。
- 禁止把 policy 实现成 permission 的一个 effect 值（语义不同）。

---

### G-07 References（命名引用 / 外部目录 / Git）

**opencode 语义**
- `references` 对象：`path` 或 `repository`、`branch`、`description`、`hidden`。
- 本地路径相对配置/绝对/`~/`；字符串以 `.`、`/`、`~` 开头为本地，否则视为 Git 仓库。
- 远程 checkout 到全局数据目录，按 host/path/branch 唯一；缺失异步 clone，过期后台刷新（≥24h）。
- 客户端按 alias 附加「根目录一层列表」；`description` 注入 Agent 指引；`hidden` 只影响选择器。

**现状与证据**
- 无 references 模块/配置/API/UI。
- 最接近的是工作目录白名单（`data/runtime_workspaces.json`）与附件；语义不同。

**缺口**
- 无 alias、无远程 Git 缓存、无刷新策略、无引用列表附件。

**严格实现要求**
1. 新增 `backend/references/`：`ReferenceSpec`、`ReferenceRegistry`、`LocalReferenceProvider`、`GitReferenceProvider`。
2. Git 操作：使用系统 `git` 或受控库；固定 cwd/data 目录；超时、锁、失败回滚；不得阻塞 prompt（异步 clone，缺失时给「正在准备」状态）。
3. 安全：外部读取仍走权限（`external_directory`）；引用不授予额外写权限；checkout 目录禁止 Agent 写入。
4. API：CRUD、刷新、状态；前端 Composer 引用选择器 + 设置页。
5. `description` 通过 G-01 InstructionResolver 注入（不得直接拼 prompt）。

**验收标准**
- 单测：alias 校验、本地路径解析、branch 校验、刷新时间、hidden 过滤。
- 集成：用本地 bare git 仓库模拟远程，clone → 附加列表 → 读取文件；断网/超时不阻塞。

**禁止补丁**
- 禁止把工作目录白名单当 references。
- 禁止在请求路径同步 clone。
- 禁止无超时/无锁的 `git clone`。

---

### G-08 Provider 矩阵与模型 Variants

**opencode 语义**
- Provider：`name/env/package/canonical/compatibility/settings/headers/body/models`；`baseURL`、headers/body 覆盖、header/chunk/timeout、zstd/gzip、包类型（openai/anthropic/google/azure/bedrock/vertex/openrouter…）。
- 模型：`modelID/name/capabilities/compatibility/variants/cost/limit/disabled`。
- Variants：`provider/model#variant`，`settings/headers/body` 按 provider → model → variant 合并；reasoning 通过 capabilities（supported/toggle/effort/budget）。
- 本地发现：Ollama `:11434`、LM Studio `:1234`、vLLM `:8000`。

**现状与证据**
- `backend/models/catalog.py`、`backend/models/runtime.py`、`backend/models/builtin_catalog.py` 已支持目录 + Ollama 探测 + `num_ctx` + 缓存提示（`models/cache_hints.py`）。
- Provider CRUD 类型只有 `openai | ollama`（`frontend/src/stores/modelManager.ts:129`）。
- 无 variant/alias/free-model/超时/编码/云厂商适配。

**缺口**
- 变体、别名、能力覆盖、请求覆盖、超时/编码、云厂商、本地发现（LM Studio/vLLM）。

**严格实现要求**
1. 重构 `models/`：声明式 `ProviderSpec` + `ModelSpec` + `VariantSpec`；运行时 adapter 只按 `package` 选择，不写厂商 `if`。
2. 请求构建统一：provider → model → variant 合并 settings/headers/body；headers 大小写不敏感；未知 variant 报错。
3. 流式：`headerTimeout/chunkTimeout/timeout`（ms 或 false），超时重试次数明确；编码 `zstd|gzip|none`。
4. 本地发现：Ollama 已有；新增 LM Studio `:1234`、vLLM `:8000` 的 `/v1/models` 探测，标注能力未知（文本/tools 不臆测）。
5. UI：Provider 表单支持 package/compatibility/timeout/headers/body；模型支持 variants 编辑；聊天选择器支持 `#variant`。
6. 配置与目录持久化：新增字段必须可序列化、可回滚。

**验收标准**
- 单测：合并顺序、header 大小写、variant 解析/未知报错、超时、编码、能力未知回退。
- 契约：用 fake HTTP server 校验请求头/体、超时、流式分块。
- 回归：现有 DeepSeek/Ollama 行为与 `context_guard` 不变。

**禁止补丁**
- 禁止在 `runtime._invoke` 里堆 `if provider == "..."`。
- 禁止把 variant 实现成前端字符串拼接。
- 禁止对未知能力做乐观假设并静默失败。

---

### G-09 插件生命周期与 JS 插件

**opencode 语义**
- npm 包/本地目录/发现 `.ts`/`.js`；可加工具、hook、集成、命令、Agent、UI。
- 生命周期 hook；安装/版本/更新/移除/重载；插件 options；GUI/TUI 插件分离。
- 策略 `plugin.use` 可禁用不可信插件。

**现状与证据**
- `backend/extensions/plugins.py:11` 仅扫描 `plugins/*.py`，`Plugin` 只有 name/description/functions。
- 工具注册通过 `sync_plugin_tools`（`app/deps.py`）；无 hook bus、无 manifest、无版本、无 options。
- `/plugins` 有路由但不在 `FEATURE_NAV`（`frontend/src/config/navigation.ts:7`）。

**缺口**
- 无生命周期、无声明式 manifest、无冲突/顺序、无热卸载、无 JS/TS、无策略接入。

**严格实现要求**
1. 第一阶段（Python 插件 v2）：
   - `plugin.json`/frontmatter：name、version、description、options schema、permissions、entry。
   - `PluginHost`：load → validate → register（tool/agent/command）→ hooks；失败隔离；unload 时原子反注册。
   - Hook bus：`tool.execute.before/after`、`chat.message`、`permission.ask`、`session.start/end`、`tool.register`；顺序与异常隔离明确。
2. 第二阶段（JS/TS）：仅在明确需要生态时做；需要 Node worker/沙箱与稳定 API。**若不做，在文档写「不适用」，禁止用 Python 加载 JS 的假实现。**
3. 策略：`plugin.use` 接入 G-06；不可信插件默认禁用。
4. UI：把 `/plugins` 加入导航；展示版本/状态/options/日志；启用/禁用/重载。
5. 安全：Python 插件是进程内全信任，必须在 UI/文档显式提示；不得宣称沙箱隔离。

**验收标准**
- 单测：manifest 校验、加载顺序、hook 顺序、异常隔离、unload 反注册、重名冲突。
- 集成：插件注册工具后可被模型调用；禁用后不可见；hook 失败不影响主流程但产生事件。

**禁止补丁**
- 禁止继续用模块级 import 副作用注册工具。
- 禁止 hook 用 monkey patch 全局对象。
- 禁止只加一个 `on_tool_call` 字段就宣称生命周期。

---

### G-10 权限 UI / per-agent 规则 / 动作覆盖

**opencode 语义**
- `permissions`：有序 `action/resource/effect`；默认无匹配 `ask`；动作覆盖 read/edit/glob/grep/shell/subagent/skill/question/webfetch/websearch/external_directory/MCP tools。
- 审批 `once/always/reject`；`always` 保存项目级 allow pattern（不得覆盖 deny）。
- 目录外先过 `external_directory`；shell 扫描子命令与 `cd` 目标。

**现状与证据**
- `backend/execution/permission.py` 已有 allow/deny/ask、通配、变量、工作区、审批队列。
- `backend/config.py:284` 有 `permissions` 列表；但动作覆盖以路径/命令为主，`skill/websearch/subagent/MCP` 等 action 未统一求值。
- 审批是回合级临时授权（`AGENTS.md` §10：无「允许并记住」）；与 opencode `always` 是**有意差异**。
- 前端有审批弹窗/内联审批，但无规则编辑器。

**缺口**
- per-agent 权限（依赖 G-05）缺失；动作覆盖不全；规则编辑器缺失；`always` 语义缺失（可作为产品选择保留差异，但必须显式）。

**严格实现要求**
1. 动作模型统一：`Action = read|edit|glob|grep|shell|subagent|skill|question|webfetch|websearch|external_directory|<server>_<tool>|...`。
2. 权限来源合并：全局 → 项目 → Agent（后追加，最后匹配胜出）；`AccessController` 统一求值。
3. UI：规则列表/新增/排序/解释（为何被拒）；默认规则可查看；不提供「静默放宽策略」入口。
4. `always` 若实现：只写项目级 allow pattern，且策略 deny 永远优先；若不实现：在 UI/文档明确「审批仅本回合」。
5. shell 扫描保持现状并补测试（子命令、`cd`、重定向）。

**验收标准**
- 单测：动作全覆盖、per-agent 合并、最后匹配胜出、deny 优先、审批超时。
- 集成：per-agent 权限实际拦截工具；UI 规则修改下一轮生效。
- 回归：现有白名单/源码保护/`.git` 拒绝不变。

**禁止补丁**
- 禁止在工具内部再写一套路径判断。
- 禁止让前端决定 allow/deny。
- 禁止 per-agent 规则直接改写全局配置。

---

## 4. P2：体验与补齐

| ID | 项 | 现状 | 严格实现要点 | 验收 |
| --- | --- | --- | --- | --- |
| G-11 | Warming | 只有 Ollama `keep_alive` 与切换预热（`models/runtime.py`） | 新增 `harness/warming.py` 调度器：`warming: true|{prompt,interval,duration}`；空闲触发、非保温活动重置、单次请求丢弃响应、不派工具、不进图/不改状态；`cache.ttl` 驱动 `auto`；失败只记日志与指标 | 假时钟单测 interval/duration/重置；不产生消息/工具调用；禁用后不发请求 |
| G-12 | Network | 只有 `litellm_proxy_url`（`config.py:303`） | 统一出口工厂：`HTTP_PROXY/HTTPS_PROXY/NO_PROXY`、自定义 CA、超时；所有 httpx/LiteLLM/Web 工具共用；保留 SSRF 校验；提供连通性诊断 API | 代理优先级、NO_PROXY、CA 加载失败、SSRF 仍生效 |
| G-13 | Web search 策略 | 搜索/抓取可用（`execution/tools/web_tools.py`），无选择/冷却 | Provider 注册表（tavily/bing/ddg）；`websearch.provider=random`；429 冷却与切 provider；`websearch` 权限动作；配置 UI/状态 | 429 冷却、随机回退、禁用即移除工具、权限拒绝 |
| G-14 | Themes | 深浅色 + 8 背景（`frontend/src/stores/theme.ts`） | 主题文件 schema（tokens/hues/states）+ 校验 + CSS 变量映射；用户主题目录；热切换；无效主题回退；不按主题改组件代码 | schema/回退/持久化/对比度；构建通过 |
| G-15 | 工具面补齐 | 缺 patch/question/session/worktree/browser/execute/image | 每项独立设计：`patch` 用 diff 解析器（非 shell）；`question` 走 durable 交互（复用权限/输入通道）；session/worktree 走 git 与 `session.db`；browser/execute 单列 ADR（安全边界）；不得用 shell 别名冒充 | 每工具单测 + 权限 + 输出边界；失败回灌不崩溃 |
| G-16 | 附件 URI / 行范围 | 附件可用（`app/api/attachments.py`） | 统一附件契约 `{uri,name?,description?}`；`file://...#start/end`；目录一层列表；图片尺寸/字节限制配置；保留 Office 抽取 | URI 解析、行范围、目录、超限拒绝、图片缩放 |
| G-17 | Skills 扩展 | 本地技能闭环（`app/skills/*`） | 多源发现（全局/项目/`.claude`/`.agents`）、HTTP catalog、frontmatter 语义、`skill` 权限动作、`@skill-id` 直接调用；保留受管库 | 发现优先级、catalog 下载、权限、直接调用 |
| G-18 | 配置 API/UI | 只有摘要配置（`app/api/config.py`） | 通用 settings schema + GET/PATCH（分域、校验、脱敏）；前端设置页；新增配置必须同步 `config.example.json` + 完整性测试 | schema 校验、敏感字段脱敏、保存即生效、回滚 |
| G-19 | OpenAI 兼容 API/SDK | 缺失 | 仅在需要对外时做：`/v1/chat/completions` 适配 v2 会话、流式、鉴权、模型映射；OpenAPI 生成 SDK；不另建一套会话真相源 | 流式契约、鉴权、模型映射、断连处理 |
| G-21 | Compaction 增强 | 自动/手动已强（`harness/compression*`） | `experimental.background_compaction` 与 provider-native compaction 作为可选；必须走同一 `ContextCompactor`，不得新增第三种摘要实现 | 后台摘要不丢状态、native 失败回落、回归现有压缩测试 |

---

## 5. 已对齐与保留差异（避免重复建设）

| 能力 | 证据 | 处理 |
| --- | --- | --- |
| Compaction 主路径 | `harness/compression.py`、`harness/compression_agent.py`、`app/api/v2/session.py:625` | 保持；只做 G-21 增强 |
| Snapshots | `app/snapshot/*`、`app/api/v2/session.py:663` | 保持；新增工具时复用同一快照钩子 |
| Attachments | `app/api/attachments.py`、`app/attachment_processor.py` | 保持；只补 G-16 契约 |
| Permissions 核心 | `execution/permission.py` | 保持；按 G-10 扩展 |
| Skills 本地闭环 | `app/skills/loader.py`、`app/skills/registry.py`、`app/skills/tools.py` | 保持；按 G-17 扩展 |
| Tools 注册表 | `execution/tool_registry.py`（支持 async，`graph/nodes/base.py:402`） | 保持；新工具一律注册 |
| 模型目录 / Ollama | `models/catalog.py`、`models/runtime.py`、`models/context_guard.py` | 保持；按 G-08 重构 provider 层 |
| Prompt 缓存 | `prompts/assembly.py`、`models/cache_hints.py` | 保持稳定头；G-01 只追加 section |
| Web search 核心 | `execution/tools/web_tools.py` | 保持；按 G-13 加策略 |
| RAG / Memory / Budget | `rag/`、`graph/nodes/memory.py`、`harness/budget.py` | 不在本矩阵范围 |

**保留差异（有意，不算缺口）**
- 审批仅本回合有效，不提供「允许并记住」（opencode 有 `always`）。
- 命令中的 shell 插值仍走审批/沙箱（opencode 绕过工具权限）。
- 不做 CLI/TUI/Desktop/Console/ACP。
- Agent 选择以内置 supervisor/plan + 自定义 spec 为准，不照搬 opencode 的 `question/plan_enter/plan_exit` 模式流。

---

## 6. 依赖与实施顺序

1. **地基**：G-01 Instructions + G-10 动作模型 + G-06 AccessController 雏形。
2. **可配置面**：G-05 Agents → G-02 Commands（两者共用发现/热重载）。
3. **写路径**：G-03 Formatters（依赖 FileMutationService 重构）。
4. **外部能力**：G-04 MCP（依赖 Tools/Permissions/Instructions）。
5. **治理**：G-06 Policies 全量接线（依赖所有注册点）。
6. **外部引用与模型**：G-07 References、G-08 Providers/Variants。
7. **扩展生态**：G-09 Plugins。
8. **体验补齐**：G-11 ~ G-18 / G-21。
9. **可选**：G-19。

关键依赖：G-06 必须在 G-04/G-05/G-09 的注册点稳定后一次性接线，否则会反复改调用点。

---

## 7. 全局验收门禁（每个缺口都必须过）

```powershell
cd backend
.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest tests\unit tests\contract tests\reliability tests\security -q --cov=harness
```

```powershell
cd frontend
npm test
npm run build
```

强制项：

1. 配置完整性：新增字段必须进 `config.example.json`，`tests/unit/test_config_example.py` 通过。
2. 契约不变量：SSE 仍带 `turn_id/session_id/seq`；`session.db` 仍是真相源；checkpoint 只做单轮运行时。
3. 复用 canonical：压缩走 `harness/compression.py`，token 估算走 `cognition/context_meter.py`，权限走 `AccessController`。
4. 安全：路径/命令/SSRF/密钥脱敏/不可信工具输出边界全部覆盖。
5. 失败可观测：不允许静默 fallback；新增错误必须有类型与事件。
6. 文档：更新本矩阵状态、`AGENTS.md`、必要时新增 ADR。

---

## 8. 文档同步与遗留清理

- 本文件是 2026-11-08 的新基线；后续实现完成后直接在本表更新状态与证据。
- `docs/opencode对齐状态总览.md`（2026-09-09）引用了已删除路径（`backend/app/agent/**`、`graphmod`、`chatmod`、`MultiAgentView.vue`），已过时。实现 G-05/G-02 时应一并更新或标记归档，避免第二份真相。
- `docs/harness-architecture-design.md` 仍是 Harness 权威；本文件只记录「opencode 能力面对齐」，两者冲突时以代码 + 测试 + Harness 设计为准，并回写本文件。
