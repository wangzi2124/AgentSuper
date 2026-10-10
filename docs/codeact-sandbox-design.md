# CodeAct 受控执行器设计（v2.2）

> 状态：**Phase 1 已实现（backend/app/codeact/），默认关闭**。
> 定位：本文档描述的是**受控代码执行器**，不是“安全沙盒”。
> 安全边界 = 可信用户 + 可信工作区 + 默认关闭 + 六层防护。真正的强隔离（独立内核/容器）留到 Phase 3。
> 关联：AGENTS.md（工具调用优化条）、`app/context/tool_output.py`（`bound_tool_output`）、
> `app/tools/file_tools.py`、`app/permission/manager.py`、`app/utils/ssrf.py`。

> **v2.2 修订要点（相对 v2.1，2026-10-10 代码评审落地）**：
> 1. **放弃 `CREATE_SUSPENDED` 时序**：实测 `subprocess.Popen._handle` 只剩**进程句柄**，主线程句柄创建后即关闭，`ResumeThread(进程句柄)` = ERROR_INVALID_HANDLE(6)。改为「先建 Job → 正常创建 → 立即 assign」；**assign 失败不再只记 warning，而是立即 kill + 置失败结果**（§5.1）。
> 2. **就绪帧（ready）握手封死启动期窗口**：bootstrap 构建完 api + 装好 audit hook 后才发 ready；宿主在收 ready 前**不派发任何 RPC**。启动残留窗口 = 连接→token→白名单 import→hook→ready，无执行面（§3/§6）。
> 3. **RPC 白名单硬编码且独立于 agent allowlist**（`rpc.py:_ALLOWED_RPC`）：不信任 `sub_tools` 允许表传参，`tool_execute`/任何可再起 shell/沙盒的工具名直接拒绝（§4.4/§7）。
> 4. **api 函数走闭包注入**（`sdk_template.make_api(rpc, _b64)`）：`_rpc`/帧收发原语不进 api 源码模块的 `__globals__`，用户代码拿不到分发入口（§7）。
> 5. **读/写根分离 + 截断转储目录只读**：`read_roots` 含工作区 + `data/codeact` + `data/generated` + **`data/truncation`**；`write_roots` 不含 truncation → 沙盒只能读回 `bound_tool_output` 落盘的截断全文，不能改（§4.4）。
> 6. **`save_artifact` 白名单化**：文件名 sanitize（禁路径分隔/非法字符/ADS）+ `CODEACT_MAX_ARTIFACT_SIZE_MB` 上限 + 相对工作区路径返回（§7/§9）。
> 7. **执行超时钳制**：`min(请求值, 全局 codeact_timeout)`，只能调小不能放大；`_pause_depth` 让宿主侧任何等待（审批 + RPC）都暂停执行计时（§5.3/§6）。
> 8. **沙盒崩溃 stderr 尾部脱敏回灌**：`_sanitize_text`（用户目录 + 疑似密钥），并把 Windows 提交内存超限退出码 `0xC000012D` 映射为 `memory_limit`（§6/§8）。
> 9. **帧长上限对读侧也对**：`session._read_frame` 拒绝 declared size > 16MB，防恶意子进程撑爆宿主内存（§3.2）。
> 10. **`-S` + `-X pycache_prefix`**：白名单全为标准库，`-S` 禁 site-packages；pycache 收进 run_dir 随运行目录清理（§4.6）。
> 11. 子进程 stdout/stderr 重定向到 `data/codeact/run/<run_id>/{stdout,stderr}.log` 供诊断（§8）。

> **v2.1 修订要点（相对 v2）**：
> 1. 补「安全前置约束」：CodeAct 开启时必须同步收紧 `tool_execute` 的 `python/node -c` 入口，否则六层可被一句话绕过（§4.0）。
> 2. AST 守卫新增 **dunder 属性访问拦截**（`x.y` 不受原黑名单约束，`object.__subclasses__()` 类逃逸必须堵）（§4.1）。
> 3. 修正 `SAFE_BUILTINS`：补异常类、保留 `__build_class__`、用白名单 wrapper 保留 `__import__`（原白名单自相矛盾，白名单 `import` 与「删 `__import__`」冲突）（§4.2）。
> 4. 执行超时**不含审批等待**（否则 10s 必杀，审批弹窗永远来不及点）（§5.3/§6）。
> 5. 明确**禁用 `asyncio.create_subprocess_*`**，统一 `subprocess.Popen` + 后台线程桥（本仓 SelectorEventLoop 实测坑，见 AGENTS.md）（§3.3）。
> 6. 审批撤销复用现有 `PermissionManager.respond(req_id, "denied")`（无需新增 `cancel` API）（§6）。
> 7. `_safe_resolve` 的 reparse 检查移到 **resolve 之前**（原写法是死代码）（§4.4）。
> 8. stdout 落盘改走 `data/truncation/`（复用 `bound_tool_output` + 7 天清理），产物目录独立 `data/codeact/`（避免污染「生成文件」页）（§8/§9）。
> 9. Job Object 补 `CREATE_SUSPENDED` 分配 + 嵌套/breakaway 失败 fallback（§5.1）。
> 10. `codeact_max_retries` 由 `graphmod/generate.py` 强制（连续失败计数），非纯提示词（§10）。
> 11. 配置并入 `app/config.py`（`app/codeact/config.py` 读不到 `.env`）（§2/§9）。
> 12. RPC 分发表复用 `sub_tools.run_tool` + 工具注册表（免费拿到 SSRF 检查、`bound_tool_output`、轮次快照）（§7）。

