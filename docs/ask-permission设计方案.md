# AgentSuper 命令级 ask-permission 设计方案

版本：v1.1（评审修订版）
日期：2026-08-26
状态：已评审，待实施（v1.0 → v1.1 变更见第 10 章）
适用范围：backend（FastAPI）+ frontend（Vue3）

## 1. 背景与问题

### 1.1 现象

在 AgentSuper 中执行 tool_execute（shell 命令工具）时，非白名单命令会被直接硬拒绝：

Error: Command 'mvn' is not in the allowed whitelist

用户期望的行为（对齐 opencode / Claude Code）：遇到非白名单命令时弹出审批窗口，由用户选择「拒绝 / 允许本次 / 允许并记住」，而不是直接报错。

### 1.2 根因

权限系统存在两个独立维度，且成熟度不一致：

| 维度 | 现状 | 机制 |
| --- | --- | --- |
| 路径权限（文件读写/目录执行） | 已接入 ask-permission | mgr.check(path, op) 返回 ask 后抛 NeedsPermission 走审批流程 |
| 命令权限（tool_execute 命令本身） | 静态硬白名单 | _ALLOWED_COMMANDS frozenset 未命中直接 raise ValueError |

结论：ask-permission 模式（弹窗审批）已经设计并大部分落地，唯独「命令维度」停留在静态白名单阶段，没有接入审批链路。

## 2. 现状调研（代码证据）

### 2.1 后端权限架构（已完整存在）

| 模块 | 位置 | 说明 |
| --- | --- | --- |
| 权限管理器 | backend/app/permission/manager.py（437 行） | PermissionManager：路径分类/白名单/临时授权/审批等待 |
| 权限 API | backend/app/api/permission.py（161 行） | 6 个接口，全部 require_admin |
| Agent 审批集成 | backend/app/agent/graph.py:688-721 | except NeedsPermission 后 create_request -> 事件队列 -> await_decision |
| 文件工具接入 | backend/app/tools/file_tools.py:1181-1203 | 写重定向目标已走 mgr.check(...,"write")，可抛 NeedsPermission |
| 前端弹窗 | frontend/src/components/PermissionDialog.vue | 三按钮：拒绝 / 允许本次 / 允许并记住 |
| 前端轮询 store | frontend/src/stores/permission.ts | 轮询 /api/permission/pending |

### 2.4 关键差距点：命令白名单是硬拒绝（本方案要改造的核心）

backend/app/tools/file_tools.py：

    # 1108-1128：硬编码白名单
    _ALLOWED_COMMANDS = frozenset({
        "python", "node", "npm", "git", "curl", ...   # 95 个（1108-1128）
    })

    # 1246-1263：校验函数 —— 未命中直接 ValueError（硬拒绝）
    def _check_single_allowed(base_cmd, cwd=None):
        if base_cmd.lower() in _ALLOWED_COMMANDS:
            return
        if "/" in base_cmd or "\\" in base_cmd:
            # 路径形式命令：解析到工作区内真实文件则放行
            ...
        raise ValueError(f"Command '{base_cmd}' is not in the allowed whitelist")  # mvn 报错源头

两个执行入口调用链：

| 入口 | 位置 | 白名单校验处 | 异常处理 |
| --- | --- | --- | --- |
| 同步 tool_execute | file_tools.py 工具函数 | _check_command_allowed -> _validate_shell_command -> _check_single_allowed | ValueError 转为错误文本 |
| 流式 _execute_tool_streaming | graph.py:726-765 | 先 mgr.check(cwd,"execute")（目录维度可 ask）；再 _validate_shell_command（命令维度硬拒） | ValueError 返回错误文本（graph.py:755-756）；NeedsPermission 自然上抛 |

重要发现：NeedsPermission 并非 ValueError 子类，所以只要让 _check_single_allowed 在 ask 模式下抛 NeedsPermission，流式入口的异常会自然穿透到 graph.py:688 的通用审批流程，几乎零改动。

## 3. 需求目标

1. 非白名单命令执行时弹出审批窗口（对齐路径权限行为）；
2. 支持「拒绝 / 允许本次 / 允许并记住」三态；
3. 「允许并记住」持久化到独立文件 data/command_permissions.json，重启仍生效；
4. 保留全部安全红线：命令黑名单、SSRF 校验、危险模式校验、解释器注入防护永不跳过；
5. 兼容现有调用：未启用 ask 的调用方（如其他工具内部调用）保持原 ValueError 行为。

## 4. 方案设计

