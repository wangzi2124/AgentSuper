# AgentSuper 代码审查报告

> 审查范围：`backend/main.py` + `app/runtime.py` + `app/config.py` + `app/permission/` + `app/agent/`（base/bus/graph/rag_wrapper/supervisor/web_search/code/memory/tools）+ `app/rag/`（9 文件）+ `app/context/`（6 文件）+ `app/api/`（全部路由）+ `app/session/` + `app/skills/` + `app/plugins/` + `app/monitor.py` + `app/middleware/`。
>
> 级别定义：**P0** 安全/正确性严重 · **P1** 高 · **P2** 中 · **P3** 低。
>
> 本报告对初稿中的原始疑点做了逐条核实，部分原判为误报/夸大，已在下文标注「已核实 / 已排除」及修正结论。

---

## 一、P0 — 严重（安全 / 正确性）

### 1.1 [已核实·修正] `tool_grep` / `tool_glob` 绕过单文件敏感路径检查（可泄露 `.env` / `.db`）

`app/tools/filesystem.py`

- `tool_grep`（:247）与 `tool_glob`（:194）只对**搜索根目录**做一次 `_ensure_safe(root, "read")`（filesystem.py:254/:197），随后遍历 `root_path.glob("**/*")` **逐个读取文件内容而不做 `_is_critical_read` 检查**。
- 对比：`tool_read_file`（:101）走 `_ensure_safe(target, "read")` → `check()` → `_is_critical_read`（manager.py:179-193）会拦截 `.env` / `*.db` / `permissions.json`。
- **攻击路径**：`tool_grep(pattern="API_KEY", root="backend")` 可匹配并回传 `.env` 全文；`tool_grep` 同样能读取 `data/session.db` 等敏感内容。LLM 若被诱导（prompt 注入）即构成凭据泄露。
- 原始疑点 P0-1 所述「symlink 绕过」经核实**已被缓解**：`filesystem._resolve`/`_ensure_safe` 与 `classify_path` 均调用 `Path.resolve()`（filesystem.py:41,46；manager.py:150），外部 symlink 会被解析出工作区从而降级为 external/system。真正的问题在 grep/glob 的逐文件扫描。

**建议**：`tool_grep`/`tool_glob` 对每个命中的文件调用 `mgr.check(str(f), "read")`，`deny` 的跳过（不输出、不报错）。

### 1.2 [已核实] 管理端点鉴权名存实亡 + CORS 全放开

`backend/main.py:52-55` + `app/api/deps.py:6-20` + `app/api/permission.py:71` + `app/api/plugins.py:43`

- `CORSMiddleware allow_origins=["*"]`，而多个管理端点（`respond`、`call_plugin_function`、`config/summarization` POST、`workspaces` 增删）在 **`ADMIN_TOKEN` 未配置时直接放行**（deps.py 默认空串即通过）。
- 服务按 `--host 0.0.0.0` 绑定局域网，`allow_credentials=False` 不构成缓解——这些端点不用 cookie。
- `respond`/`workspaces` 确证会重建 Agent 提示词 / 写白名单 / 新增可写工作区，属敏感操作。

**建议**：deps.py 在 `ADMIN_TOKEN` 为空时对写端点默认拒绝或至少要求本机来源；CORS 收紧为具体源。

### 1.3 [已核实] `_get_legacy_conversation` 跨用户数据泄漏（IDOR）

`app/api/chat.py:300-318`

- 查询 `conversations WHERE id = ?` **不带 `user_id` 过滤**，且 `get_conversation` 在 session.db 未命中时直接回退到它（chat.py:869-871）。
- 对比同文件旧库路径 `delete_message`/`update_conversation`/`_migrate_conversation` 均先 `_check_ownership`（:753/:910/:263），唯独此漏。
- 后果：任何 `X-User-Id` 方只要知道 conversation_id 就能读他人**未迁移旧库**全文。

### 1.4 [已核实] session.db 串行承诺不成立：seq 竞态 + 多写路径

`app/session/repository.py:235-240` + `app/api/chat.py:445-684`

- `_next_seq` 用独立连接的 `SELECT MAX(seq)+1`，非事务/非锁，同一会话并发写两个连接可算出相同 seq → `PRIMARY KEY(session_id, seq)` IntegrityError。
- `/multi-agent` 与 `/multi-agent/stream` **不经过 SessionCoordinator**，`_persist_multi_agent` 直接对父会话 `append_message`；`service.compact/revert/fork` 也绕过协调器。仅 `/stream` 路径串行 → "per-session 串行"并非全局保证，可消息交错。

