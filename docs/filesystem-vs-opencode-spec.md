# 后端文件系统 vs opencode 构建规格 —— 对比与执行文档

> 规格来源：`C:\tool\AGENT\opencode-构建规格.md`（下称"规格"）。
> 对比对象：`backend/app/filesystem/` 及其关联层（`app/tools/`、`app/permission/`、`app/session/`）。
> 结论先行：**模块 A 约 90% 已实现但有缺口；模块 B（快照回滚）完全缺失；模块 C/D 功能等价但架构不同。** 本文档按规格落地缺口。

---

## 1. 逐模块对比

### 1.1 模块 A：AppFileSystem 文件访问层 —— 已实现，三类缺口

规格 4.1 接口契约 vs 现状：

| 规格接口 | 状态 | 位置 |
|---|---|---|
| `readFile` (bytes) | ✅ | `FileSystem.read()` filesystem.py:60 |
| `readFileString` | ✅ | `fsutil.read_file_string()` |
| `writeFile` | ⚠️ 仅有 `writeWithDirs`，无独立写、**非原子** | fsutil.py:222 |
| `writeJson` | ✅ 存在但**非原子**（直接覆盖） | fsutil.py:241 |
| `appendFile` | ❌ fs 层缺失（仅工具层 `tool_append_file`） | writer.py:85 |
| `ensureDir` | ✅ | fsutil.py:217 |
| `writeWithDirs` | ✅ | fsutil.py:222 |
| `readDir` | ✅ `read_directory_entries()`（DirEntry{name,type}，对齐 opencode） | fsutil.py:203 |
| `remove` | ❌ fs 层缺失（仅工具层 `tool_delete_file`） | writer.py:409 |
| `exists` / `isDir` / `isFile` | ✅ | fsutil.py:176-195 |
| `stat {size, type}` | ❌ **完全缺失** | — |
| `findUp` | ✅ core.py:63 + fsutil.py:133 | |
| `glob` | ✅ search.py:85 + FileSystem.glob | |
| `globMatch` | ✅ fsutil.py:120 | |
| `globUp` | ✅ core.py:88 + fsutil.py:159 | |
| `resolve` | ✅ fsutil.py:78 | |
| `contains` / `overlaps` | ✅ 均用 `is_relative_to`，**路径边界安全** | fsutil.py:64/71 |
| `normalizePath` | ✅ core.py:27 + fsutil.py:37 | |
| `windowsPath` / `mimeType` | ✅ | fsutil.py:50/86 |

规格 4.2 关键语义 vs 现状：

| 语义 | 状态 |
|---|---|
| ① 原子写（`path.tmp-<rand>` + rename） | ❌ **未实现**（`write_with_dirs`、`_write_text_raw` 均直写） |
| ② 文件级并发锁（按规范化绝对路径信号量） | ❌ **未实现** |
| ③ 错误分类（`FileSystemError` 平台无关错误） | ❌ **未实现**（IO 错误被静默吞掉） |
| ④ 路径归一化（大小写/分隔符） | ✅ |
| ⑤ `contains` 边界判断 | ✅ |
| ⑥ `globMatch` 通配语义 | ✅ |

### 1.2 模块 B：Snapshot 快照与回滚 —— ❌ 完全缺失

规格 §5 要求：独立外部 git 仓库，`<data>/snapshot/<projectId>/<sha1(worktree)>/gitdir`，
接口 `init/track/patch/diff/diffFull/restore/revert/cleanup`，门上 `enabled() = git 项目 && snapshot≠false`。

- 全库 grep 无 `write-tree`/`read-tree`/`checkout-index` 等实现。
- `app/session` 里的"snapshot"仅为压缩刻画度的 `tail_start_id` 元数据（history.py:97），与会话状态无关，**不是文件快照**。
- 消息 part 无 `snapshot_hash` 列；`/revert` 端点只删消息不还原文件。
- **这是四大能力中最缺的一块**。

### 1.3 模块 C：Permission 权限管控 —— ✅ 功能等价，模型不同

`app/permission/manager.py` 采用**路径分类**（workspace/会话目录/external/temp/额外区/worktree/mgr.py:351）+ 命令白名单，
而非规格的 `{action, resource, effect}` Ruleset + Wildcard + `findLast` 模型。

| 规格要点 | 现状 |
|---|---|
| 默认兜底 `ask`（外部路径） | ✅ `external_default="ask"` |
| `deny` 一旦命中立即阻断 | ✅ `.git` 路径恒 deny；`.env/.db` 写恒 deny |
| ask 阻塞式状态机 + allow/deny/ask | ✅ `await_decision` + TTL 过期，等价 |
| `once`/`always`/`reject` 语义 | ✅ 等价（always 只存 allow 于 permissions.json，永不产 deny） |
| 全局 `opencode.json` 规则表 | ❌ 无配置文件 ruleset，改由声明式 `agent_specs.py` allowlist + 白名单文件 |
| 工具 `permission_name` 声明 | ❌ 无（改用 per-agent 工具 allowlist 裁剪） |

结论：结果语义对齐（allow/deny/ask + 兜底 ask + deny 优先），实现架构按本仓库既有约定保留，不在本次改动范围。

### 1.4 模块 D：会话与持久化 —— ✅ 主体实现，缺快照粘合