---

## 0. 与 v1 的核心差异

| 项 | v1 | v2 / v2.1 |
|---|---|---|
| 安全定位 | 自称“沙盒” | 明确“受控执行器”，安全声明写清楚 |
| RPC 通道 | stdin/stdout 混用 | 控制面/数据面分离 |
| 安全模型 | AST 单层 | AST（含 dunder 拦截）+ 受限 builtins + audit hook + 宿主路径校验 |
| 资源限制 | 无内存限制 | Windows Job Object + POSIX rlimit |
| 生命周期 | 散落 | `ExecutionSession` 统一管理超时/审批/取消/并发 |
| 导入控制 | 黑名单 | 预加载白名单 + 白名单 `__import__` wrapper + sys.modules 清理 |
| 与既有体系 | 孤立 | 复用 `run_tool`/`PermissionManager`/`bound_tool_output`/SSRF/快照 |

## 1. 目标与非目标

### 1.1 目标
- 让 LLM 写一段 Python，多步逻辑（循环/条件/变换）一次执行完成。
- 工具调用经宿主 RPC，复用 `PermissionManager`、审批事件桥、`bound_tool_output`。
- 默认关闭；开启后风险可解释、可审计、可终止。

### 1.2 非目标（明确不做）
- **不隔离恶意代码**。运行不可信用户/不可信工作区时必须走 Phase 3（更强隔离）。
- **不做有状态 Kernel**（Phase 2）：无状态单发式执行，无交互/继续。
- **不做网络出口代理**（Phase 2）：`web_search` 走宿主 SSRF 白名单通道，沙盒内无直接网络。

## 2. 目录结构

```
backend/app/codeact/
├── __init__.py
├── ast_guard.py       # 静态分析（导入白名单 + 名/调用黑名单 + dunder 属性拦截 + 规模上限）
├── bootstrap.py       # 沙盒子进程入口（独立文件，非字符串模板）
├── sdk_template.py    # 生成沙盒内 api 模块源码
├── rpc.py             # 宿主侧 RPC 分发（含路径根校验）
├── session.py         # ExecutionSession：生命周期/超时/审批/取消
├── job_object.py      # Windows Job Object 封装（ctypes）
├── kill_tree.py       # 跨平台杀进程树（复用 file_tools._kill_process_tree 逻辑）
└── result.py          # 结构化结果
```

> **配置不单独成文件**：`backend/.env` 只由 `app/config.py`（pydantic-settings）读入并注入
> `os.environ`；独立的 `app/codeact/config.py` 读不到 `.env`。因此所有 `codeact_*` 配置项并入 `app/config.py`。

## 3. 通道设计

### 3.1 双通道（控制面 / 数据面分离）

```
┌─────────────────────────────────────────────────────┐
│  宿主 (asyncio)                                      │
│  ├─ Control pipe  (宿主→沙盒: 启停/取消/配置)          │
│  ├─ RPC pipe      (沙盒↔宿主: JSON lines)            │
│  └─ Result pipe   (沙盒→宿主: 最终结构化结果)          │
└─────────────────────────────────────────────────────┘
                  ▲
                  │ subprocess.Popen(pass_fds / pipe)
                  ▼
┌─────────────────────────────────────────────────────┐
│  沙盒子进程                                           │
│  ├─ RPC: 独占 fd 3 (读) / fd 4 (写)                  │
│  ├─ user stdout/stderr → StringIO 缓冲                │
│  └─ 执行结束 → 从 fd 5 发最终 JSON                    │
└─────────────────────────────────────────────────────┘
```

- RPC 独占专用 fd，用户 `print` 无论怎么打都不会污染 RPC。
- 用户 `stdout/stderr` 被替换为 `io.StringIO`，执行完随最终结果一起回。
- Windows 下 `Popen` 不直接支持任意 fd 传递，用**本地回环 socket**（仅绑定 `127.0.0.1`，随机端口 + 随机 token 握手）替代；三路复用一连接，帧头区分 `rpc`/`result`/`control`。（命名管道 `\\.\pipe\codeact_<uuid>` 为备选，Phase 2 评估。）

### 3.2 RPC 帧

```
沙盒→宿主: {"ch":"rpc","id":1,"fn":"write_file","args":{...}}
沙盒→宿主: {"ch":"ready"}                          # 【v2.2】就绪握手，见下
宿主→沙盒: {"ch":"rpc","id":1,"ok":true,"result":"..."}
           {"ch":"rpc","id":1,"ok":false,"error":{"code":"permission_denied","msg":"..."}}
宿主→沙盒: {"ch":"control","cmd":"cancel"}
沙盒→宿主: {"ch":"result","ok":true,"stdout":"...","stderr":"...","last_expr":"...","traceback":null,"files":[...]}
```

- 每条帧有**长度前缀**，避免半包/粘包。
- **帧长上限双向**：encode（`result.py:_MAX_FRAME_BYTES=16MB`）与 decode（`session._read_frame`
  拒绝 declared size > 16MB，D2）对称——防恶意子进程声明超大帧撑爆宿主内存。