### 1.5 [已核实] 并发写入共享 `BM25Index` 无锁

`app/rag/bm25_index.py:35-53` + `app/services/task_manager.py:142-144` + `app/api/documents.py:153-159`

- 上传/删除走 `asyncio.create_task` + `run_in_executor` 并发改写 `self.documents`/`self._tokenized` 并重建 `self.bm25`，列表扩展与重建两步间可交叉 → 索引与内容错位/丢块。

---

## 二、P1 — 高（逻辑缺陷）

### 2.1 三套存储无跨库事务，中途失败产生孤儿数据

`app/services/task_manager.py:112-145`

- 上传依次写 ChromaDB（分 5000/批，`vector_store.add`）→ SQLite chapters → 内存 BM25。任一中间步失败，前序数据成孤儿（"有章节元数据无向量块"等），删除流程也清不干净。
- `vector_store.add` 分批无回滚：第 N 批失败时前 N-1 批已提交可被检索。

### 2.2 检索子链路无异常隔离，单点抖动打挂整条链路

`app/rag/retriever.py:106-135` + `app/rag/reranker.py:72-90`

- `invoke()` 中 `embed_query`/`similarity_search` 任一异常会同时杀死 BM25 路径，无降级到"仅 BM25 / 空上下文"。
- `rerank()` 无 `try/except`，`predict` 抛错经 `graph.py:242 asyncio.to_thread` 直接杀死当前对话轮。
- `reranker.__init__`（:21）无条件加载模型，下载失败抛 `RuntimeError` 无降级；`embeddings.py` 下载同样无重试/超时且是必需组件 → 临时网络故障=启动失败。

### 2.3 模型加载/下载"全有或全无"

- `app/rag/reranker.py:51-70`、`app/rag/embeddings.py:46-64`、`app/utils/model_download.py:27-44`：下载无重试、无超时，任何异常直接 `RuntimeError`。虽然有 `ENABLE_RERANKER=false` 逃生阀，但漏配即整体不可用。

### 2.4 跨用户隔离承诺未全面落实

- `app/agent/` 与 `session/` 之外：`app/api/documents.py:36-159` 上传/列表/删除**无 X-User-Id 隔离**（任意客户端可删任意 doc）。
- `app/session/service.py:234-238`：`if session.user_id and session.user_id != user_id`——`user_id` 为空串时跳过校验（防御性弱点）。
- `app/api/permission.py:51-68`：`list_pending` 未加 `require_admin`（同文件 `respond` 有），待审批 `tool_args` 可能泄露路径。
- `app/api/skills.py:24-31`：`toggle_skill` 无 `require_admin`，而 `plugins.py:43` 有——不一致。
- `X-User-Id` 由客户端自报、无签名（单用户本地工具可接受，多用户即 P1）。

### 2.5 `rag_wrapper` 子 Agent 的 `retrieve`/`generate` 丢失 `files`/`model`/`history`

`app/agent/rag_wrapper.py:75-104`

- 仅 `chat` action 传递 `files`（:60）；`retrieve`/`generate` 只传 `question`，不传 `files`/`model`/`history`/`conversation_id`。supervisor 分解后子任务无法读取用户上传的文件。

### 2.6 fork 两个边界错误

`app/session/service.py:121-129`

1. `message_id` 指向最后一条消息时：`copied == len(source)` 成立 → 误抛 `MessageNotFound`（fork 到末条失败）。
2. `message_id` 不存在时：先 `create` 子会话并复制完全部父消息，**然后才抛异常** → 留下满载消息的孤儿子会话。

### 2.7 插件加载 = 任意代码执行，且目录可被 Agent 写

`app/plugins/loader.py:71`（`exec_module`）+ `app/api/deps.py` 缺省放行

- plugins 目录内全部 `.py` 无条件执行；`toggle` 触发 `load_all`。若「工作目录」面板把 `backend/plugins/` 加入可写工作区，存在"写 .py → toggle → RCE"升级链。

### 2.8 revert 不处理子会话/待执行输入

`app/session/repository.py:355-412`

- revert 只删消息+parts、回滚 epoch；**未级联** `kind='task'` 子会话（task_bridge 的 future 照常存活）和 `session_inputs`（已入队输入随后被 promote 追加 → 被撤销内容复活）。

### 2.9 `coordinator.run` 持锁 await 潜在死锁

`app/session/coordinator.py:45-51` + `:131-134`