- 关系表：`sessions`/`session_messages`/`message_parts`（session.db）✅（规格 7.2 对应）
- blob 存储：`data/truncation/`、`data/storage/` ✅
- 上下文 epoch + 压缩基线 ✅
- **§7.4 快照粘合**（步骤前后 track、part 记录 snapshot_hash）❌
- **§8.3 文件级回滚**（revert 还原 changed files）❌ —— 目前 `/revert` 仅删消息

---

## 2. 本次执行范围（按文档落地）

> 目标：把规格模块 A 的三个缺口与模块 B 的整个快照层做出来，附测试；不改动已功能等价的模块 C/D 架构。

### 2.1 模块 A 补齐（`backend/app/filesystem/fsutil.py` + `__init__.py`）

1. `FileSystemError` —— 平台无关错误（glob 语法错等），避免上层误判文件缺失。
2. `FileStat` + `stat(path)` —— 返回 `{size, type, mtime_ns}`；不存在返回 `None`。
3. `read_file(path)` —— 字节读取（对齐 `readFile`）。
4. `write_file(path, data)` —— **原子写**：先写 `path.tmp-<rand>` 再 `os.replace`；自动建父目录。
5. `write_json(...)` 改造为**原子写**。
6. `append_file(path, data)` —— 追加（不存在则原子创建）。
7. `remove(path)` —— 文件+目录递归删除，不存在时静默成功。
8. `file_lock(path)` —— 按规范化绝对路径的**文件级可重入锁**（threading.RLock 注册表），对齐规格 4.2 ②。

### 2.2 模块 B 新建（`backend/app/snapshot/`）

新建 `app/snapshot/` 包，实现规格 §5 的 git 快照层：

- `Snapshot`（`snapshot.py`）：
  - 数据布局：`<data>/snapshot/<projectId>/<sha1(worktree)>/`，`projectId` = git 根提交 sha（`git rev-list --max-parents=0 HEAD`），worktree-hash = 工作区绝对路径 sha1。
  - `enabled()`：`(worktree/.git 存在) && not disabled`，否则全部 no-op。
  - 惰性 `init`：首次 `track` 时以 `GIT_DIR+GIT_WORK_TREE` 环境变量 `git init`，随后写入 spec §5.3 全套 config（autocrlf=false、longpaths=true、symlinks=true、fsmonitor=false、feature.manyFiles=true、index.version=4、index.threads=true、untrackedCache=true）。
  - `add()`：`syncExclude`（用户 info/exclude + 未跟踪大文件 >2MiB 写入 gitdir `info/exclude`）→ 并行 `diff-files --name-only -z` + `ls-files --others --exclude-standard -z` → 去重 → 大文件跳过列表回写 exclude → `git add --sparse .`。
  - `track()`：`git write-tree` 返回 tree hash，不产生 commit、不动 HEAD（零干扰）。
  - `patch(hash)` / `diff(hash)`（`-c core.quotepath=false`）自该 hash 的文件清单 / diff 文本。
  - `diffFull(from, to)`：`--name-status --numstat` 得 status/增减行，数 `-` 判定 binary，`git show <hash>:<file>` 取 before/after。
  - `restore(hash)`：`git read-tree <hash>` + `git checkout-index -a -f`（两命令，不碰用户 git）。
  - `revert(patches)`：按时间序 + `seen` 去重（同文件取最早 hash），批量合并 `ls-tree` + `git checkout <hash> -- <files>`，不存在者逐个删除，clash/失败回退逐文件 `single()`。
  - `cleanup()`：`git gc --prune=7.days`。
  - **并发**：每个 gitdir 一把锁（map 缓存，track/patch/diff/restore/revert/cleanup 串行）。
  - 边界：文件被删期间 stat 失败 → 跳过不中断；二进制 diff 文本空不报错；中文/空格路径 `quotepath=false`。

### 2.3 测试（`backend/tests/`）

- `test_snapshot.py`：临时 git 项目做 track→改→restore 往返；`git status/log` 不变（零干扰）；revert 对修改/删除/新建三类正确还原；同文件连改 3 次 revert 去重还原到最早；大文件排除；非 git 目录降级（track 返回 None）。
- `test_fsutil_extras.py`（或并入现有 `test_filesystem_core_fsutil.py` 的约定）：原子写无 `.tmp` 残留；`stat` 类型/大小；`remove` 递归+静默；`file_lock` 重入与互斥；`append_file`。

### 2.4 明确不做（后续项）

- §7.4 步骤级快照粘合与 part `snapshot_hash` 落库（涉及 agent 主循环，风险高、需联调，后续单独里程碑）。
- 权限模块向 Ruleset 模型迁移（当前功能等价，保持既有架构）。
- `cleanup` 定时调度（每小时）—— 模块提供接口，由调用方挂计划任务。

---

## 3. 验收清单

- [x] 规格 4.3：原子写两文件后 `writeJson` 原子替换，无 `.tmp` 残留。
- [x] 规格 4.3：`contains` 边界用例、`../` 越界用例（既有实现已覆盖，测试回归）。
- [x] 规格 5.9：track→改→restore 往返，`git status/log` 不变。
- [x] 规格 5.9：revert 三类变更、同文件连续 3 次去重、大文件排除。
- [x] 规格 5.9：`gc --prune=7.days` 后旧对象消失、新快照仍可 restore。