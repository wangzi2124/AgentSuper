# changed_files.md — CodeAct v2.1.1 (P0 + key P1) 落地记录

> 会话：CodeAct 受控执行器 Phase 1 代码评审优化落地（2026-10-10）。
> 范围：全部 P0（B1-B6）+ 关键 P1（C1/C3/C6/C7/C8）+ P2 quick wins（D2/D4）。

## 文件变更

### 改动
- `backend/app/config.py`
  - 新增 `codeact_max_artifact_size_mb: int = 10`（B6：save_artifact 单产物上限）。
- `backend/app/codeact/session.py`
  - `_spawn`（P0/A）：弃 `CREATE_SUSPENDED`，改「先建 Job → Popen 正常创建 → 立即 assign」；
    **assign 失败立即 kill + 置 `spawn_failed` 失败结果**，不再只 warning。
  - `_spawn` 追加 `-S`（C7，禁 site-packages）+ `-X pycache_prefix=<run_dir>/pycache`（D4）。
  - `__init__`：读/写根分离（`read_roots` 含 `data/truncation`，B4；`write_roots` 不含）；
    `_ready` Event（P0/A 就绪门）；`_pause_depth` 深度合并（C1，宿主侧任何等待暂停执行计时）。
  - `_on_client`（P0/A/C1/C6/B5）：`ready` 帧 set `_ready`；RPC 派发前 `wait_for(ready, rpc_timeout)`；
    每次 RPC 外围 `on_approval_begin/end`（C1）；连接断开走 `_error_crash_result`。
  - `_error_crash_result`（C6/B5）：Windows 提交内存超限 `0xC000012D` → `memory_limit`；
    stderr.log 尾部取 4000B、`_sanitize_text` 脱敏、回灌 ≤2000 字。
  - `_read_frame`（D2）：拒绝 declared size > `_MAX_FRAME_BYTES`（16MB），防撑爆宿主内存。
  - `_write_config`：`build_sdk_source` → `build_api_source`。
- `backend/app/codeact/rpc.py`
  - `_ALLOWED_RPC` 硬编码白名单（B2），`handle()` 入口硬拒绝非白名单名，**独立于 agent allowlist**。
  - `_file_rpc` 读/写根分离传递（B4）。
  - `_save_artifact`（B6）：文件名 sanitize（非法字符/`.` `..`/路径分隔/ADS 冒号）+ 大小上限 +
    返回相对工作区路径（不在工作区则绝对路径）。
- `backend/app/codeact/sdk_template.py`
  - `build_sdk_source` → `build_api_source`；`make_api(rpc, _b64)` 工厂（B3），api 函数闭包注入，
    `_rpc`/帧收发不进 `__globals__`。
- `backend/app/codeact/bootstrap.py`
  - （此前已落地）`_head_tail`（C3）、`sock.settimeout(None)`、`__main__` 重抛 SystemExit；
    构建 api 模块 + 装 audit hook 后 **send ready**（P0/A）。
- `docs/codeact-sandbox-design.md`
  - 标题/状态 → v2.2「Phase 1 已实现，默认关闭」；新增 v2.2 修订要点 11 条；
    更新 §0 定位、§1.2、§3.2（ready 帧 + 双向帧长上限）、§4.4（读/写根分离 + 硬白名单）、
    §4.6（`-S` + pycache_prefix + stdin=DEVNULL）、§5.1（弃 CREATE_SUSPENDED、assign 失败即杀、
    Phase 2 CreateProcessW 备注）、§6（ready 门 + C1 + B5/C6 + B1 超时钳制）、§7（闭包注入 + 硬白名单
    + `_build_tool_ctx` 伪代码 + save_artifact 白名单化）、§8（run 目录落盘 + 脱敏 + memory_limit）、
    §9（新增 `codeact_max_artifact_size_mb`）、§14 checklist（勾选已落地项、标注未接线项）。
- `backend/config.example.json`
  - 新增 CodeAct 配置段（`CODEACT_ENABLED`~`CODEACT_MAX_ARTIFACT_SIZE_MB` 全部 `codeact_*`）。
- `AGENTS.md`
  - 新增 CodeAct 条目（核心架构 + 关键约束 + 验证脚本 + 尚未接线风险）。

### 验证
- `codeact_check.py`（临时目录，24 项）：全部 PASS（AST/frame/result/exec 全链路含超时杀树）。
- `codeact_p0_check.py`（临时目录，11 项）：全部 PASS（B2 白名单+运行时拒绝 / B3 闭包 / B4 根分离
  读写 / B6 文件名+大小 / C1 RPC 等待不计时）。

### 尚未接线（后续任务）
- `tool_code_exec` 注册（`graphmod/base.py:_static_tools_tail` + `graphmod/tools.py:_execute_tool`）。
- `codeact_max_retries` 由 `graphmod/generate.py` 强制（连续失败停用工具）。
- **§4.0 前置**：`CODEACT_ENABLED=true` 时同步收紧 `tool_execute` 的 `python/node -c`。

---

## 接线完成（v2.2 收尾，2026-10-12）

> 上述「尚未接线」三条已全部落地并通过端到端验证。设计清单见 `docs/codeact-sandbox-design.md` §14（全部勾选）。

### 新增改动（本轮）
- `backend/app/agent/graphmod/constants.py`
  - 新增 `CODEACT_DISABLED_PROMPT`（§10 停用提示）。
- `backend/app/agent/graphmod/base.py`
  - `_build_tool_defs` 末尾阶段过滤：`codeact_enabled` 且会话失败数 ≥ `codeact_max_retries` 时从 schema 摘除 `tool_code_exec`（其余工具照常）。
- `backend/app/agent/graphmod/generate.py`
  - `_generate_impl` 主循环：会话失败数越限 → 注入 `CODEACT_DISABLED_PROMPT` + 重建 `tool_defs`（只注入一次）。
- `backend/app/agent/graphmod/tools.py`
  - `_tool_code_exec`（既有）：会话级连续失败累加/清零，达上限返回停用错误信息（执行级兜底）。
- `backend/tests/test_codeact.py`
  - +3 项：schema 摘除（≥上限）／保留（<上限）／提示词常量；合计 34 项。
- `docs/codeact-sandbox-design.md`
  - §10 接线描述与实现对齐；§11 测试计划标注已并入 pytest；§14 checklist 全部勾选并注明实际位置。

### 验证
- 单测：`pytest tests/test_codeact.py` → **34 passed**。
- 端到端（`deepseek/deepseek-v4-flash`，会话 `ses_209frh5jvpa8284c8`）：
  - 正向：fib(100)+1..300 质数计数落盘 `codeact_result.txt`，数值独立复算一致；沙盒痕迹
    `data/codeact/run/<run_id>/`（隔离 pycache 证明 `-S`+`-X pycache_prefix` 生效）。
  - 负向：`import os` → `ast_rejected`（0.0s，未 spawn），白名单随错误返回。