- `run` 在 `async with self._lock` 内 `await entry.done`（join 运行中会话），而 `_drive` 的 `finally` 释放需同一把锁 → 互等死锁。当前无路由调用 `service.run`，属潜伏缺陷。

---

## 三、P2 — 中（设计 / 健壮性）

### 3.1 `bus.py` 异常以裸异常回传、错误上下文丢失 [已核实]

`app/agent/bus.py:108,236`

- `send`（type="error"）与 `run_agent` 崩溃分支均 `fut.set_exception(...)`，调用方（supervisor）收到裸 `RuntimeError`/原始异常而非 `AgentMessage(type="error")`，丢失子 Agent 的已完成步骤上下文。
- **原判 P0-3 修正**：Future 泄漏是**有界的**——`send_and_wait` 的 `try/except BaseException`（:173-178）在超时/取消时总会 `_pending.pop`，不会无限残留。真正的改进点是异常包装。

### 3.2 `cancel_pending` 只取消 future，不停止子 Agent 任务 [已核实]

`app/agent/bus.py:180-190`

- 级联取消时子 Agent 事件循环内任务继续执行（工具调用不因 future 取消中断），产生"幽灵任务"资源浪费。

### 3.3 子 Agent 无法触发权限审批 [已核实]

`app/agent/graph.py:306-310`

- 无 `_event_queue`（多 Agent 总线路径）时 `NeedsPermission` 直接返回 `_permission_denied_msg`，子 Agent 写外部路径必然失败；系统提示未告知该限制。

### 3.4 章节检索不带 document_id，跨文档混答

`app/rag/retriever.py:149-154` + `app/rag/chapter_store.py:108-111`

- `find_by_number(None, chap_number)` 无 document_id 过滤，返回所有文档同号章节；子块只按 `results[0].document_id` 拉取 → 混答。

### 3.5 BM25 元数据共享引用被原地改写

`app/rag/retriever.py:196-210` + `app/rag/bm25_index.py:67-69`

- `_enrich_with_parent()` 对 `doc["metadata"]` **原地修改**，而该 dict 是 `BM25Index.metadata` 共享元素 → `chapter_summary` 持久残留 + 并发请求竞态改写。

### 3.6 `tool_dedup` 缓存失败结果，吞掉重试

`app/context/tool_dedup.py:61-72` + `app/agent/graph.py:607-611`

- 执行失败的错误字符串也被 `dedup.set` 缓存；同轮内 LLM 重试同参数返回缓存中的错误（看起来像成功）。`tool_execute`（shell，非确定性）也被去重。

### 3.7 中文 token 估算低估近 2 倍

`app/context/token_counter.py:45`

- 兜底 `len(text)//4` 对中文严重低估，tiktoken 不可用时压缩触发偏晚 → `graph.py` 的 `_truncate_messages` 先硬截断丢数据而非先压缩，与 budget 设计相悖。`estimate_tokens_messages` 也不统计 tool_calls 参数体。

### 3.8 文档上传 fire-and-forget 任务

`app/api/documents.py:65`

- `asyncio.create_task(tm.process_document(...))` 不保存引用 → 可能被 GC（"Task was destroyed but it is pending!"），任务静默消失。

### 3.9 `weather.py` 同步网络调用阻塞事件循环

`app/api/weather.py:257-268`

- `refresh_weather_endpoint` 在事件循环内同步 `urlopen`（最长 ~10s），刷新时冻结服务；应 `asyncio.to_thread`。

### 3.10 `remove_session`/interrupt 只级联直接子会话

`app/session/service.py:91-95` + `repository.py:222-227`

- `cancel_best_effort`/`interrupt` 仅一层；`remove_session` 递归删除所有后代 → 孙级任务未取消，协调器仍可能写已删 session（FK 错误被吞）。

### 3.11 上传/删除阻塞事件循环

`app/api/documents.py:153` + `vector_store.delete_by_metadata`（非原子"先 get 全部 id 再 delete"），大文档（数千块）同步执行阻塞整个事件循环。

### 3.12 `tool_execute` 权限/命令检查顺序不一致（双路径）

`app/agent/graph.py:343-355` vs `app/tools/filesystem.py:396-404`

- 流式路径先 `check(work_dir,"execute")` 后命令白名单；同步路径先白名单后 `check`。且 `check` 对 workspace 内非关键目录返回 `allow` → 工作区内（除 app/plugins/skills/config）可执行任意白名单命令——需确认 `_check_command_blacklist` 覆盖是否充分。

