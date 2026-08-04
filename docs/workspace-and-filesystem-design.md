# 工作目录管理与文件系统工具设计（对齐 opencode）

> 参考 opencode 的 **工作目录 / 权限 / 文件系统工具** 设计，适配本项目的
> **FastAPI + LangGraph + Vue3** 技术栈。
>
> opencode 核心设计（源码参考）：
> - 实例上下文：`packages/opencode/src/project/instance-context.ts`（`directory`(cwd) + `worktree`(git root) + `containsPath`）
> - 外部目录权限：`packages/opencode/src/tool/external-directory.ts`（`external_directory` 审批）
> - 权限配置：`packages/core/src/v1/config/permission.ts`（`ask/allow/deny` + 目录 pattern）
> - 文件系统工具：`packages/opencode/src/tool/read.ts` / `write.ts` / `edit.ts` / `glob.ts` / `grep.ts`
> - 工具参数校验：`packages/opencode/src/tool/tool.ts`（Effect Schema 类型化 + 运行时校验 → `InvalidArgumentsError`）

---

## 1. 背景与现状

### 1.1 长文件读写测试发现的三个问题

| # | 问题 | 现象 | 根因 |
| --- | --- | --- | --- |
| 1 | 单次写入有大小限制 | 一次性写 190+ 行（约 10KB）tool-call 失败，JSON 参数被截断 | LLM 单次 tool-call 输出 token 有上限，超长 `content` 被截断；现有靠 `__APPEND_MARKER__` + `edit_file` 拼接，脆弱易错 |
| 2 | `tool_read_file` offset/limit 类型 bug | `'<' not supported between instances of 'str' and 'int'` | `tools.py` 将所有参数 schema 声明为 `"type": "string"`，LLM 只能传字符串，与函数内整数比较冲突 |
| 3 | `tool_grep`/`tool_glob` 只搜默认工作区 | 无法搜索 `F:\tetris` 等已授权目录 | `filesystem.py` 硬编码 `WORKSPACE.glob(...)`，绕过了 `_resolve` + `_ensure_safe` 权限机制 |

### 1.2 工作目录配置现状

- 主工作区：`backend/`（`WORKSPACE` 常量 + `PermissionManager.workspace` 启动时固定）。
- 额外工作区：两套入口并存，职责重叠：
  1. **环境变量** `EXTRA_WORKSPACES`（`config.py:73` → `runtime.py:97-99`），需改 `.env` 重启生效；
  2. **前端「工作目录」面板**（`ChatView.vue` → `POST /api/permission/workspaces` → `runtime_workspaces.json`），运行时生效、持久化、免重启。
- 问题：环境变量入口与前端入口重复，配置分散、需重启、文档两处维护（`README.md` / `.env.example`）。

### 1.3 目标

对齐 opencode 的「运行时可配置目录 + 类型化工具 + 权限统一走审批」思路：

1. **去掉 `EXTRA_WORKSPACES` 环境变量**，工作目录**唯一入口为前端配置**（持久化到 `runtime_workspaces.json`）。
2. **工具参数类型化**（`integer`/`boolean`/`string`）+ 运行时强转兜底，修复 offset/limit 类型 bug。
3. 新增 **`tool_append_file`** 分段追加，替代 `__APPEND_MARKER__` 拼接，解决大文件写入。
4. **`tool_grep` / `tool_glob` 增加 `root` 参数**，可搜索任意已授权目录，权限走 `_ensure_safe` 统一审批。

---

## 2. opencode 参考设计

### 2.1 实例上下文（`instance-context.ts`）

```
InstanceContext = { directory: cwd, worktree: gitRoot }
containsPath(filepath, ctx) = 在 directory 或 worktree 内 → true（免外部目录审批）
```

- 所有文件工具把**相对路径基于 `directory` 解析**，输出**绝对路径**。
- 工作区内路径默认放行；工作区外路径触发 `external_directory` 审批（`external-directory.ts`：以父目录 glob 向用户请求授权）。

### 2.2 权限配置（`v1/config/permission.ts`）