- **就绪握手（P0/A）**：bootstrap 在装完 audit hook、构建好 api 模块后、`exec` 用户代码**之前**
  发 `{"ch":"ready"}`；宿主收到后才 set `_ready`。任何 `rpc` 帧派发前先阻塞等 ready
  （`wait_for` 到 `rpc_timeout`）——启动残留窗口内子进程即便异常发帧也不会被执行。
- 宿主侧 asyncio 读循环 + 每 RPC 一个 `Future`。
- RPC 有独立超时（默认 30s，`CODEACT_RPC_TIMEOUT`），超时回 `{"ok":false,"error":{"code":"rpc_timeout"}}`，**不直接杀子进程**，让模型自己决定。

### 3.3 子进程创建：必须 `Popen` + 线程桥（仓库实测约束）

> **硬约束（勿用 asyncio 子进程 API）**：本仓后端在 Windows + uvicorn 下以 **SelectorEventLoop** 运行，
> `asyncio.create_subprocess_*` 会抛 `NotImplementedError`（`app/agent/graphmod/tools.py:456/597` 有实测记录，
> `tool_execute` 流式路径因此重写为 `subprocess.Popen` + 后台线程 + `loop.call_soon_threadsafe`）。

- 子进程创建：`subprocess.Popen`（见 §5），**不要** `asyncio.create_subprocess_exec`。
- RPC 读取：后台线程读 socket/pipe → `loop.call_soon_threadsafe` 唤醒对应的 asyncio `Future`。
- 用法与既有 `tool_execute` 流式路径保持一致，避免重复踩坑。

## 4. 安全模型

多层防护，非单点。**AST 是“降低面”，不是安全边界**；真正的边界是「可信用户 + 可信工作区 + 默认关闭 + §4.0」。

### 4.0 前置约束：必须同步收紧 `tool_execute` 后门

代码执行器是「更难越权」的替代通道，但**它不能凌驾于既有 `tool_execute` 之上**：
`tool_execute` 支持真实 shell 与 `python -c`/`node -e`（仅对 inline 参数做 `_DANGEROUS_PATTERNS` 关键词扫描，属弱防护）。
若 `CODEACT_ENABLED=true` 而 `tool_execute` 不收紧，模型用 `tool_execute python -c "..."` 即可绕过全部六层。

**要求**：`CODEACT_ENABLED=true` 时，`tool_execute` 的 `python/node -c` 入口列为**需审批**（或直接禁止），
并在系统提示词里明确「代码请走执行器」。否则本文档的安全叙述不成立。

### 4.1 第一层：AST 静态守卫（`ast_guard.py`）

拒绝即返回错误给 LLM，**不进沙盒**。

- **导入白名单**：`math, json, re, statistics, datetime, itertools, functools, collections, decimal, fractions, random, textwrap, csv, base64, hashlib, uuid, unicodedata, string`。
- **导入黑名单**：`os, sys, subprocess, socket, shutil, ctypes, importlib, pathlib, urllib, http, requests, threading, multiprocessing, signal, pty, tempfile, glob, pickle, marshal, shelve, dbm, code, codeop`。
- **名/调用黑名单**：`eval, exec, compile, __import__, open, globals, locals, vars, dir, getattr, setattr, delattr, memoryview, input, breakpoint, __builtins__`。
- **【v2.1 新增】dunder 属性拦截**：属性访问 `x.y` 不受函数名黑名单约束，而 `object.__subclasses__()` / `().__class__.__bases__[0].__subclasses__()` 是经典逃逸路径。
  因此 `ast.Attribute` 中凡 `attr` 以 `__` 开头或结尾（`__class__`/`__subclasses__`/`__globals__`/`__dict__`/`__bases__`/`__loader__`/`__import__`…）一律拒绝。
  （`__init__`/`__name__`/`__repr__` 等若确需，用白名单精确放行，不要整体放开。）
- 规模上限：AST 节点数 ≤ `CODEACT_MAX_AST_NODES`（默认 5000），代码字符 ≤ `CODEACT_MAX_CODE_CHARS`。
- 不做 `while`/递归静态拦截，由超时 + Job Object 兜底。

### 4.2 第二层：受限 builtins（`bootstrap.py`）

`exec(compiled, {"__builtins__": SAFE_BUILTINS}, local_ns)`。`SAFE_BUILTINS` 必须**自洽**（v1/v2 原白名单有三处逻辑冲突，均已修正）：

- **计算/类型**：`abs, all, any, ascii, bin, bool, bytes, callable, chr, complex, dict, divmod, enumerate, filter, float, format, frozenset, hash, hex, int, isinstance, issubclass, iter, len, list, map, max, min, next, object, oct, ord, pow, print, range, repr, reversed, round, set, slice, sorted, str, sum, tuple, type, zip`。
- **【修正】异常类**：`Exception, BaseException, ArithmeticError, ZeroDivisionError, ValueError, TypeError, KeyError, IndexError, AttributeError, RuntimeError, NotImplementedError, StopIteration, LookupError, OverflowError`。
  → 原白名单没有任何异常类，`try/except ValueError` 会直接 `NameError`，代码基本不可用。
- **【修正】保留 `__build_class__`**：删除它会使**任何** `class X:` 定义崩溃（不是“按需”，是必需）。
- **【修正】保留白名单 wrapper `__import__`**：CPython 的 `import` 指令 = `IMPORT_NAME` → 查 builtins 的 `__import__`。
  原设计删 `__import__` 会让 §4.1 的导入白名单**全部失效**（`import math` 直接崩）。
  改为注入一个 `_safe_import(name, ...)`：仅当 `name` 在导入白名单内才 `return real_import(name,...)`，否则 `raise ImportError`。