### 4.1 总体架构

    tool_execute(命令 C)
      |
      +- 分段解析（保留）：_split_shell_segments / 反引号递归
      |
      +- 黑名单 + SSRF（保留，永不跳过）
      |
      +- 每段首命令 -> 新 check_command(base)
      |     +- 命中静态白名单 _ALLOWED_COMMANDS        -> 放行
      |     +- 路径形式且工作区内存在真实文件           -> 放行
      |     +- 命中持久化命令白名单 command_whitelist   -> 放行
      |     +- 命中临时授权（TTL 300s）                 -> 放行
      |     +- 未命中 -> external_default
      |           +- "allow" -> 放行
      |           +- "deny"  -> 拒绝（当前行为）
      |           +- "ask"   -> 抛 NeedsPermission(base, "command", "tool_execute", {...})
      |                          -> graph.py 通用审批 -> 前端弹窗
      |                          -> 允许本次 -> add_temp_command_approval(base)
      |                          -> 允许并记住 -> 写入 command_permissions.json
      |                          -> 重试执行

### 4.2 后端改造

#### 4.2.1 backend/app/permission/manager.py（核心，约 +60 行）

新增持久化：
- command_whitelist_path：默认 data/command_permissions.json（与路径白名单 permissions.json 分离，便于单独管理与审计）；
- _load_command_whitelist() / _save_command_whitelist()：JSON 结构 {"allowed_commands": ["mvn", ...]}，命令统一小写规范化存储。

新增方法：

    def check_command(self, base_cmd: str) -> str:
        """命令级权限检查，返回 "allow" / "ask" / "deny"。
        1. 静态白名单与路径形式已在 file_tools 校验，此处不重复；
        2. 命中持久化 command_whitelist -> "allow"；
        3. 命中临时命令授权（TTL 内）-> "allow"；
        4. 否则返回 self.external_default。"""
        key = base_cmd.lower()
        if key in self._command_whitelist:
            return "allow"
        if self._has_temp_command_approval(key):
            return "allow"
        return self.external_default

    def add_temp_command_approval(self, base_cmd: str) -> None:
        """允许本次：写入临时授权（复用 _temp_approvals，TTL 300s，LRU 上限 1000）。"""

    def _temp_command_key(self, base_cmd: str) -> str:
        """命令临时授权 key 规范化，与路径授权同表存储时加前缀区分，如 "cmd:mvn"。
        建议独立 OrderedDict 存储，避免污染路径授权。"""

respond() 分支扩展（remember=True 时）：

    if req.operation == "command":
        key = _extract_base_command(req.tool_args.get("command", req.path))
        if key:
            self._command_whitelist.add(key.lower())
            self._save_command_whitelist()

_extract_base_command 可复用 file_tools._first_command（shlex 解析首 token），避免重复实现。

create_request() 去重键 _pending_by_key[(resolved_path, operation)] 对命令类型同样适用（path 槽位存命令签名），无需改动。

#### 4.2.2 backend/app/tools/file_tools.py（约 +15 行，关键透传点）

目标：让命令校验在 ask 模式下抛 NeedsPermission 而非 ValueError。

改动 1：_check_single_allowed 增加 ask 参数（file_tools.py:1246）

    def _check_single_allowed(base_cmd: str, cwd: str | None = None, ask: bool = False) -> None:
        # 1) 静态白名单命中 -> 放行（不变）
        if base_cmd.lower() in _ALLOWED_COMMANDS:
            return
        # 2) 路径形式且工作区内存在真实文件 -> 放行（不变）
        if "/" in base_cmd or "\\" in base_cmd:
            ...（原逻辑不变）
        # 3) 新增：ask 模式下查询管理器（持久化白名单 + 临时授权）
        if ask:
            from app.permission import get_manager as _get_perm_mgr
            decision = _get_perm_mgr().check_command(base_cmd)
            if decision == "allow":
                return                      # 已记住 / 本次临时授权命中
            if decision == "deny":
                raise ValueError(f"Command '{base_cmd}' is not in the allowed whitelist")
            # decision == "ask"：落入下方 NeedsPermission
            raise NeedsPermission(
                base_cmd, "command", "tool_execute",
                {"command": 当前完整命令}   # 由上层 _validate_shell_command 注入
            )
        # 4) 非 ask 模式：保持原 ValueError（兼容旧调用方，零回归）
        raise ValueError(f"Command '{base_cmd}' is not in the allowed whitelist")

    说明：check_command 会命中「持久化命令白名单」与「临时命令授权」返回 allow，
    因此审批通过后的重试（见 4.2.3）不会再抛异常，不会死循环。