```
permission: {
  read: "allow" | "deny" | "ask" | { pattern: action },
  edit: ...,
  bash: ...,
  glob / grep / external_directory: ...,
}
```

- 每个工具通过 `ctx.ask({ permission, patterns, always, metadata })` 发起权限检查。
- `external_directory` 独立成类，显式控制「工作区外目录」的读/写/搜索。

### 2.3 工具参数 Schema（`tool.ts`）

- 每个工具用 **Effect Schema** 声明参数类型（`NonNegativeInt`、`Boolean`、`String`…），运行时 `decodeUnknownEffect` 校验。
- 校验失败 → `InvalidArgumentsError` 以工具结果回传给 LLM，让模型重写参数，而不是函数内部 `TypeError`。
- 类型在 JSON Schema 中原样暴露给 LLM（`type: "number"` / `"boolean"`），从源头减少字符串误传。

### 2.4 glob / grep 的 `path` 参数（`glob.ts` / `grep.ts`）

- 均有可选 `path` 参数：默认当前工作目录，显式指定可搜索任意目录。
- 对目标目录先 `assertExternalDirectory`（外部目录走 `external_directory` 审批），再执行搜索。
- 结果输出**绝对路径**（`path.resolve(...)`），LLM 可直接回传给 read/edit。

### 2.5 大文件写入

- opencode 本身无独立 append 工具，但系统提示词与工具说明强调：
  - 优先编辑已有文件、不建冗余文件；
  - 写入前必须已 Read，避免整文件覆盖丢失内容。
- 本项目补充 `tool_append_file`，在保留 `write` 语义的同时给 LLM 一个明确的**分段追加**出口，配合提示词把「先写首段 → 分段追加」变成标准动作。

---

## 3. 目标设计

### 3.1 工作目录模型 v2（前端唯一配置入口）

**移除环境变量入口：**

- `backend/app/config.py`：删除 `extra_workspaces: str = ""`。
- `backend/app/runtime.py`：不再从 `settings.extra_workspaces` 读取，`PermissionManager` 只从 `runtime_workspaces.json` 加载。
- `backend/.env.example`：删除 `EXTRA_WORKSPACES` 说明。

**前端配置工作目录（保留并作为唯一入口）：**

```
前端「工作目录」面板(ChatView.vue)
   ├─ GET    /api/permission/workspaces   → 主工作区 + 已配置目录
   ├─ POST   /api/permission/workspaces   → 运行时新增（免重启，立即生效）
   └─ DELETE /api/permission/workspaces   → 运行时移除
              └─ PermissionManager.add_workspace/remove_workspace
                   └─ runtime_workspaces.json 持久化（重启后依然生效）
```

- `PermissionManager.extra_workspaces` 内部机制保留，但**唯一数据来源是前端配置**。
- 新增目录自动 `mkdir` 并立即进入 `classify_path` 的 `workspace` 分类（读/写/执行同规则）。
- `list_workspaces()` = `[主工作区(backend/), *前端配置目录]`，供系统提示词动态注入「可写工作区列表」。

### 3.2 文件系统工具对齐 opencode

#### 3.2.1 参数类型化 + 运行时强转（修问题 2）

- `create_filesystem_tools()`：用 `inspect.signature` + `_annotation_to_json_type` 声明真实类型
  （`int → integer`、`bool → boolean`、`str → string`），LLM 的 JSON Schema 中看到类型提示。
- 每个工具函数内部用 `_coerce_int` / `_coerce_bool` 兜底强转（防御 LLM 仍传字符串）：

```python
def _coerce_int(v, default: int = 0) -> int:
    if isinstance(v, bool): return default
    try: return int(v)
    except (TypeError, ValueError): return default

def _coerce_bool(v, default: bool = False) -> bool:
    if isinstance(v, bool): return v
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on")
    if v is None: return default
    return bool(v)
```

- 应用点：`tool_read_file`(offset/limit)、`tool_edit_file`(replace_all)、`tool_write_file`(overwrite)、
  `tool_grep`(context/count_only/files_only)、`tool_execute`(timeout)。