- **明确删除**：`open, eval, exec, compile, input, breakpoint, globals, locals, vars, dir, getattr, setattr, delattr`。
- 说明：`type` 仍可能用于动态建类，属已知残余面（审计为“降低面”）；`audit hook`（§4.3）为兜底。

### 4.3 第三层：audit hook

`sys.addaudithook`（装在预加载之后，**不可卸载**）拦截以下事件，命中即抛 `PermissionError`：

- `open` / `os.open` / `os.listdir` / `os.scandir`
- `subprocess.Popen` / `os.exec*` / `os.spawn*` / `os.system` / `os.fork`
- `socket.*` / `socket.getaddrinfo`
- `import` 中 `name` 不在预加载白名单
- `ctypes.*`

**内部导入冲突处理**：预加载阶段（hook 安装前）先把白名单模块**全部 import 完**；白名单模块内部需要 `os` 等危险模块时放行一次（标记位），加载完清理 `sys.modules` 里的危险模块，再装 hook。用户阶段任何 `import os` 都拒。

### 4.4 第四层：宿主 RPC 根校验（`rpc.py`）

所有文件 RPC 必须**独立于** `file_tools` 再做一次根校验，**不信任沙盒传参**：

```python
def _safe_resolve(root: Path, user_path: str) -> Path:
    raw = root / user_path
    # 【修正】reparse（符号链接/junction）必须在 resolve 之前对未解析路径检查，
    # 否则 resolve 已跟随链接，_has_reparse_point 永远为 False（原写法是死代码）
    if _path_has_reparse_ancestor(raw) or str(raw).startswith("\\\\"):
        raise PermissionError("reparse point or UNC not allowed")
    if _has_ads(str(raw)):                       # Windows ADS: "file:stream"
        raise PermissionError("ADS not allowed")
    p = raw.resolve(strict=False)                # Python 3.14，is_relative_to 无需兜底
    if not p.is_relative_to(root.resolve()):
        raise PermissionError("path outside workspace")
    return p
```

- **读/写根分离（v2.2 / B4）**：`read_roots = [会话工作目录, data/codeact, data/generated, data/truncation]`；
  `write_roots = [会话工作目录, data/codeact, data/generated]`（**不含 `data/truncation`**）。
  `data/truncation` 只读的意义：`bound_tool_output` 把大工具输出落盘到该目录并提示模型用
  `tool_read_file(offset)` 续读——沙盒内 `api.read_file` 必须能读回全文，但**不能篡改**截断转储。
  写 RPC（`write_file`/`append_file`）按 `write_roots` 校验，`read_file`/`list_dir`/`glob`/`grep` 按 `read_roots`。
- 根目录 = 会话工作目录 + 显式 allowlist（`data/codeact/`、`data/generated/`、只读 `data/truncation/`）。
- **RPC 名硬白名单（v2.2 / B2）**：`handle()` 先验 `fn in _ALLOWED_RPC`（硬编码，见 §7），
  越界直接拒绝——**不**继承 agent allowlist（allowlist 可能含 `tool_execute` 等高危名）。
- 读操作同样校验。
- `.env` / `.git/config` / `*.pem` / `*.key` / 数据库文件额外黑名单（可复用 `file_tools._is_read_allowed` 与 `PermissionManager._is_git_path` 常量，避免重复实现）。

### 4.5 第五层：宿主权限系统

RPC 命中 `file_tools` → `PermissionManager.check`：

- 允许 → 执行；
- `NeedsPermission` → 复用 `sub_tools._permission_request_payload` 发审批 → `await_decision`；
- 无事件队列 → 立即拒绝（与既有语义一致，不永久挂起）。

### 4.6 第六层：环境隔离

- 子进程 env 只传：`PATH, PATHEXT, SYSTEMROOT, SystemRoot, WINDIR, COMSPEC, TEMP, TMP, LANG`。
  （`-X utf8` 已决定编码；`PYTHONIOENCODING` 在 `-I` 下无效，删除。）
- **绝不传**：`LLM_API_KEY, DB_*, AUTH_TOKEN_SECRET, AWS_*, OPENAI_*` 等。
- 用 `-I -X utf8`（`-I` 隔离模式已隐含忽略 `PYTHON*` 环境变量与用户 site）；**v2.2 追加 `-S`**
  （白名单全为标准库，禁 `site-packages` 进一步收面）+ `-X pycache_prefix=<run_dir>/pycache`
  （白名单模块的 `__pycache__` 收进 run 目录，随 run 清理，不污染工作区）（C7/D4）。
  子进程 `stdin=DEVNULL`（C8）：沙盒不需要交互输入，杜绝 read from stdin 挂起。

## 5. 资源限制

### 5.1 Windows Job Object（`job_object.py`）

`ctypes` 调 `CreateJobObjectW` + `SetInformationJobObject`：

- `JOB_OBJECT_LIMIT_PROCESS_MEMORY`：默认 512MB
- `JOB_OBJECT_LIMIT_ACTIVE_PROCESS`：默认 16
- `JOB_OBJECT_LIMIT_JOB_MEMORY`：默认 1GB
- `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`：确保崩溃时子进程全灭
- 可选 CPU rate control（`JOB_OBJECT_LIMIT_CPU_RATE_CONTROL`，Win8+）