改动 2：_validate_shell_command 增加 ask 参数并透传（file_tools.py:1266-1285）

    def _validate_shell_command(command: str, cwd: str | None = None, ask: bool = False) -> None:
        for inner in _backtick_bodies(command):
            _validate_shell_command(inner, cwd, ask=ask)      # 递归透传
        segments = _split_shell_segments(command)
        ...
        for seg in segments:
            base = _first_command(seg)
            ...（环境前缀跳过逻辑不变）
            _check_single_allowed(base, cwd, ask=ask)          # 透传
            ...（黑名单 + SSRF 校验不变，永远执行）

    完整命令注入：_check_single_allowed 抛 NeedsPermission 前需要携带完整命令，
    由 _validate_shell_command 捕获 ValueError 场景的补充做法：
    可在 _check_single_allowed 的 ask 分支抛出的 NeedsPermission 中只放 base_cmd
    （path 槽位），graph.py 审批流使用的 tool_args 取自 _execute_tool 收到的完整
    参数 args（graph.py:690），前端展示与请求内容不受影响，无需额外注入。

改动 3：同步 tool_execute 打开 ask（file_tools.py:1610）

    try:
        _validate_shell_command(command, cwd=resolved_cwd, ask=True)   # 原无 ask=True
    except ValueError as e:
        return _env("execute", f"Error: {e}", error=True)

    效果：mvn 等未白名单命令抛 NeedsPermission（NeedsPermission 不是 ValueError 子类，
    graph.py:722 的 except Exception 也不会吞掉 NeedsPermission？——注意：graph.py
    中 NeedsPermission 在 688 行先于 722 的 except Exception 捕获，顺序正确，见 4.2.3）。

改动 4：兼容入口 _check_command_allowed（file_tools.py:1366）保持 ask=False 默认，
供其他内部调用方使用，行为与现在完全一致。

#### 4.2.3 backend/app/agent/graph.py（约 +5 行）

改动 1：审批通过后的临时授权按操作类型分流（graph.py:707-708）

    if decision == "allowed":
        if e.operation == "command":
            mgr.add_temp_command_approval(e.path)   # e.path 即 base_cmd（如 "mvn"）
        else:
            mgr.add_temp_approval(e.path)

    之后 709-715 的流式重试与 716-719 的同步回退无需改动：
    重试时 _validate_shell_command(ask=True) -> check_command 命中临时授权 -> 放行。

改动 2：流式入口打开 ask（graph.py:753-756）

    try:
        _validate_shell_command(command, cwd=str(resolved_cwd), ask=True)   # 原无 ask=True
    except ValueError as e:
        return f"Error: {e}"

    NeedsPermission 不是 ValueError 子类，不会被此 except 捕获，会自然上抛到
    graph.py:688 的通用审批流程（已在 4.2.1 设计时验证该捕获顺序：688 行 except
    NeedsPermission 位于 722 行 except Exception 之前，NeedsPermission 必然先被处理）。

改动 3（无需改动，确认即可）：_execute_tool 中 create_request(e.path, e.operation, name, args)
（graph.py:690）——operation 为任意字符串，命令类型 "command" 直接复用；
_pending_by_key[(resolved_path, operation)] 去重键同样适用（resolved_path 槽位存 base_cmd）。

#### 4.2.4 配置项（.env.example 补充，约 +5 行）

现有 external_default 已支持三态（manager.py:100，默认 "ask"，非法值回退 "ask"）：

    # 外部（未授权）资源默认策略：ask=弹窗审批 / allow=放行 / deny=拒绝
    PERMISSION_EXTERNAL_DEFAULT=ask

    说明：manager 初始化已从环境读取该值（main.py 注入），命令维度直接复用同一配置，
    无需新增开关；生产环境建议 PERMISSION_EXTERNAL_DEFAULT=deny（保持现状硬拒绝）。

新增可选配置（不设则用默认路径）：

    # 命令白名单持久化文件（默认与路径白名单同目录，文件名 command_permissions.json）
    COMMAND_PERMISSIONS_PATH=

### 4.3 前端改造（PermissionDialog.vue + permission.ts）

原理：operation 是自由字符串，审批轮询/响应链路（/api/permission/pending、respond）
对 operation 无枚举校验，后端无需为前端加接口。前端只需做展示适配：

1. PermissionDialog.vue 增加 command 分支（约 +10 行）：

   - operation === "command" 时：
     - 标题文案显示「执行命令」；
     - 主展示区不再显示路径，改为显示 tool_args.command 中的完整命令
       （用 code 样式，命令可能较长，加横向滚动或换行）；
     - 说明文字：「该命令不在白名单中，是否允许执行？」
   - 按钮三态不变：拒绝 / 允许本次 / 允许并记住。