#### 3.2.2 `tool_append_file` 分段追加（修问题 1）

```python
def tool_append_file(path: str, content: str) -> str:
    """向文件追加内容（文件不存在则创建）。用于分段写入大文件。"""
```

- 语义：文件不存在 → 创建并写入；存在 → 追加。返回 `Appended N bytes (total M)`。
- 与 `tool_write_file`（覆盖整文件）互补，组成「首段 write + 分段 append」的标准大文件写入流程。
- 系统提示词补充指令：**内容超过 ~6KB 时，先 `tool_write_file` 写首段（≤150 行），再多次 `tool_append_file` 追加**。

#### 3.2.3 `tool_grep` / `tool_glob` 增加 `root` 参数（修问题 3）

```python
def tool_glob(pattern: str, root: str = ".") -> str
def tool_grep(pattern, include="", context=0, count_only=False, files_only=False, root=".") -> str
```

- `root_path = _resolve(root)`（相对路径基于 `backend/`），`_ensure_safe(root_path, "read")` 复用统一权限审批：
  - 前端已配置目录 → `allow`；
  - 外部路径 → `ask`（前端弹窗审批）/ `deny`。
- 搜索范围改为 `root_path.glob(file_pattern)`；输出路径规则：
  - `root == 默认工作区` → 保持现状（相对路径），无行为变化；
  - `root` 为自定义目录 → 输出**绝对路径**，LLM 可直接回传 read/edit（对齐 opencode 绝对路径输出）。

### 3.3 系统提示词与权限提示更新

- `tools.py` 文件系统工具清单：新增 `tool_append_file(path, content)`；`tool_grep`/`tool_glob` 增加 `root` 参数说明。
- `graph.py` `_system_prompt_with_kb` 的内置工具清单同步加入 `tool_append_file`。
- 「可写工作区列表」保持动态注入（`_writable_workspaces()`），提示文案把「在 `.env` 配置 EXTRA_WORKSPACES」改为「在前端页面右上角『工作目录』中添加」。

---

## 4. 落地实施计划

| 阶段 | 改动 | 涉及文件 |
| --- | --- | --- |
| P1 去掉 EXTRA_WORKSPACES | 删除 env 配置与读取；`PermissionManager` 仅加载前端配置 | `backend/app/config.py`、`backend/app/runtime.py`、`backend/.env.example` |
| P2 类型化 + 强转 | `_coerce_int`/`_coerce_bool` 辅助函数；filesystem 工具内应用；schema 声明真实类型 | `backend/app/tools/filesystem.py`、`backend/app/agent/tools.py` |
| P3 append 工具 | 新增 `tool_append_file` 并注册到工具列表与系统提示词 | `backend/app/tools/filesystem.py`、`backend/app/agent/tools.py`、`backend/app/agent/graph.py` |
| P4 grep/glob root | 增加 `root` 参数 + `_ensure_safe` 权限检查 + 绝对路径输出 | `backend/app/tools/filesystem.py`、`backend/app/agent/tools.py` |
| P5 文档 | 更新 README / AGENTS.md / 本设计文档 | `README.md`、`AGENTS.md`、`docs/` |

## 5. 验收标准

1. 后端启动无 `EXTRA_WORKSPACES` 相关配置与日志；`.env` 中即使存在该变量也不生效。
2. 前端「工作目录」面板添加 `F:\tetris` 后：`tool_read_file` / `tool_write_file` / `tool_append_file` / `tool_grep(root="F:\tetris")` / `tool_glob(root="F:\tetris")` 全部可读写、可搜索；重启后端后目录仍在。
3. `tool_read_file(path, offset="10", limit="50")` 以字符串传参不再报 `TypeError`。
4. 大文件写入：LLM 用 `tool_write_file` 写首段 + `tool_append_file` 分段追加，1000 行文件无截断、无跳号。
5. `tool_grep` / `tool_glob` 默认行为与改造前一致（相对路径输出、只搜主工作区）。
