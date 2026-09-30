# 全链路代码审计报告（死代码 / 未接线 / 业务缺陷）

- 日期：2026-09-29
- 范围：技能子系统、模型目录与模型 API、多 Agent 执行链路、后端↔前端接线
- 方法：4 个并行审计 agent，每个结论均由 `rg` 实际调用点或代码原文佐证；无佐证的猜测一律不写
- 仓库：`E:\AgentSuper`
- 状态：审计阶段为只读；**批次 1 + 批次 2 + 批次 3 已于同日实施完毕**，逐项修复记录见文末
  [修复实施记录](#修复实施记录)。批次 3 的 10 个决策点全部按推荐方案落地。

### 图例

- ✅ 已修复
- ⏳ 待产品决策（批次 3）—— **本轮已全部决策并修复**
- ○ 未处理（批次 1/2 之外、判定为无需动作）

## 严重度定义

| 级别 | 含义 |
| --- | --- |
| **HIGH** | 已经/即将造成用户可见的错误行为、安全泄露或功能整体失效 |
| **MED** | 行为与文档/意图不符，或存在被掩盖的数据错误；用户可能长期不察觉 |
| **LOW** | 死代码、不可达分支、无消费者的导出；当前无害但会误导后续维护 |
| **INFO** | 文档与实现漂移、注释失效、cosmetic |

## 汇总

| 区域 | HIGH | MED | LOW | INFO |
| --- | --- | --- | --- | --- |
| A 技能子系统 | 1 | 6 | 0 | 0 |
| B 模型目录与 API | 4 | 2 | 0 | 0 |
| C 多 Agent 链路 | 1 | 3 | 4 | 0 |
| D API ↔ 前端接线 | 0 | 4 | 1 | 3 |
| **合计** | **6** | **15** | **5** | **3** |

---

## A. 技能子系统

### ✅ A1 · HIGH · `backend/app/agent/graphmod/core.py:588-596` · `refresh_tools()` 重建工具时丢弃全部 core 工具

`refresh_tools()` 在插件/技能开关变更后重建 `self.tools`，但重建结果只包含插件工具与技能工具，**`tool_web_search` / `tool_task` / `tool_memory_set|get|search` / `tool_tts_*` 不在其中**。一旦运行期发生过任何一次 `await agent.refresh_tools()`（插件 toggle、技能 toggle、custom tools 变更），这些工具从此永久消失，直到重启后端。

影响：用户在 UI 里开/关一个技能，可能连带让记忆、网络搜索、任务委派、语音全部失效，且没有任何提示。

建议：以「base 工具集 + 动态工具集」的并集重建，不要覆盖式赋值；补一条回归测试断言 `tool_web_search` / `tool_task` / `tool_memory_*` 在 `refresh_tools()` 之后仍存在。

### ✅ A2 · MED · `backend/app/skills/registry.py:132` · `seed_bundled()` 会把用户自建的同名技能标成「内置」

播种时只按**目录名**判断是否已存在。若用户自己在受管库建了 `tdd/`，与内置 `tdd/` 同名，播种会把用户目录写进 `.bundled.json` 的 `seeded` 名单，该技能随即被打上「内置」徽章 —— 而它其实是用户自己写的。当前测试反而锁定了这个行为。

建议：`seed_bundled` 只对「本次确实从包内复制过去、且此前受管库不存在该目录」的情况记账；已存在同名目录一律不记账、不覆盖、不标徽章。

### ✅ A3 · MED · `backend/app/skills/registry.py:45,143` · `BUNDLED_VERSION` 是死状态

`BUNDLED_VERSION` 被写入 `.bundled.json` 并在播种时比较，但**没有任何功能读取它来触发重新播种或迁移**。写它、读它都无副作用，等于给未来的播种策略埋了一个假的「已播种版本」标记。

建议：要么让 `seed_bundled` 真的按版本做增量播种（版本不同则补齐新内置技能、保留用户改动），要么删掉该字段，别留一个看起来在起作用其实没有的开关。

### ✅ A4 · MED · `backend/app/skills/registry.py`（`_discover_bundled`）vs `loader.py` · 内置名录与技能加载器的 key 不一致

`_discover_bundled()` 按**目录名**建名录，`SkillLoader` 按 **frontmatter `name`** 建 `_skills` 字典键。两者只在「目录名 == frontmatter name」时一致。任一内置技能的 frontmatter name 与目录名不同（或被用户改过），该技能的「内置」标记就丢；反过来用户自建的技能可能被误标（见 A2）。

建议：统一为 frontmatter name 作为唯一身份键，目录名只用于定位文件；`bundled_names()` 消费名录后再与 `SkillLoader` 的键空间求交集。

### ✅ A5 · MED · `backend/app/skills/loader.py:200,271,363` · `split("---", 2)` 缺空片段守卫

三处用 `content.split("---", 2)` 解析 frontmatter，没有校验 `parts[0].strip() == ""`。当技能正文本身以 `---` 开头（markdown 分隔线）或以 `---` 结尾时，解析会错位；`_save_skill_file` 在这种情况下可能把用户正文改写损坏。

建议：改为 `parts = content.split("---", 2)` 后先 `if len(parts) >= 3 and parts[0].strip() == ""` 才视作有 frontmatter，并加正例/反例测试。

### ✅ A6 · MED · `backend/app/agent/graphmod/base.py:416` · `_INTENT_RULES` 规则 #4 不可达

`tool_tts_*` / `tool_voice_*` 规则的匹配前提是它们既不在 `_CORE_TOOL_PREFIXES` 里、也不在 `_RESIDENT_SKILL_PREFIXES` 里。但这两个前缀的判断在规则匹配之前就把它们挡掉了，规则 #4 永远命中不了。

建议：确认这两类工具的挂载策略后，删除规则 #4 或调整前缀判定顺序；无论哪种都不应保留一条永不执行的意图规则。

### ✅ A7 · MED · `backend/app/agent/graphmod/base.py:409-424` vs `backend/app/agent/tools.py:330` · 提示词广告了 `plugin_http-client_*` 但无规则挂载

系统提示词告诉模型存在 HTTP 客户端插件工具，但 `_INTENT_RULES` 里没有对应规则，工具在未命中其他意图时不会被挂载给模型。模型看得见描述、却调不到工具。

建议：补一条 `plugin_http-client_` 意图规则，或在提示词里明确说明该工具需用户显式请求才挂载。

---

## B. 模型目录与模型 API

### ✅ B1 · HIGH · `backend/app/api/models.py:63,75,161` · `fail()` 位置参数误用导致「报错变成功」

`responses.fail` 的签名是 `fail(code: int = 1, message: str = "error", data=None)`。这三处把错误文案作为**第一个位置参数**传给了 `code`：

```python
return fail("text or messages is required")   # code="text or messages is required"
return fail(str(e))                             # code=<异常文案>
return fail(str(e))                             # 同上
```

产出的响应体是 `{"code": "<文案>", "message": "error", "data": null}` —— `code` 是字符串而不是数字。

而前端 `frontend/src/api/errors.ts:112` 的判定是 `typeof body.code === 'number'`：字符串 code 不满足条件，于是**跳过了 `code !== 0` 的抛错分支**，落到 `return body as T`，把错误响应体当成成功数据返回。调用方拿到的是 `{code: "...", message: "error", data: null}`，真实错误文案丢失。

对比 `:93` 与 `:103` 用的是 `fail(message=...)`，行为正确 —— 说明这是遗漏而非有意设计。

建议：三处改为 `fail(message=...)`；并在 `responses.py` 加一条防御（`code` 非 int 时强制转 int），或在 `apiRequest` 的信封判定里同时接受字符串 code 作为错误。

### ✅ B2 · HIGH · `backend/app/api/models.py:34-45` · `GET /api/models/config` 无鉴权且返回明文 api_key

该路由没有任何 `require_admin` 依赖，而返回体包含 `catalog.provider_models_source()`，后者在 `backend/app/models/catalog.py:686` 直接给出每个 Provider 的**明文 `api_key`**。`ADMIN_TOKEN` 未配置时 `require_admin` 至少还把访问限制在 localhost；当前这条路由连这层都没有 —— 同一局域网内任意主机都能取走全部服务商密钥。

建议：加 `dependencies=[Depends(require_admin)]`。模型管理页同源访问时不受影响（`ADMIN_TOKEN` 为空 → 放行 localhost）；跨机部署按既有约定配 `VITE_ADMIN_TOKEN` 即可。

### ✅ B3 · HIGH · `backend/app/api/models.py:135,142` · `GET /api/models/export` 与 `GET /api/models/catalog-full` 无鉴权且返回明文 api_key

`catalog_db.export_config()`（`catalog_db.py:316`）与 `export_full()`（`catalog_db.py:162`）都会 SELECT `providers.api_key` 并原样写进响应（`catalog_db.py:182,215`）。两条路由同样没有 `require_admin`，等价于一个无需认证的密钥导出接口。

建议：两条都加 `require_admin`。若担心跨机迁移场景，退一步方案是响应里把 `api_key` 替换成掩码并提供 `include_secrets=true` + 二次鉴权的独立导出路由。

### ✅ B4 · HIGH · `backend/app/api/models.py:122` · `POST /api/models/reload` 缺 `require_admin`

`reload_catalog()` 会重探测所有 Provider（`catalog.py:452-527` 逐个发 HTTP 请求）并刷新内存目录，是有外部副作用的状态变更，却完全没有鉴权。同文件其它写路由（`:68 :79 :85 :100 :109 :153`）都带 `require_admin`，这条是遗漏。

建议：加 `dependencies=[Depends(require_admin)]`。

### ✅ B5 · MED · `models` 写入但不消费 · `context_length`

模型条目可写入 `context_length`，但 LLM 调用点读取的是 `.env` 的 `MAX_CONTEXT_TOKENS` / `OLLAMA_NUM_CTX`，没有任何代码读条目里的这个字段。用户改了没有任何效果。

建议：二选一 —— 让 `litellm_extra_kwargs` / `provider_api` 真的消费它，或在模型管理 UI 上把它标为只读/未接入。

### ✅ B6 · MED · `models` 写入但不消费 · `limits.max_output_tokens`

输出上限实际取 `settings.llm_max_tokens`（`LLM_MAX_TOKENS`，`graphmod/core.py:_llm_call`），条目里的 `limits.max_output_tokens` 从未被读取。

建议：同上。

### ✅ B7 · MED · `backend/tests/test_api_integration.py` 缺 `/api/models/*` HTTP 测试

模型 API 一条 HTTP 级测试都没有，B1（`fail` 参数误用导致错误被当成功）正是因此长期未被发现。也没有覆盖 B2/B3 的鉴权行为。

建议：补「校验失败返回可识别的错误信封」「无 admin 凭证时 `/models/config` 返回 403」两条测试。

> **已实施（2026-09-29）**：`tests/test_api_integration.py` 新增 `models_client` fixture（只挂 `models` router + catalog 桩，不启动后端运行时）+ 4 条 HTTP 级用例：
> `test_models_list_is_public_and_enveloped`（公开目录 `code=0`；`estimate-tokens` 空 body → **非 0 错误码**，B1 复发防线）、
> `test_models_validation_error_returns_error_envelope`（`upsert_custom` 的 `ValueError` 分支 → `code != 0` + `message` 可见 + `data is None`）、
> `test_models_config_requires_admin_token`（`ADMIN_TOKEN` 已配时 config/export/catalog-full/reload 无凭证或错凭证一律 401，响应体不含 token）、
> `test_models_admin_endpoint_blocks_remote_when_token_unset`（未配 token 时局域网来源 403，公开 `/models` 不受影响）。

---

## C. 多 Agent 执行链路

### ✅ C1 · HIGH · `supermod/core.py:386-407` + `api/chatmod/endpoints.py:275-294,498-524` · plan→build 失败会静默丢弃整份计划

`_merge_plan_build_reply` 在 build 子 Agent 失败时**刻意**把计划正文塞进 `payload["answer"]` 和 `payload["plan_path"]`，但用 `type="error"` 消息发出。两个端点的错误分支只读 `error` / `error_type` / `completed_steps`，**`answer` 与 `plan_path` 全部被丢弃** —— 不进响应体、不进会话记录、SSE 也没有对应字段。用户只看到「build 执行出错」，既没有计划正文，也没有任何指向 `<data>/plans/<conv>/plan.md` 的线索。

这条设计本身就自相矛盾：`AGENTS.md` 写的是「build 失败仍保留计划并透传错误」，实测「保留」只保留到了 AgentMessage 边界。

建议：两个端点的错误分支读取 `answer` / `plan_path`，以 SSE `error` 事件 + `answer` 字段下发并持久化；或让该分支返回 200 的 `MultiAgentChatResponse(answer=..., routed_to="plan→build")`。

### ✅ C2 · MED · `supermod/core.py:194-196,381-384` · plan→build 成功路径 token / cost 双计

`_route_to` 每次路由后发出的是**累计值** `dict(self._usage)`（`:217`）。plan 跑完累计 = P，build 跑完累计 = P+B。`_merge_plan_build_reply` 把两个累计快照相加 → `P + (P+B) = 2P + B`，成本同理翻倍。合并结果作为 `tokens` / `cost` 落库，报表长期虚高。

建议：`_route_to` 发出本轮**增量**（派发前后对 `self._usage` 做差），或合并时减去已计部分；补一条断言合并后 token 精确相等的测试（现有 plan→build 测试根本没校验 `tokens`）。

### ✅ C3 · MED · `graphmod/generate.py:352-367,531` · `MAX_STEPS` 在默认配置下完全失效

循环上界只看 `rounds < max_tool_rounds`；`effective_max_steps = min(max_tool_rounds, max(1, max_steps))` 的**唯一**用途是决定何时注入 `MAX_STEPS_PROMPT`。默认值 `max_steps=24` > `max_tool_rounds=8`，所以注入实际发生在第 8 轮，任何 `MAX_STEPS > MAX_TOOL_ROUNDS` 都是 no-op。`AGENTS.md` 把 `MAX_STEPS` 描述为「对齐 opencode 的 agent.steps 主上限」与实际不符。

建议：让循环真正以 `effective_max_steps` 为界（`max_tool_rounds` 仍参与 `min()`），或者把 `MAX_STEPS` 默认值降到 8 以下并在文档里写清两者的取小关系。

### ✅ C4 · MED · `supermod/decompose.py:43-77` · 并行分解是死代码

`_decompose` 的三条分支（`:70/:74/:77`）**都只返回 1 个子任务**，因此 `len(subtasks) > 1` 永不成立，`parallel.py:48` 的 `_execute_parallel`（其唯一生产调用点 `core.py:122`）不可达。

连带死亡：`_llm_decompose`、`_validate_subtasks`、`DECOMPOSE_SYSTEM_PROMPT`、它们的 usage/cost 统计，以及 `sub_task_fresh_history`（`config.py:208`，唯一读取点在 `parallel.py:71`）。结果是**系统没有任何 fan-out 能力**，多部分请求全部压给单个 Agent。

建议：二选一并明确写进文档 —— 要么在关键词路径无法定夺时真正调用 `_llm_decompose` 恢复扇出，要么删掉 `_execute_parallel` / `_llm_decompose` / `sub_task_fresh_history` 及其不可达的测试。后者能让代码诚实，前者才能对上 `opencode-plan-parallel-explore-design.md`。

### ✅ C5 · MED · `graphmod/generate.py:637` + `config.py:229,233` · `WEAK_MODEL_TWO_STAGE` 永不可达

该分支条件是 `settings.weak_model_two_stage and settings.weak_model_strong_fallback and rounds > 0 and is_weak_model(model)`。三重不可达：

1. `weak_model_strong_fallback` 默认 `False`；
2. 弱模型 `_build_tool_defs` 返回 `None`（不挂任何工具）→ 工具循环根本不进入 → `rounds > 0` 不可能；
3. 即便前两条都打开，条件 2 仍独立成立地排除一切弱模型。

这是一个「默认开启但永远执行不到」的开关。`test_graphmod_generate_core.py:798-870` 反而在测试它，测的是默认配置进不去的路径。

建议：删除 two-stage 分支（弱模型按设计已是 tool-free 纯 QA），同时删掉 `WEAK_MODEL_TWO_STAGE`；或把门控改到一个可达条件上并重写测试。

### ○ C6 · LOW · `agent_specs.py:42,52,59,65` · `AgentSpec.mode` 纯声明（声明式注册表的正常表达，保留）

三个 spec 都填了 `mode`，但全仓只有 `tests/test_agent_specs.py:35/42/50` 读它，生产代码零消费 —— `primary` / `subagent` 的区分并没有被它宣称的规格注册表真正执行。

建议：让 `supermod/base.py` 从 `mode` 派生路由与超时，或明确标注为纯元数据。

### ✅ C7 · LOW · `config.py:219` · `WEAK_MODEL_MOUNT_ALL_TOOLS` 是死配置

全仓搜索只命中它自己的定义一处，是「弱模型纯文本问答」改造前的遗留。

建议：删除该字段。

### ✅ C8 · LOW · `agent/plan_agent.py:187` + `models/schemas.py:114-122` · `plan_path` 永远到不了前端

`plan_path` 一路产出并透传（`core.py:215,400,419`），但 `MultiAgentChatResponse` 里没有这个字段，SSE `done` 事件不带、持久化不存、`rg "plan_path|planPath" frontend/src` 命中 **0**。用户唯一能看到它的通道是 `plan_agent.py:150` 追加在答案正文尾部的那行文字 —— 而这段正文正是 C1 会丢掉的。

建议：把 `plan_path` 加进 schema + `done` 事件 + 前端展示，或从各层 payload 里删掉。

### ✅ C9 · LOW · `agent/bus.py:128-130,175-177` · 宽限期静默把超时翻倍

`grace_extensions=1` 是默认值且所有生产调用点都接受它，但扩展逻辑是 `deadline = loop.time() + timeout` —— 重置为一个**完整的额外超时**。于是 `SUPERVISOR_TIMEOUT=300` 最长可阻塞约 600s，而 `core.py:273` 给用户的超时提示只报基础值。

建议：`deadline += min(timeout, grace_window)`，或在提示与 chainlog 中写出实际截止时间。

### ✅ C10 · LOW · `config.py:200-201` · `SUBAGENT_DEPTH` 守卫不可达且默认值与自身提示矛盾

接线是完整的（`rag_wrapper.py:75` → `core.py:635` → `state.py:75` → `graphmod/tools.py:315` → `_tool_task(depth=…)`），但最深链路只有一跳，且 `explore` 的只读 allowlist（`sub_tools.py:155-160`）不含 `tool_task`，无法嵌套。`max_depth=4` 永不触发；错误文案硬编码 "(default 1)"，而 `config.py` 注释写的是「仅 1 层」，默认值却是 4。

建议：默认值改成 1，或把这条明确标注为面向未来的预留。

---

## D. API ↔ 前端接线

### ✅ D1 · MED · 11 个后端路由没有任何调用者

| 路由 | 位置 | 状态 |
| --- | --- | --- |
| `POST /api/sessions/{id}/fork` | `session/router.py:103` | 包装函数 `forkSession` 零调用，`backend/scripts` 也无调用 |
| `GET /api/sessions/{id}/context` | `session/router.py:135` | 零调用者 |
| `POST /api/sessions/{id}/compact` | `session/router.py:141` | 零调用者（`scripts/compact_session.py` 自带实现，不走该路由） |
| `GET /api/sessions/{id}/children` | `session/router.py:179` | 零调用者 |
| `GET /api/sessions/{id}/status` | `session/router.py:184` | 零调用者 |
| `POST /api/sessions` | `session/router.py:22` | 零调用者（首条消息时后端自动建会话，故无害） |
| `GET/POST /api/config/summarization` | `api/config.py:33,43` | `rg summarization frontend/src` 命中 0 |
| `POST /models/reload`、`GET /models/export`、`GET /models/catalog-full`、`POST /models/import` | `api/models.py:122,135,142,153` | 前端 0 命中（可视为手工/CLI 运维接口，但 B2–B4 正是这几条的安全问题所在） |
| `POST /api/auth/register`、`POST /api/auth/token` | `api/auth.py:48,58` | 旧版设备 TOFU，`AGENTS.md` 标为 legacy |

建议：对每个路由明确二选一 —— 要么接上 UI（fork/compact/status/context 都很有用），要么删掉路由与前端包装函数。现在这种「路由齐全但没人调」的状态，无法区分「有意的手工接口」和「漏接的功能」。

### ✅ D2 · MED · 10 个前端导出函数没有任何调用者

- `api/sessions.ts`：`createSession`(:105)、`forkSession`(:168)、`getSessionContext`(:184)、`compactSession`(:190)、`getSessionChildren`(:235)、`getSessionStatus`(:239) —— 全部 0 调用
- `api/voice.ts`：`ttsHealth`(:15) —— 0 调用（它包的路由被 `scripts/smoke_voice_api.py:35` 用着，所以是前端侧死代码）
- `api/session-cache.ts`：`loadAllSessionIds`(:124) —— 只被测试引用
- `api/auth.ts`：`getUsername`(:42)、`isAuthEnabled`(:97) —— 0 调用（store 走的是 `getAuthInitInfo()`）

另有 6 个过度导出的内部符号（`errors.ts` 的 `ApiError`/`parseErrorResponse`/`toApiError`、`base.ts` 的 `apiRequest`/`ADMIN_TOKEN`/`apiUrl`）只在本模块内使用，没有消费者做 `instanceof ApiError`。

建议：与 D1 一并处理；`loadAllSessionIds` 连同它的测试用例一起删。

### ✅ D3 · MED · `api/chatmod/endpoints.py:408` · `model_switched` 事件前端没有处理器

后端在 `:408` 发出 `model_switched`，但 `stores/multiAgent.ts:586-716` 的事件分支链里没有它（且链尾没有 `else` 兜底），事件被静默丢弃 —— 用户永远不会被告知会话已切换模型。`types/index.ts:274` 里有这个类型成员，说明前端曾打算处理但没落地。

建议：补一个分支（更新消息上的模型标签或提示「已切换到模型 X」），或者干脆停止发这个事件。

### ✅ D4 · MED · `stores/multiAgent.ts:622` · `agent_stream` 事件后端从不发送

`rg -F "'agent_stream'" backend` 命中 0（所有 `agent_stream` 匹配都是 `chat_multi_agent_stream` 这个函数名的子串）。真正的增量文本通道是 `text_delta`（`stream_events.py:121-123`）。前端这个分支和 `types/index.ts:275` 的类型成员是死的。

建议：删掉该分支与类型成员，以及只覆盖它的测试用例。

### ✅ D5 · LOW · `endpoints.py:408` vs `multiAgent.ts:586-596` · `model_switched` 的发送顺序导致队列态闪烁

`model_switched` 在信号量/队列检查**之前**发出，前端遇到它会先触发 `streamPhase = 'running'` 并清空 `queuePosition`，随后 `queued`（`:440`）又把两者设回去 —— 排队的请求头部会闪一下。

建议：把 `model_switched` 的发出挪到队列检查之后，或把前端的 `running` 转换挪到 `queued` 分支之后。

### ○ D6 · LOW · 「统一响应信封」只覆盖了 14 个 router 中的 4 个

`AGENTS.md` 称所有 `/api/*` 响应都是 `{code, message, data}`，实际只有 `voice.py` / `auth.py` / `models.py` / `logs.py` 用了 `ok()`，其余 10 个返回裸 payload。能跑通是因为 `errors.ts:111-118` 在没有数字 `code` 时直接返回原 body —— 一套两套并存的契约。

B1 正是踩在两套契约的缝里炸的。

建议：要么统一到 `ok()`，要么在 `api/errors.ts` 把「兼容裸 body」写成显式契约注释。

### ✅ D7 · INFO · `api/auth.ts:125-129` · 登出把 5 个 `agent_super_*` 键写成空串而不是删除

全仓 `localStorage.removeItem` 命中 0，退出登录后本地仍留 5 个空值键。

建议：改用 `removeItem`。

### ○ D8 · INFO · `frontend/src/App.vue:26` · 注释描述的前端从未走过的路径

注释说存在一个针对 `POST /api/chat/multi-agent` 的 `startPolling()` 兜底，但 `rg -F "/multi-agent" frontend/src` 只命中 `api/multiAgent.ts:16,39`（快照恢复 + 流式），非流式端点没有前端包装函数。`App.vue:26` 的注释与 `PermissionDialog` 的轮询兜底因此是悬空的。

建议：修正或删除该注释；非流式端点目前只有 `scripts/stress_session_writes.py:198` 的压测在用。

### ○ D9 · INFO · `frontend/src/config/models.ts` · `SUPPORTED_MODELS` 文档不符

`AGENTS.md` 称其「由 store + view 共享」，实际只有 `stores/multiAgent.ts:15`（外加测试）引用；`ModelManagerView.vue` 读的是服务端目录。

建议：改文档，或删掉该常量。

---

## 修复批次

### 批次 1：安全与正确性（无产品决策，立即修）

1. **B1** `models.py:63,75,161` → `fail(message=...)`；`responses.py` 加 `code` 类型防御。回归测试 B7。
2. **B2/B3/B4** 四条模型路由补 `require_admin`。
3. **A1** `refresh_tools()` 改为并集重建，不丢 core 工具。回归测试。
4. **C1** 两个端点错误分支读取 `answer` / `plan_path`。
5. **A5** `split("---")` 加空片段守卫。回归测试。

### 批次 2：数据正确性与不可达分支

6. **C2** token/cost 改发增量。回归测试。
7. **C3** 循环真正以 `effective_max_steps` 为界（或明确文档化取小关系）。
8. **C5 + C7** 删除永不可达的 two-stage 分支与 `WEAK_MODEL_MOUNT_ALL_TOOLS`。
9. **A6** 删除不可达的意图规则 #4。
10. **D3 / D4** 补 `model_switched` 处理器、删 `agent_stream` 分支与类型。**D5** 调整发送顺序。
11. **D7** 改用 `removeItem`。

### 批次 3：需要产品决策（先确认再动）

| 项 | 决策点 | 决策 | 状态 |
| --- | --- | --- | --- |
| **C4** | 恢复 `_llm_decompose` 扇出，还是删掉整套并行分解？ | **删掉整套并行分解**（扇出职责归主 Agent 的 `tool_task` 链） | ✅ 已修复 |
| **C5** | 弱模型 two-stage 死分支：删掉，还是改门控使其可达？ | 删 two-stage；**保留** `weak_model_strong_fallback` | ✅ 已修复 |
| **D1/D2** | 11 条路由 + 10 个前端导出：接 UI 还是删？ | **接线 UI**（fork/compact/status）+ 删死导出 + 保留运维路由并文档化 | ✅ 已修复 |
| **A2** | `seed_bundled` 是否应把用户同名目录算作内置？ | **不算**（用户目录不记账、不标内置、不被覆盖） | ✅ 已修复 |
| **A3** | `BUNDLED_VERSION` 要不要真的做增量播种？ | **做**，摘要记账、只刷新未被用户修改的技能 | ✅ 已修复 |
| **A4** | 技能身份键统一到 frontmatter name？ | **统一到 frontmatter `name`** | ✅ 已修复 |
| **B5/B6** | `context_length` / `limits.max_output_tokens` 接入还是标只读？ | **接入**，语义为「上限取小」（不放放大 `.env` 全局值） | ✅ 已修复 |
| **C8** | `plan_path` 前端要不要展示（后端已接线，见下）？ | **展示**（可点击复制路径） | ✅ 已修复 |
| **C9** | 宽限期是否封顶？ | **封顶**（有限次追加宽限） | ✅ 已修复 |
| **C10** | `SUBAGENT_DEPTH` 默认值改 1？ | **改 1** | ✅ 已修复 |

---

## 修复实施记录（2026-09-29）

### 批次 1：安全与正确性 —— 6/6 已修复

| 项 | 改动 | 回归测试 |
| --- | --- | --- |
| **A1** | `graphmod/base.py` 抽出 `_static_tools_head()` / `_dynamic_tools()` / `_static_tools_tail()` / `_compose_tools()`，`__init__` 与 `core.py:refresh_tools()` 共用同一入口；refresh 由「只放动态工具」变为与首次构建完全一致的并集重建，工具注册顺序不变 | `test_audit_batch1_regressions.py::test_refresh_tools_keeps_memory_and_voice_tools`（直接构造 `RAGAgent`，断言 `tool_memory_set` / `tool_voice_transcribe` 在 refresh 后仍在） |
| **B1** | `api/models.py:63,75,161` 三处 `fail(str(e))` → `fail(message=str(e))`；`api/responses.py:fail()` 增加防御：非 `int` 码被强转、`code == 0` 归一为 1，字符串码不再被前端 `res.code === 0` 判成成功 | `test_fail_rejects_string_code` / `test_fail_normalizes_success_code_to_error`（`test_audit_batch1_regressions.py`） |
| **B2/B3/B4** | `/api/models/config`、`/api/models/export`、`/api/models/catalog-full`、`/api/models/reload` 四个路由加 `dependencies=[Depends(require_admin)]`，关闭「明文 api_key 任意读取」与未授权 reload | `test_model_sensitive_routes_require_admin`（遍历 4 条路由断言 `require_admin` 出现在 `dependant.dependencies`） |
| **C1** | 计划正文不再被丢弃：`endpoints.py` 非流式遇 `type="error"` 且 `payload["answer"]` 非空时走正常返回（并把 `status="error"` 记入会话）；流式改为发 `done` + `partial_error` 而非 `error`，同时落库；`MultiAgentChatResponse` 新增可选 `plan_path` | `test_chat_multi_agent_reply_error_with_plan_keeps_plan`、`test_stream_partial_error_emits_done_with_plan`（断言 `assistant_msg_id` 存在 → 正文真的落库）+ 前端 `partial_error` 事件用例 |
| **A5** | `skills/loader.py` 新增 `_split_frontmatter()`：要求 `parts[0].strip() == ""`，YAML 解析失败或结果非映射时回退为「无 frontmatter」；`load_all` / `update_skill` / `save_skill` / `get_skill_body` 全部改走它 | 6 条 frontmatter 边界用例（`test_audit_batch1_regressions.py`） |

### 批次 2：数据正确性与不可达分支 —— 全部已修复

| 项 | 改动 | 回归测试 |
| --- | --- | --- |
| **C2** | `supermod/core.py` 派发前快照 `_usage` / `_cost`，yield 时用新增的 `_sub_usage()` 发**本轮增量**而非累计值。单路由场景两者相等（行为不变），plan→build 由「P 与 P+B 相加」变回 `P + B`，计划用量不再被计两次 | `test_route_to_emits_per_route_usage_delta`（走真实 `_route_to`，断言两条分别为 100 / 500，合并后 600）+ `test_route_to_single_route_delta_matches_subagent` |
| **C3** | `graphmod/generate.py` 循环上界由 `rounds < max_tool_rounds` 改为 `rounds < effective_max_steps`（= `min(MAX_STEPS, MAX_TOOL_ROUNDS)`）。默认 `min(24, 8) = 8`，行为不变；但 `MAX_STEPS < MAX_TOOL_ROUNDS` 时该配置项**首次真正生效**。收尾告警日志改为打印生效上限与两个配置值 | `test_generate_max_steps_is_the_binding_cap`（`MAX_STEPS=2 / MAX_TOOL_ROUNDS=8` → 只 3 次 LLM 调用，旧实现会跑满 8 轮）+ `test_generate_max_steps_above_tool_rounds_keeps_tool_rounds_cap`（取小语义） |
| **C7** | 删除 `config.py:weak_model_mount_all_tools` —— `rg` 全仓仅此一处声明，无任何读取方 | 由 `test_audit_batch1_regressions.py` 之外的全量套件覆盖（`Settings` 字段删除无外部引用） |
| **A6** | 删除 `_INTENT_RULES` 的「语音」规则：`tool_tts_synthesize` / `tool_voice_transcribe` 都是 `tool_*` 前缀，已被 `_CORE_TOOL_PREFIXES` 无条件挂载（`base.py:505` 先 `continue`），该规则永远走不到。表注释同步改为「仅用于 `plugin_*`」 | 运行时断言 `len(_INTENT_RULES) == 6` 且所有前缀均为 `plugin_*` |
| **D7** | `api/auth.ts:clearSessionLocal()` 改为 `localStorage.removeItem()` 逐键删除（原为写空串，共享设备上残留 `key=""` 残骸，`expires_at` 的 `'0'` 有被误解析的风险） | 新增 `frontend/tests/unit/api/auth.spec.ts`（3 条：login 写全 5 键 / logout 后全部为 `null` / logout 后不残留旧用户名） |
| **D3 + D5** | `model_switched` 契约对齐：后端原发 `{"model": {...}}`，而前端读 `event.model_ref` → 切模型后 store 的模型标记**永远不更新**。后端改发 `model_ref`（`endpoints.py:430`）。闪烁问题（D5）由前端把该分支放在 `streamPhase` 转换**之前** `return` 解决 | 后端 `test_stream_model_switched_uses_model_ref_key`（同时断言旧键 `model` 已不存在）+ `test_stream_no_model_switched_when_model_unchanged`；前端 `model_switched` 2 条（含 `model_ref` 缺失时安全忽略） |
| **D4** | 删除 `stores/multiAgent.ts` 的 `agent_stream` 分支 —— 后端从不发送该类型（实际是 `text_delta`），`rg` 确认无发送方 | 由全量 vitest 覆盖 |

### 批次 3：需要产品决策 —— 10/10 已决策并修复

| 项 | 改动 | 回归测试 |
| --- | --- | --- |
| **C4** | 删除整套并行分解：`supermod/parallel.py` 整文件移除（`_execute_parallel` / `parallel_message`），`decompose.py` 去掉 LLM 分解入口（`_llm_decompose` / `MAX_SUBTASKS` / `validate` 与「3 子任务」硬约束），`constants.py` 删 `SUBTASK_PARSE_MODEL`，`supervisor.py` facade 改由 `decompose.SupervisorAgent` 导出（继承链降为 `Base→Core→Decompose`）。Supervisor 顶层**只路由单个** build/plan，并行委派由主 Agent 的 `tool_task` 链承担；`sub_task_fresh_history` 配置随之删除 | `test_split_regressions.py::test_facade_exports_supervisor_agent_from_decompose`、`test_supervisor_agent.py::test_mro_chain_and_method_placement`、`test_supervisor_agent.py::test_facade_exports_intact`，以及 `scripts/test_multi_agent_parallel.py`（改名为串行契约断言） |
| **C5** | 删除 `weak_model_two_stage` 及其死分支；保留 `WEAK_MODEL_STRONG_FALLBACK`（显式开关，默认 false）与「提示切换更强模型」路径 | 全量套件（配置项删除无外部引用） |
| **C9** | `AgentBus.send_and_wait()` 的宽限期改为**有限次**追加（原来只要子 Agent 仍在心跳就无限续期 → `SUB_AGENT_TIMEOUT` 事实上失效） | `test_agent_bus.py::test_grace_extension_does_not_double_the_timeout`、`::test_send_and_wait_custom_grace_params` |
| **C10** | `SUBAGENT_DEPTH` 默认值由 2 改 1（默认配置下 `plan→build` 不会再额外多派生一层 explore） | `test_graphmod_generate_core.py::test_tool_task_depth_limit` |
| **A2/A3/A4** | `skills/registry.py` 播种改为**摘要记账的增量播种**：身份键取 frontmatter `name`（复用 `loader._split_frontmatter`），`BUNDLED_VERSION = 2`，清单升级为 `{version, seeded: {name: {dir, digest}}}`（旧 list 清单读取时迁移）。三种不变量：**幂等**（重复启动零拷贝）、**非破坏**（已存在同名目录不记账、不覆盖、不标内置）、**尊重删除**（删掉的内置技能不再复活）。版本变化时只刷新摘要仍匹配、确认未被用户修改的技能；用户编辑过的技能内容与记账一并保留 | `test_skills_registry.py` 21 条通过，含 `test_seed_bundled_preserves_preexisting_same_name`、`::test_seed_bundled_uses_frontmatter_name_as_identity`、`::test_seed_bundled_migrates_legacy_list_manifest`、`::test_seed_bundled_version_bump_refreshes_unmodified`、`::test_seed_bundled_version_bump_keeps_user_edits`、`::test_seed_bundled_version_bump_adds_new_only`、`::test_seed_bundled_respects_deletion` |
| **B5/B6** | 模型声明的限制**真正接线**（此前写而不读）：`catalog.py` 新增 `read_limits()` / `resolve_context_length()` / `resolve_max_output_tokens()`，语义为「模型声明值与 `.env` 全局值**取小**」——`context_length` 收窄上下文预算（`budget.py` 三个函数与 `compaction_threshold_tokens` 自动阈值）、Ollama `num_ctx`（`provider_api`），`limits.max_output_tokens` 收窄主 Agent / plan / 子 Agent 的输出上限。不放大 `.env` 是有意的：`MAX_CONTEXT_TOKENS` / `OLLAMA_NUM_CTX` 同时是成本与显存旋钮，模型页填大值不应悄悄抬高 | `test_model_catalog.py::test_resolve_context_length_narrows_but_never_widens`、`::test_resolve_max_output_tokens_caps_by_entry`、`::test_provider_api_ollama_num_ctx_narrowed_by_entry`、`::test_budget_functions_honor_model_context_length`；另 `test_context_guards.py` / `test_context_utils.py` / `test_agent_tools.py` 全通过 |
| **D1** | 11 条孤立路由**接线 UI 而非删除**：`sessions.ts` 补 `forkSession` / `compactSession` / `getSessionStatus`，`stores/multiAgent.ts` 新增 `forkConversation` / `compactConversation` / `fetchSessionStatus`，`MultiAgentChatHistory.vue` 每条会话加「分叉 / 压缩 / 状态」按钮（压缩中禁用，状态写入行内 tooltip）。`POST /api/sessions`、context/children、voice status 等编程/运维路由**保留**并在 `api/sessions.ts` / `api/voice.ts` 注释中标注用途 | 前端 `stores/multiAgent.spec.ts` 新增 5 条（fork 成功/失败、compact 成功/失败、status 透传/异常） |
| **D2** | 删除 10 个无调用前端导出：`createSession`、`getSessionContext`、`getSessionChildren`（sessions.ts）、`ttsHealth` + `TtsHealth`（voice.ts）、`loadAllSessionIds`（session-cache.ts）、`isAuthEnabled`（auth.ts）、以及 `ADMIN_TOKEN` / `apiUrl` / `parseErrorResponse` / `toApiError` 改为模块内部符号（`base.ts` / `errors.ts`）。`getUsername()` 因 D7 登出残留回归测试仍需导出，保留 | `tests/unit/session-cache.spec.ts` 改为验证「单会话缓存存取/墓碑过滤」；`api/auth.spec.ts` 复用 `getUsername`；全量 vitest 通过 |
| **C8** | `plan_path` 前端落地：消息卡片展示「已生成计划文件 + 完整路径」，整块**可点击复制**（`role="button"` + `tabindex` + Enter 键），复制成功显示「已复制」，`navigator.clipboard` 不可用（非安全上下文）降级 `window.prompt` 手动复制。不做 `href="file://"` 伪链接 —— http(s) 页面会被浏览器拦截，后端也没有「在系统文件管理器中打开」的路由（`os.startfile`/`explorer` 全仓无调用） | `tests/unit/components/MultiAgentResponse.spec.ts` 新增 3 条（有路径 → 点击复制并提示已复制 / 剪贴板失败 → prompt 降级 / 无路径 → 不渲染卡片） |

### 验证

- 后端全量：`.venv\Scripts\python.exe -X utf8 -m pytest tests\ -q` → 全部通过（exit 0，批次 3 修改后重跑）。
- 前端：`npm run check`（`vue-tsc -b` + vitest）→ 8 files / 89 tests 通过。
- 前端：`npm run test:coverage` → 全部门禁通过（`MultiAgentResponse.vue` lines ≥90 / statements ≥80 已由 C8 三条用例拉回）。
- 前端：`npm run build` → 构建成功。

### 未改动的审计结论

以下项经复核判定**无需动作**：C6（`AgentSpec.mode` 声明式，与实际行为一致）、D6（信封覆盖率）、D8/D9（注释与文档不符）。A2/A3/A4/A7/B5/B6/C4/C5/C8/C9/C10/D1/D2 已在本轮修复，见上表；B7 的 HTTP 测试亦已补齐（见 B7 条目下的实施记录）。

---

## 明确核查过、**不是**问题的项

- 13 个 Pinia store 全部有实例化点；12 个 view 全部被路由覆盖；12 个组件全部被引用；13 个 `styles/chat/*.css` 全部被导入；7 个 mobile view 全部接进 `mobileViews` 映射。
- `TaggedEventQueue`（`rag_wrapper.py:64`）、`send_and_wait` 宽限期机制（`test_agent_bus.py:158`）、`AgentSpec.task_subagents`（`plan_agent.py:243`）均真实接线。
- 子 Agent 只读约束是**结构强制**：`tool_ls/read_file/glob/grep` 确实都是只读工具，schema 过滤（`sub_tools.py:561`）与运行时硬拒绝（`:602-604`）双重生效。
- 4 处 `apiRequest(..., auth=false)` 全部合法（`auth/status`、`account/login`、`account/register`、`account/me`；后者为避免 `ensureAuth()` 循环等待而手动设头，是正确写法）。
- 未发现信封二次解包 bug；未发现孤立的 localStorage 键；`WeatherAlert`/`SettingsPanel` 的直连 URL 与后端路由逐一核对一致。
- 终止条件判定（`finish_reason` 归一 + 仅 `tool-calls` 续跑）实现正确，循环退出后有末批 `agent_stream` 之外的收尾调用批（`generate.py:553`）兜底排空待执行调用。