2. stores/permission.ts：无需改动（pending 轮询、respond 参数透传已通用）。

3. frontend/src/types（可选）：PermissionRequest 的 tool_args 已是 Record<string, unknown>，
   如需强类型可补 command?: string 字段，非必需。

### 4.4 安全边界（红线，永不因 ask 放松）

1. 静态命令黑名单（_DANGEROUS_PATTERNS：os.system/subprocess/eval/urllib/Invoke-Expression 等）
   仍在 ask 之前执行，命中即拒绝——弹窗只决定「命令能否跑」，不决定「命令怎么跑」；
2. SSRF 校验（URL/主机解析）不放松；
3. 反引号与 $(...) 内层命令递归校验，且 ask 参数透传（内层也弹窗）；
4. 分段校验不放松（cat x | evil 中 evil 段同样弹窗）；
5. external_default=deny 时命令维度行为与现状完全一致（零回归）；
6. 「允许并记住」只记录 base 命令（如 mvn），不记录完整参数（避免参数含敏感信息落盘）；
7. 命令白名单独立文件存储（command_permissions.json），与路径白名单分离，便于审计；
8. 临时命令授权复用 _TEMP_APPROVAL_TTL=300s、_MAX_TEMP_APPROVALS=1000（LRU 淘汰）；
9. 审批超时沿用 approval_timeout（默认 3600s）；无事件队列时直接拒绝不卡死（graph.py:701-705）；
10. 命令执行前的目录权限检查（mgr.check(cwd, "execute")）不受影响，命令仍限工作目录语义。

## 5. 涉及文件与工作量

| # | 文件 | 改动 | 估算行数 |
| --- | --- | --- | --- |
| 1 | backend/app/permission/manager.py | 新增 check_command / add_temp_command_approval / respond 命令分支 / command_permissions.json 持久化 | +30 |
| 2 | backend/app/tools/file_tools.py | _check_single_allowed 增加 ask 参数三态分流；_validate_shell_command 递归透传；两个执行入口打开 ask | +25 |
| 3 | backend/app/agent/graph.py | 审批通过后按 operation 分流命令临时授权（约 5 行）；流式入口打开 ask | +8 |
| 4 | frontend/src/components/PermissionDialog.vue | operation==='command' 展示分支 | +10 |
| 5 | backend/.env.example + backend/README.md | 新增配置项说明 | +5 |

合计：5 个文件，净增约 75–80 行，改动集中、无重构。

可选（不阻塞）：frontend/src/types/index.ts 补 command 类型字段（+2 行）。

## 6. 实施步骤

按依赖顺序，每步独立可验证：

1. 步骤 1：manager.py —— 先加 check_command 与命令白名单持久化（纯新增，不触碰现有路径逻辑）。
   验证：python -m compileall backend/app/permission；现有审批用例回归通过。
2. 步骤 2：file_tools.py —— _check_single_allowed 加 ask 参数；先保持两个入口传 ask=False（行为零变化），
   验证：mvn 仍报原有白名单错误，路径审批行为不变。
3. 步骤 3：graph.py —— 审批通过后的命令临时授权分流；create_request 无需改动（NeedsPermission 已带 operation 字段）。
4. 步骤 4：file_tools.py 两个执行入口改为 ask=True（打开命令级弹窗）；流式入口同步打开。
   验证：mvn 不再直接报错，而是抛 NeedsPermission 进入审批。
5. 步骤 5：前端 PermissionDialog.vue 增加 command 展示分支。
   验证：手动执行非白名单命令，弹窗展示完整命令；三按钮分别验证。
6. 步骤 6：文档与配置说明（.env.example / README）。
7. 步骤 7：全量回归（见第 7 章）。

实施期间后端 --reload 会自动重载；前端需重新 build（或 dev server 热更新）。

## 7. 验证计划

前提：后端 --reload 运行中，前端已 build。