**【不可行→实测替换】`CREATE_SUSPENDED` 时序（v2.1 写法，已废弃）**：
`subprocess.Popen` 只保留**进程句柄**（`_handle`）；主线程句柄创建后即被关闭，`ResumeThread(进程句柄)`
返回 -1 / ERROR_INVALID_HANDLE(6)（实测，`codeact_repro.py`）。无法用 Popen 实现
「挂起创建 → assign Job → resume」的无窗口时序。

**【v2.2 落地】先建 Job → 正常创建 → 立即 assign，失败即杀**：
- `JobObject.create(...)` 先建好 → `Popen` 正常创建（`CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP`）
  → 立即 `AssignProcessToJobObject`。
- **assign 失败 = 立即 `TerminateJobObject`/kill + 置 `spawn_failed` 失败结果**，**不只记 warning**
  （P0/A：子进程已在跑但不在 Job 约束内，窗口从“极小”变“无限”，warning 是隐患）。
- 残留窗口仅限 bootstrap 启动期：子进程只连 socket / import 白名单标准库，不派生任何子进程；
  且收到 `ready` 帧前宿主不派发任何 RPC（§3.2 就绪握手）——执行面被封死。
- 嵌套 Job 限制：父进程已在不允许 breakaway 的 job 内时 assign 可能失败（Win8+ 才支持嵌套）——
  上一条的「失败即杀」正是为此兜底，不退化到“裸跑 + taskkill”。
- 杀树优先 `TerminateJobObject`；Job 不可用时退回 `kill_tree`（taskkill /T /F）。
- **Phase 2 可选**：要真正「无窗口」，用 ctypes `CreateProcessW` 拿 `PROCESS_INFORMATION.hThread`
  实现 `CREATE_SUSPENDED → assign → ResumeThread`（Popen 做不到，见上）。

### 5.2 POSIX 分支
`resource.setrlimit(RLIMIT_AS, RLIMIT_CPU, RLIMIT_NPROC, RLIMIT_NOFILE)`，在 `preexec_fn` 内设置。

### 5.3 超时
- **执行超时**：默认 10s（`CODEACT_TIMEOUT`）——**仅计子进程运行时间，暂停于审批等待期间**（见 §6）。
- **RPC 单次超时**：默认 30s（`CODEACT_RPC_TIMEOUT`）。
- 超时统一由 `ExecutionSession` 处理：发 `control cancel` → 等 500ms → `TerminateJobObject`（或 `kill_tree`）。

> **【修正】审批等待与执行超时的关系**：审批可能需要用户数分钟（`PERMISSION_APPROVAL_TIMEOUT` 默认 180s）。
> 若 10s 执行超时把审批等待也算进去，弹窗永远来不及点。**规则**：进入审批等待时暂停执行超时计时，
> 用户响应后恢复；审批自身由 `await_decision` 的 180s 上限兜底（超时返回 `expired` → 视为拒绝）。

## 6. 生命周期（`session.py`）

```
ExecutionSession
  ├── run()         spawn(Job→Popen→assign) + 监听 socket + 读 result / 看门狗
  ├── _on_client()  hello 握手 → 等 ready → 逐帧分发 RPC / 收 result
  ├── _rpc_loop()   读 RPC 帧 → 分发 → 回帧（后台线程 → call_soon_threadsafe）
  ├── _on_permission(req) 发审批 → await_decision（暂停执行超时）→ 回帧
  ├── cancel()      发 control cancel → 等 → 强杀(TerminateJobObject / kill_tree)
  └── close()       清理资源（finally）
```

关键规则：
- **审批撤销复用现有 API**：`PermissionManager` **没有** `cancel(req_id)`，只有 `create_request`(manager.py:484) / `await_decision`(508) / `respond`(523)。
  超时或外部取消时直接 `mgr.respond(req_id, "denied")` 即可（幂等，不存在则忽略），**无需新增后端 API**。
- **就绪握手门（P0/A）**：`_on_client` 收到 `ready` 帧才 set `_ready` Event；任何 `rpc` 帧在派发前
  先 `await wait_for(self._ready.wait(), rpc_timeout)`——启动窗口内即使子进程恶意发帧也不会被执行。
  bootstrap 顺序：连 socket、hello 握手、按白名单预载标准库、装 audit hook、`exec` 前 **send ready**。
- **宿主侧等待全部暂停执行计时（C1）**：审批等待 **和 **所有 RPC 派发存续期**都以
  `on_approval_begin()/end()` 包围（`_pause_depth` 计数 + 重叠合并），看门狗公式
  `now >= base + grace_total` 才判超时——沙盒内 `api.read_file` 读大文件、web_search 等不再
  消耗执行预算。
- **超时钳制（B1）**：`self.timeout = min(请求传入 timeout, 全局 codeact_timeout)`，只能调小不能放大；
  下限 1s。`rpc_timeout` 独立于执行超时。