### 3.13 `supervisor._decompose` 英文关键词子串误路由

`app/agent/supervisor.py`

- 关键词用 `in` 子串匹配，`"code"` 会命中 `encode`/`decode`/`scode` 等词导致误路由到 code agent。

### 3.14 `plugins/loader.py:71` 执行插件目录任意 .py（已并入 2.7）；`skills` toggle 无鉴权（并入 1.2）

---

## 四、P3 — 低（代码质量 / 健壮性）

| # | 文件:行 | 问题 |
|---|---------|------|
| 1 | `config.py:33-36` | `summarization_model/api_key/api_base` 声明后**无使用点**（摘要经 `_summarize` 直接读 `settings.llm_*`）→ 死配置，建议删除或接线 |
| 2 | `web_search_agent.py` | `import os` 在函数内，应移顶部；DDG HTML 正则解析脆弱（改版即失效）；`resp.text(errors="ignore")` 编码乱码；无重试/限速 |
| 3 | `supervisor.py:285` `code_agent.py:200` `web_search_agent.py:282` | `max_tokens` 硬编码（512/2048/1024），未走 `settings.llm_max_tokens` |
| 4 | `permission/manager.py:156-159` | `system_dirs` Windows 硬编码，缺 `C:\ProgramData`、`/System`、`/Library`、`C:\Users\*\AppData`；Linux/macOS 下 `C:\...` 无效 |
| 5 | `permission/manager.py:179-209` | `_is_critical_read/_write` 仅相对主工作区；额外工作区（extra_workspaces）内的 `.env`/`.db` 不受保护 |
| 6 | `permission/manager.py:304-319` | `respond()` 用 `"allowed"` 而 `check()` 返回 `"allow"`——**命名不一致**（前端 `stores/permission.ts:44` 与 `PermissionDialog.vue:10` 一致发 `'allowed'`，原判 P0-2 竞态**已排除**，仅为可读性缺陷） |
| 7 | `graph.py:631-651` | `doom_fingerprints.clear()` 后升级需 `2×doom_threshold` 轮连续相同指纹（原判 P1-4"永远到不了 strikes"**已排除**——升级逻辑可到达，但阈值语义与注释不符，建议改为不 clear 仅滑窗） |
| 8 | `graph.py:644-661` | MAX_STEPS 注入后**不会重复注入**（有 `not steps_prompt_injected` 守卫，原判 P1-5 上下文膨胀**已排除**）；但若 Provider 在 `tools=None` 时仍返回 tool_calls，循环会继续执行工具直至 `max_tool_rounds` 兜底（边缘情况） |
| 9 | `tools.py:36-52` | `_annotation_to_json_type` 对 `Union`/嵌套泛型/`Literal`/`Enum` 回退 `"string"`；`Sequence` 已映射 array，`dict` 泛型丢弃 |
| 10 | `memory.py` | 纯内存 dict 无容量上限，TTL 惰性清理，长跑可能 OOM |
| 11 | `document_processor.py:94-104` | `CHAPTER_PATTERN` 非行首锚定，正文含"第X章"字句会被拦腰截断；短章节 parent/child 内容重复入库 |
| 12 | `document_processor.py:5-9` vs `intent.py:11` | 英文章节正则不一致（`Chapter`/`CHAPTER` vs `[Cc]hapter`） |
| 13 | `bm25_index.py:35-40` | `add()` 每次上传全量重建 BM25，语料增大线性劣化 |
| 14 | `chapter_store.py:20-26` | 懒连接非线程安全；读方法无锁；`get_all` 硬编码 `LIMIT 500` |
| 15 | `embeddings.py:55-60` | `except` 内 `if local_model:` 死代码（恒为 None） |
| 16 | `vector_store.py:58` | `score = 1 - distance` 未截断 `[0,1]`，不相关文档可负分 |
| 17 | `compaction.py:284` | `conversation_text[-chars//5:]`：`chars//5==0` 时 `-0` 取整段（边界缺陷，当前门槛不可达）；`max_input_tokens/max_tokens/timeout` 硬编码 |
| 18 | `monitor.py` | `_save_persisted` 同步文件 I/O 在事件循环；`reset_stats` 与 `_save_persisted` 有 tmp.replace 竞态 |
| 19 | `chat.py:765-800` | 旧库 `delete_message`/`delete_conversation` 无命中也返回 ok；`_queue_counter` 排队位次非精确 |
| 20 | `session/task_state.py:46-60,90-149` | `cleanup_old_tasks` 无调用点（tasks 表无界增长）；`record_compaction/load/list_by_conversation` 从未被调用，类基本为残留 |
| 21 | `session/db.py:108-119` | `session_tasks` 死表（schema 定义后无任何读写） |
| 22 | `session/agent_executor.py:106-116` | checkpoint 每轮恒存在时重复追加相同 compaction 消息 |
| 23 | `session/agent_executor.py:118-167` | 失败后遗留无应答 user 消息（有问无答） |
| 24 | `session/router.py:104-110` | `POST /{id}/prompt` 触发无人消费的 Agent 执行，事件静默丢弃 |
| 25 | `session/service.py:133-137` | fork 复制 raw log 不复制 epoch，子会话视角与父模型视角不一致 |
| 26 | `bm25_index.py:55-65` | 本机 `rank_bm25` 无 `delta` 参数：单文档（N==1）且含查询词时 IDF 为负，被 `score > 0` 过滤 → `search` 恒返回空（批量文档场景不受影响） |