| # | 用例 | 操作 | 预期 |
| --- | --- | --- | --- |
| 1 | 白名单命令不弹窗 | tool_execute: git status | 直接执行，无弹窗 |
| 2 | 非白名单命令弹窗 | tool_execute: mvn -v | 前端弹窗，展示完整命令 mvn -v |
| 3 | 允许本次 | 弹窗点「允许本次」 | 命令执行一次；第二次执行 mvn 再次弹窗（不记住） |
| 4 | 允许并记住 | 点「允许并记住」 | 命令执行；重启后端后仍直接执行（持久化 command_permissions.json） |
| 5 | 拒绝 | 点「拒绝」 | 不执行，工具返回拒绝原因文本 |
| 6 | 审批超时 | 不响应弹窗，等待 approval_timeout | 工具侧返回超时错误，不卡死（默认 3600s，测试可临时调小） |
| 7 | 注入防护不放松 | tool_execute: python -c "import os; os.system('x')" | 弹窗出现前即被黑名单拒绝（不弹窗）；本用例成立依赖 P0-1 检查顺序修正（黑名单先于白名单/弹窗） |
| 8 | 反引号内层弹窗 | tool_execute: echo \`mvn -v\` | 内层 mvn 触发弹窗；外层 echo 通过 |
| 9 | 分段校验不放松 | tool_execute: git status && mvn -v | git 段直接执行，mvn 段弹窗 |
| 10 | 记住不落参数 | 允许并记住 mvn -v 后检查 command_permissions.json | 文件仅含 mvn，不含 -v |
| 11 | 零回归 | 执行既有文件读写审批（如写 data/ 下文件） | 路径弹窗行为与改造前完全一致 |
| 12 | external_default=deny | 临时设 deny 重启 | 命令维度回到硬拒绝，与现状一致 |

## 8. 风险与回退

| 风险 | 影响 | 缓解/回退 |
| --- | --- | --- |
| 命令弹窗被滥用导致放行危险命令 | 安全 | 黑名单/SSRF/递归校验在 ask 之前执行；external_default 生产建议 deny；记住只存 base 命令 |
| 弹窗无人响应导致任务挂起 | 可用性 | 超时默认 3600s；无事件队列直接拒绝（graph.py:701-705 已有兜底） |
| ask=True 打开后交互变多 | 体验 | 白名单命令不弹窗；记住机制减少重复弹窗；可按需调 external_default |
| 临时授权 key 与路径 key 冲突 | 正确性 | 命令临时授权使用独立命名空间（prefix:cmd:base），不与路径 key 混用 |
| 前端 build 失败 | 交付 | 前端改动仅展示分支（+10 行），可回退到 build 前版本；后端独立生效 |

回退总原则：本方案全部为增量改动，无数据迁移；恢复 baseline 提交（984d3b9）或撤销步骤 4 的 ask=True 即可整体回退。

## 9. 附录：代码定位索引（改造锚点）

| 位置 | 内容 |
| --- | --- |
| file_tools.py:1108-1128 | _ALLOWED_COMMANDS 硬编码白名单 |
| file_tools.py:1246-1263 | _check_single_allowed（硬拒绝源头） |
| file_tools.py:1266-1281 | _validate_shell_command 分段校验结构 |
| file_tools.py:1596-1624 | 同步 tool_execute 入口（改 ask=True 处） |
| file_tools.py:1181-1203 | 写重定向目标走 mgr.check(...,"write")（参照实现） |
| graph.py:688-721 | 通用审批流程（create_request -> await_decision） |
| graph.py:726-765 | 流式执行入口（同步打开 ask） |
| graph.py:701-705 | 无事件队列直接拒绝兜底 |
| manager.py | PermissionManager（external_default 默认 ask；_temp_approvals TTL 300s/上限 1000；permissions.json 持久化） |
| frontend/src/components/PermissionDialog.vue | 弹窗三按钮（加 command 展示分支处） |
| frontend/src/stores/permission.ts | pending 轮询（无需改动） |

（文档完，v1.0 草案，待评审）
�

| 位置 | 内容 |
| --- | --- |
| file_tools.py:1108-1128 | _ALLOWED_COMMANDS 硬编码白名单 |
| file_tools.py:1246-1263 | _check_single_allowed（硬拒绝源头） |
| file_tools.py:1266-1281 | _validate_shell_command 分段校验结构 |
| file_tools.py:1596-1624 | 同步 tool_execute 入口（改 ask=True 处） |
| file_tools.py:1181-1203 | 写重定向目标走 mgr.check(...,"write")（参照实现） |
| graph.py:688-721 | 通用审批流程（create_request -> await_decision） |
| graph.py:726-765 | 流式执行入口（同步打开 ask） |
| graph.py:701-705 | 无事件队列直接拒绝兜底 |
| manager.py | PermissionManager（external_default 默认 ask；_temp_approvals TTL 300s/上限 1000；permissions.json 持久化） |
| frontend/src/components/PermissionDialog.vue | 弹窗三按钮（加 command 展示分支处） |
| frontend/src/stores/permission.ts | pending 轮询（无需改动） |

（文档完，v1.0 草案，待评审）