- 沙盒崩溃（进程退出码非 0 且无 result 帧）→ 结构化错误 `{"ok":false,"error":{"code":"sandbox_exited","msg": <stderr 尾部 sanitize>}}`；
  Windows 提交内存超限退出码 `0xC000012D`（`STATUS_COMMITMENT_LIMIT`）映射为 `"memory_limit"`（C6）。
  stderr 尾部回灌前过 `_sanitize_text`：用户目录 `C:\Users\<name>\` → `<user>`、疑似密钥 → `<redacted>`（B5）。
- 并发：`asyncio.Semaphore(CODEACT_MAX_CONCURRENT=2)` 全局限流，与 `MAX_CONCURRENT_AGENTS` 协同。
- `tool_code_exec` 计入 `MAX_TOOL_ROUNDS`；连续失败由 `graphmod/generate.py` 计数强制（§10）。
- 每个 session 有 run_id（uuid），日志/审计带上（run 目录 `data/codeact/run/<run_id>/`）。

## 7. SDK 设计（`sdk_template.py`）

沙盒内注入 `api` 模块（`types.ModuleType`），函数体全部走 RPC。

**【v2.2】闭包注入（B3）——`sdk_template.make_api(rpc, _b64)` 工厂**：
`build_api_source()` 只生成包含 `make_api` 的模块源码；bootstrap 里 `exec` 后调用
`sys.modules["api"] = make_api(rpc, b64)`。`rpc` 是闭包捕获的局部变量，
**不进入** api 源码模块的 `__globals__` —— 用户代码不可能读到 `_rpc`/帧收发原语，
从根上堵死“借 api 函数拿分发入口再造帧”这条路（`test`: `read_file.__globals__` 无
`_rpc`/`_send_frame`/`_recv_frame`）。

```python
def read_file(path: str, offset: int = 0, limit: int = 2000) -> str:
    """读取工作区文件。相对路径相对会话工作目录。"""
    return _rpc("read_file", path=path, offset=offset, limit=limit)
```

**【v2.2】宿主侧硬白名单（B2）——不依赖 agent allowlist**：`rpc.py:_ALLOWED_RPC =
{read_file, write_file, append_file, list_dir, glob, grep, web_search, save_artifact}`。
`handle()` 先验 `fn in _ALLOWED_RPC`，否则直接回 `not_allowed`。**绝不允许** `tool_execute` /
`tool_code_exec` / 任何可能再起 shell、再起沙盒、读宿主内存的工具名进名单——allowlist 表
随前端/规格变动不影响沙盒面。`_FILE_TOOLS` 把 api 名映射到 `file_tools` 实现
（`read_file→tool_read_file` 等），web_search 经 `web_search_tool`（走 SSRF，不绕过）。

**宿主侧 `_build_tool_ctx` 伪代码**（每 RPC 派发时装配工具上下文，供 `run_tool`）：

```python
def _build_tool_ctx(self) -> ToolCtx:
    # 从会话/事件队列拿审批与事件面，让 run_tool 的 NeedsPermission → 审批事件桥成立
    return ToolCtx(
        conversation_id=self.session.conversation_id,
        session_id=self.session.session_id,
        _cwd=str(self.session.workspace_dir),
        _event_queue=self.session.event_queue,
        _task_depth=self.session.task_depth,
    )
```
生产上这段逻辑就是 `graphmod/tools.py` 已有的 `_execute_tool` 特判装配；`ExecutionSession`
把同样的 ctx 传给 `RpcDispatcher`，保证沙盒内 RPC 与主 Agent 工具调用**共享审批/快照/事件面**。

写工具（write/append）成功后 `session.record_file(rel_path)` → `bound_tool_output` 的全量转储
与轮次快照逻辑照常生效。

**`save_artifact(name, data) → str`**：data 为 base64 str（bootstrap 里
`import base64; api = make_api(rpc, base64)`）。宿主侧（B6）：
- 文件名 sanitize：拒绝 `<>:"/\\|?*`、空名、`.`/`..`、路径分隔（只允许纯文件名）、`:`（NTFS ADS）。
- 大小上限 `CODEACT_MAX_ARTIFACT_SIZE_MB`（默认 10MB）超限拒绝。
- 写 `data/codeact/<session_id>/<name>`，返回相对工作区路径（不在工作区内则绝对路径）。

- **RPC 分发复用 `sub_tools.run_tool` + 工具注册表**，不硬编码。这样**免费**获得：
  - `app/utils/ssrf.py` 的出站检查（`web_search` 必须走 SSRF，不绕过）；
  - `bound_tool_output` 的截断 + 落盘续读；
  - **写工具自动进轮次快照**（`fstools/writer.py` 的 `record_write()` → 改动可 diff / 撤回）。
- 结果统一走 `bound_tool_output(str(result), f"api.{fn}")` 兜截断（宿主侧第二级）。
- RPC 层异常统一转 `{"error": {"code":..., "msg":...}}`，`_rpc` 里 `raise RuntimeError(msg)`，让模型看 traceback 自修。

## 8. 反馈层（`result.py`）

```python
@dataclass
class CodeActResult:
    ok: bool
    stdout: str           # child 侧 head+tail 预截断
    stderr: str           # child 侧 head+tail 预截断
    last_expr: str | None # repr()，需截断上限
    traceback: str | None # 完整，含行号
    files: list[str]      # 宿主侧从 RPC 日志收集（子进程不知道写成功哪些）
    error: dict | None    # {"code":..., "msg":...}
```

- **两级截断**：① child 侧 head(1000)+tail(1000) 预截断（防 IPC 爆），避免把 MB 级 print 塞进通道；
  ② 宿主侧最终仍走 `bound_tool_output`（`app/context/tool_output.py:87`，默认 200 行/32KB）兜底。