---

## 五、原审问题复核结论

| 原审编号 | 原判 | 复核结论 |
|---------|------|----------|
| P0-1 | symlink 绕过 | **已排除**（`Path.resolve()` 缓解）；真正漏洞为 grep/glob 逐文件无检查（见 1.1） |
| P0-2 | respond 竞态 | **已排除**（前端一致传 `'allowed'`；仅命名不一致，降为 P3-6） |
| P0-3 | Future 无限泄漏 | **降级**（有界，超时/取消必清理）；真实问题为裸异常回传（见 3.1） |
| P1-4 | doom 升级不可达 | **已排除**（可到达，需 2×threshold；阈值语义偏差降为 P3-7） |
| P1-5 | MAX_STEPS 重复注入膨胀 | **已排除**（有守卫不重复注入；仅边缘 tool_calls 续跑，降为 P3-8） |

---

## 六、修复优先级建议

**第一批（安全，先做）✅ 已完成**
1. `tool_grep`/`tool_glob` 逐文件 `_is_critical_read` 检查（防 `.env` 泄露）——P0-1.1（已加 `_is_read_allowed` 逐文件过滤）
2. `ADMIN_TOKEN` 缺省时管理端点默认拒绝 + CORS 收紧——P0-1.2（未配置 token 时仅允许本机来源；CORS 收紧为 `cors_origins`）
3. `_get_legacy_conversation` 补 `user_id` 过滤——P0-1.3（与 delete/update 兜底一致：`user_id = ? OR user_id = ''`）
4. `BM25Index.add/remove` 加锁 + 文档任务串行化——P0-1.5（RLock 保护结构；上传/删除经 app 级 `asyncio.Lock` 串行；上传任务保存引用防 GC）
5. `list_pending`/`toggle_skill`/`config` 统一 `require_admin`——P1-2.4（`list_pending`/`toggle_skill`/`update_summarization_config` 已加）

**第二批（一致性/正确性）**
6. session seq 生成改事务内原子化，多写路径收敛到协调器——P0-1.4
7. fork 末条/不存在边界修复——P1-2.6
8. revert 级联 task 子会话与待执行输入——P1-2.8
9. retriever/reranker 子链路降级兜底——P1-2.2
10. `bus.send` error 包装为 AgentMessage 而非裸异常——P2-3.1
11. `rag_wrapper` retrieve/generate 透传 `files`——P1-2.5

**第三批（健壮性/清理）**
12. 清理死代码：`task_state` 未用函数、`session_tasks` 表、`summarization_*` 死配置
13. `weather` 改 `asyncio.to_thread`；上传任务保存引用；`documents` 删除改 `to_thread`

---

## 七、总体评价

代码整体分层清晰、防御意识良好：SQL 全参数化（无注入）、所有路径穿越入口（上传/生成文件/生成器输出/doc_id 删除）均被正确拦截、compaction 不破坏会话水位、monitor 计数有锁。主要短板集中在三处：(1) **鉴权一致性**——`ADMIN_TOKEN` 可缺省 + CORS 全放开 + 多个写端点未按统一标准加锁 + 旧库回退路径 IDOR，构成真实的未授权面；(2) **并发一致性**——三套存储（ChromaDB/SQLite/BM25）无事务无锁、session 串行仅对单一写路径成立；(3) **故障容错**——模型加载"全有或全无"、检索子链路无降级、任务 fire-and-forget。建议按第六节分批修复，安全项优先。