- **【修正】stdout/stderr 原始全文落盘走 `data/truncation/`**（复用既有 7 天清理 `cleanup_truncated()`），
  **不要**写 `data/generated/`（那里会被 `GeneratedFilesView` 当产物列出，污染 UI）。产物本身走 `data/codeact/<session_id>/`。
- **两层 stderr**：Python 级 `sys.stderr`→`StringIO`（用户代码），进程级 `Popen(stderr=file)`（解释器崩溃的原生输出）——崩溃兜底靠后者；进程级 stdout/stderr 落盘 `data/codeact/run/<run_id>/{stdout,stderr}.log`（v2.2，供诊断，不随 result 全量回灌）。
- 崩溃回灌走 `_sanitize_text`（B5）：脱用户目录 `C:\Users\...` 与疑似密钥，只取尾部 2000 字符。
- `Traceback` 完整保留（自我修复关键）。
- `last_expr` 的 `repr()` 设长度上限（任意对象 repr 可能巨大）；超限用 `head_tail` 标记（C3）。
- `memory_limit`：Windows 提交内存超限退出码 `0xC000012D`（`STATUS_COMMITMENT_LIMIT`）映射为
  `{"ok":false,"error":{"code":"memory_limit"}}`（C6），模型据此缩小数据规模。

回灌格式：

```
[codeact] 执行成功/失败 (3.2s)
--- last expression ---
...
--- stdout ---
...
--- stderr ---
...
--- traceback ---
...
--- files ---
...
```

## 9. 配置（并入 `app/config.py`，默认关闭）

```python
codeact_enabled: bool = False         # CODEACT_ENABLED，开才注册 tool_code_exec
codeact_timeout: float = 10.0         # CODEACT_TIMEOUT（不含审批等待）
codeact_rpc_timeout: float = 30.0     # CODEACT_RPC_TIMEOUT
codeact_max_output_chars: int = 2000  # 每侧 head+tail 预截断上限
codeact_max_code_chars: int = 20000
codeact_max_ast_nodes: int = 5000
codeact_max_memory_mb: int = 512      # Job Object PROCESS_MEMORY
codeact_max_processes: int = 16       # Job Object ACTIVE_PROCESS
codeact_max_concurrent: int = 2
codeact_max_retries: int = 3          # 由 generate.py 强制
codeact_allow_network: bool = False   # MVP 忽略；后续接出口代理白名单
codeact_artifact_dir: str = "data/codeact"   # 产物目录（独立于 data/generated）
codeact_max_artifact_size_mb: int = 10 # 【v2.2】save_artifact 单产物上限（B6）
```

## 10. 工具注册

- `graphmod/base.py:_static_tools_tail()`：`codeact_enabled` 时追加 `tool_code_exec(code, timeout?)`。
- `graphmod/tools.py:_execute_tool()`：特判 → `ExecutionSession(...).run(code)`，注入 `conversation_id / _cwd / _event_queue / _task_depth`。
- **【修正】错误自愈次数由 `graphmod/generate.py` 强制**：跟踪连续 `tool_code_exec` 失败次数，达 `CODEACT_MAX_RETRIES`
  后**不再注入 `tool_code_exec`**（等同 doom-loop 的 `DOOM_LOOP_MAX_STRIKES` 处理），并注入结构化总结提示，止住空烧。
  纯靠系统提示词约束不可靠，必须代码层兜底。
- 系统提示词（仅 `codeact_enabled` 注入）：

```
你可以编写 Python 代码，在受控执行器中运行。
- 用 api.read_file / api.write_file / api.glob / api.grep / api.web_search 访问工具。
- 不支持 os/subprocess/socket/open；文件操作必须经 api。
- 报错请读 traceback 后重写，最多 3 次。
- 成功后用自然语言总结，不要把原始数据直接给用户。
- 需要 shell/系统命令时用 tool_execute，不要用代码执行器。
```

- 子 Agent：先只挂主 Agent（explore/plan 不挂）。

## 11. 测试计划

> 注意：本机 `backend/.venv` **未安装 pytest**，Phase 1 需先补测试依赖，否则用例无法执行。

- **11.1 AST / builtins / hook**
  - 白名单导入通过；`os/subprocess/eval/__import__/open/getattr` 被拒；
  - **dunder 属性** `object.__subclasses__()` / `().__class__.__bases__` 被拒；
  - `__builtins__` 里无 `open/eval/exec`，但有 `ValueError` 与 `__build_class__`；
  - `try/except ValueError` 与 `class A: pass` 能正常跑（白名单自洽性回归）；
  - audit hook 拦截 `os.system` / `socket.socket` / `ctypes.CDLL`；
  - 白名单模块（如 `random`）内部导入 `os` 不被误杀。
- **11.2 通道**
  - 用户 `print` 与 RPC 并发：RPC 不被污染，最终 result 含完整 stdout；
  - 半包/粘包：分片发送 RPC 帧，宿主正确重组；
  - 沙盒崩溃：无 result 帧 → `sandbox_crashed`（含原生 stderr 尾部）。
- **11.3 路径**
  - `..` / 符号链接 / junction / UNC / ADS 全部被拒（**含 symlink 在 resolve 前被拦**）；
  - 读 `.env` 被拒；
  - 越权写触发 `NeedsPermission` → 审批拒绝返回结构化错误、允许落盘。
- **11.4 资源**
  - 死循环超时 → Job Object 杀干净，`Get-Process` 无残留；
  - 内存暴涨 → Job Object 限制生效，进程被杀；
  - fork bomb → `ACTIVE_PROCESS` 限制生效。
- **11.5 生命周期**
  - 审批等待中超时 → `respond(denied)` 撤销、弹窗关闭、进程被杀；
  - **审批等待不消耗执行超时**（耗时 > 10s 仍能正常审批）；
  - 并发 4 个 session → 信号量限流到 2。
- **11.6 集成**
  - mock LLM 产码 → `tool_code_exec` → 断言回灌文本结构；
  - env 不含 `LLM_API_KEY`（注入假值验证不出现在沙盒内）；
  - `api.write_file` 落盘后**轮次快照可 diff**。

## 12. 分阶段计划

- **Phase 1（本文档）**：无状态受控执行器 + 六层防护 + `Popen`/线程桥 + Job Object + `ExecutionSession` + 测试。
- **Phase 2**：有状态 Kernel（`code.InteractiveInterpreter` 子进程 + 行协议 + 空闲回收）；网络出口代理白名单；富媒体产物规范化。
- **Phase 3**：Docker/WSL2 容器运行时（gVisor/Kata 可选），替代进程级隔离；镜像内只读 FS + cgroups + 无网络。

## 13. 风险与开放问题

- **§4.0 是本设计成立的前提**：不收紧 `tool_execute` 的 `python -c`，六层等于摆设。
- AST + audit hook 不是绝对墙：仍可能被未知绕过路径突破；生产多租户必须 Phase 3。
- Windows 本地回环 socket RPC：需实测延迟与稳定性，Windows Defender 可能拦。
- Job Object 兼容性：父进程在 job 内时嵌套可能失败，需 fallback 到 `taskkill /T /F`。
- 审批-超时竞态：用现有 `respond(denied)` 处理，无需新 API。
- 提示注入：工具结果回灌前轻量清洗（去“忽略以上指令”类模式），完整方案留 Phase 2。
- 与 `tool_execute` 边界：系统提示词里必须讲清楚，否则模型会混用（并与 §4.0 的收紧配套）。

## 14. 实现者 checklist

> 状态（v2.2）：`backend/app/codeact/` 核心已落地，`codeact_check.py` 24 项 + `codeact_p0_check.py` 11 项全部通过。
> 尚未接线到 Agent（`tool_code_exec` 注册/`generate.py` 连续失败计数/`tool_execute` 收紧）。

- [x] RPC 走本地（回环 socket）+ 长度前缀帧协议（3.x/6）；帧长上限双向（D2）。
- [x] 子进程一律 `subprocess.Popen` + 后台线程桥，**禁用 `asyncio.create_subprocess_*`**（§3.3）。
- [x] 用户 stdout/stderr 进程级落盘 `data/codeact/run/<run_id>/`，级内 StringIO + 回灌两级截断（§8）。
- [x] 安全加三层：受限 builtins（自洽白名单）+ audit hook + 宿主路径根校验（§4.2-4.4）。根校验读/写分离（B4）。
- [x] AST 守卫含 **dunder 属性拦截**（§4.1）。
- [x] `PermissionManager` 撤销走 `respond(req_id,"denied")`（不新增 API）（§6）。
- [x] 执行超时**暂停于审批等待 + 所有宿主侧 RPC 等待**（`_pause_depth`，C1）（§5.3/§6）。
- [x] Windows Job Object：「先建 Job → 正常创建 → 立即 assign」+ `TerminateJobObject` 杀树 + `taskkill` fallback；**assign 失败即杀**（§5.1）。
- [x] `ExecutionSession` 统一超时/审批/取消/并发；超时钳制 B1。
- [x] 导入白名单处理“内部导入 os”冲突 + 白名单 `__import__` wrapper；`-S` + `-X pycache_prefix`（§4.2/4.6）。
- [x] 配置并入 `app/config.py`（含 `codeact_max_artifact_size_mb` B6）。
- [ ] `tool_code_exec` 注册（`graphmod/base.py:_static_tools_tail` + `graphmod/tools.py:_execute_tool` 特判）。
- [ ] `codeact_max_retries` 由 `generate.py` 强制（连续失败后停用工具，§10）。
- [ ] **§4.0 前置**：`CODEACT_ENABLED=true` 时同步收紧 `tool_execute` 的 `python/node -c`（需审批/禁止）。
- [x] 产物写 `data/codeact/`，全文转储走 `data/truncation/`（勿污染 `data/generated/`）；truncation 只读（B4）。
- [x] RPC 分发复用 `run_tool`（SSRF + `bound_tool_output` + 轮次快照自动生效）+ **硬编码白名单**（B2）。
- [x] api 函数闭包注入 `make_api(rpc, b64)`，分发原语不进 `__globals__`（B3）；`save_artifact` sanitize+上限（B6）。
- [x] 崩溃 stderr 尾部脱敏回灌 + `0xC000012D→memory_limit`（B5/C6）。
- [ ] **文档同步**：`.env.example` 增补 `codeact_*`；`AGENTS.md` 增补条目；`changed_files.md` 记录。
- [ ] 测试用例补通道/路径/资源/生命周期（并入 pytest，先补 pytest 依赖，当前为独立检查脚本 `codeact_check.py`/`codeact_p0_check.py`）。
