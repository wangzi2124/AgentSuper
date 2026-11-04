# 聊天框模型切换慢：opencode 设计对照与改造报告

> 结论先行：当前 AgentSuper 的「切换模型」是一个同步的重迁移操作，点击后要等一次全量模型目录刷新、一次服务端历史压缩/精确值探针、两次历史拉取和一次会话列表刷新。opencode 的做法相反：**切换只是本地偏好变更，模型随下一条用户消息下发，真正昂贵的工作由回合流程按需完成**。建议把 AgentSuper 改成「本地立即切换 + 后台迁移/回合前迁移」，保留现有的备份、探针、原子提交等安全约束。

---

## 1. 现象与调用链

### 1.1 前端点击路径

`frontend/src/components/ChatInput.vue:31`

```ts
async function pickModel(value: string) {
  modelMenuOpen.value = false
  await agent.persistSelectedModel(value)
}
```

点击后菜单立即关闭，但 `selectedModel` 要等服务端成功才更新，所以胶囊上的模型名会停留在旧值，直到整条链路完成。

`frontend/src/stores/multiAgent.ts:1299` 的 `persistSelectedModel` 实际做了这些事：

1. `await loadModels(true)`：强制重新请求 `GET /api/models`，后端可能重新探测所有 provider。
2. `GET /api/sessions/{id}/messages`：切换前拉全量历史，作为完整性校验基线。
3. `PATCH /api/sessions/{id}`：触发后端 `SwitchService.switch()`。
4. `GET /api/sessions/{id}/messages`：切换后再拉一次全量历史，逐条比对是否丢消息。
5. `await loadConversations()`：再刷新一次会话列表。
6. 失败时还要再 `PATCH` 一次回滚模型。

也就是说，一次点击至少产生 **5 个网络往返 + 1 次可能长达数十秒的服务端压缩**。

### 1.2 后端切换路径

`backend/app/api/sessions.py:100` 的 `PATCH /api/sessions/{id}` 在 `body.model` 变化时调用 `ctx.switch_model()`。

`backend/harness/switch.py:142` 的 `SwitchService.switch()`：

1. 读取目标模型窗口；窗口未知直接拒绝。
2. 会话 `running` 时直接 409 拒绝。
3. `read_messages()` 读取全量消息。
4. 计算 token 与目标模型压缩阈值。
5. 把全量历史写一份迁移备份 JSON（`_backup`）。
6. 若 `tokens > threshold`：调用 `compress_messages(..., require_llm=True, require_precision=True)`，即**同步等 LLM 摘要 + 精确值探针**；失败则回滚。
7. 写 epoch 绑定。
8. 再读一次消息做完整性校验。
9. 持久化会话模型、写审计。

慢的根因不是 UI，而是**把「选择模型」和「把历史迁移到能装进目标窗口」绑定成了同一个同步动作**。只要目标窗口较小或历史较长，点击就必须等模型完成摘要。

### 1.3 另外两个放大项

- `loadModels(true)` 会强制刷新目录，而目录接口可能触发 provider 探测；这跟「切换」本身无关。
- `loadConversations()` 与前后两次 `getSessionMessages()` 都是全量请求，且与切换正确性并非强绑定。

---

## 2. opencode 的设计

### 2.1 切换是本地状态，不是服务端迁移

`C:\tool\document\opencode-dev\packages\app\src\context\local.tsx`

- 模型选择按 workspace + session 持久化到本地存储：
  - `Persist.workspace(..., "model-selection", ...)`（`local.tsx:69`）
  - `scope()` 优先取当前 session 的保存值，否则取草稿值（`local.tsx:121`）
- `model.set()` 只做三件事（`local.tsx:292`）：
  1. 记录 `last`，供撤销/回退使用；
  2. `write({ model: item })` 写入本地持久化状态；
  3. 更新最近使用列表。

没有 HTTP PATCH，没有历史压缩，没有服务端门禁。

`packages/app/src/components/dialog-select-model.tsx:66` 的选择回调也只是：

```ts
model.set({ modelID: x.id, providerID: x.provider.id }, { recent: true })
props.onSelect()
```

选完立即关闭弹层，UI 马上显示新模型。

### 2.2 模型随「下一条消息」下发

提交时从本地取当前模型，放进 `promptAsync` 请求：

`packages/app/src/components/prompt-input/submit.ts:155`

```ts
await input.client.session.promptAsync({
  sessionID: input.draft.sessionID,
  agent: input.draft.agent,
  model: input.draft.model,
  messageID,
  parts: requestParts,
  variant: input.draft.variant,
})
```

服务端 `PromptInput` 把 `model` 当作可选字段（`packages/opencode/src/session/prompt.ts:2000` 附近），创建用户消息时：

`packages/opencode/src/session/prompt.ts:1104`

```ts
const model = input.model ?? ag.model ?? (yield* currentModel(input.sessionID))
```

然后把这个模型写进 **user message**（`prompt.ts:1119`）。也就是说：

- 模型是**消息级属性**，不是「整个会话必须先迁移完成的全局状态」；
- 切换只影响下一条消息；
- 正在运行的回合继续用它启动时的模型，不会被半途改变；
- 服务端只在模型真的变化时发一个 `ModelSwitched` 事件（`prompt.ts:1140`），用于时间线展示/同步，不阻塞请求。

### 2.3 昂贵的压缩在「需要时」发生

opencode 不在切换时压缩。它按每条消息记录的模型、在回合适配阶段做 overflow/compaction 判断（`packages/opencode/src/session/compaction.ts`、`overflow.ts`）。因此：

- 切到大窗口模型：几乎零成本；
- 切到小窗口模型：只有真的发送并超限时才压缩，用户已经先看到选择生效；
- 用户切了又切回去：不会产生两次无意义的摘要迁移。

### 2.4 客户端乐观呈现

opencode 的 prompt 提交是「先插入乐观消息，再发请求」：

`packages/app/src/components/prompt-input/submit.ts:142` 先 `setBusy()` + `add()`，失败才 `remove()`。模型选择本身更是纯本地操作，不存在等待态。

---

## 3. 两套设计的本质差异

| 维度 | AgentSuper 当前 | opencode |
| --- | --- | --- |
| 切换的语义 | 服务端会话级迁移事务 | 本地会话偏好 |
| 生效时机 | PATCH 成功即生效 | 下一条消息生效 |
| 历史压缩 | 切换时同步执行 | 回合内按需执行 |
| UI 反馈 | 等服务端成功才改标签 | 点击即改 |
| 运行中切换 | 409 拒绝 | 允许，当前回合不受影响 |
| 失败处理 | 回滚模型 + 提示 | 只影响下一条消息，重选即可 |
| 网络往返 | 5+ 次 | 0 次（保存本地） |
| 代价 | 点击路径承担全部迁移成本 | 成本推迟到真正需要时 |

---

## 4. 推荐改造方案（兼容现有系统）

### 4.1 目标状态模型

把「用户想用的模型」和「当前上下文已经适配好的模型」分开：

```text
desired_model   用户刚选的模型（本地立即生效，下一条消息携带）
active_model    当前上下文 epoch 已完成迁移的模型
pending_switch  可选：后台迁移任务状态（idle/running/failed）
```

- 点击选择器：只改 `desired_model` 并本地持久化，UI 立即更新。
- 下一条消息：请求携带 `model = desired_model`。
- 服务端回合开始前：如果 `desired_model != active_model`，执行迁移；迁移完成才真正调用目标模型。
- 当前正在跑的回合：继续用启动时的模型，不受选择影响。

这样既保留「显式切换」的语义，又不把成本压在点击上。

### 4.2 前端改动（P0，可立即做）

1. **先更新 UI，再发请求**
   - `persistSelectedModel` 首行 `selectedModel.value = model`。
   - 失败再回滚到 `previous`，并把错误提示放到时间线/Toast。

2. **去掉切换路径上的非必要请求**
   - 不在切换时 `loadModels(true)`；目录已在 store 初始化时加载，下拉只需要当前快照。
   - 删除切换前后的 `getSessionMessages()` 完整性比对。完整性应该由服务端在迁移事务内保证，不应让前端再全量拉两次。
   - 删除 `loadConversations()`；PATCH 返回的 session 足够更新当前项。
   - 若仍要兼容旧后端，可在后端返回中带 `messages_revision`/`model_epoch`，前端只做版本号比对。

3. **加切换状态**
   - `selectedModel` 立即变；
   - 胶囊显示一个小状态点：`迁移中` / `已就绪` / `失败`；
   - 迁移中允许再次切换（只更新 desired_model，合并后台任务）。

4. **本地持久化**
   - 参考 opencode：按 `session_id` 存 `{ providerID, modelID }` 到 `localStorage`（`frontend/src/api/model-cache.ts` 已有缓存设施，可扩展一个 `model-selection` key）。
   - `loadConversation` 时优先用本地该会话的选择，服务端返回的 `session.model` 作为兜底回填。

### 4.3 后端改动（P1）

#### 方案 A：后台迁移任务（改动最小）

`PATCH /api/sessions/{id}` 不再同步跑完整 `SwitchService`，而是：

1. 轻量校验目标模型存在、窗口已知、会话不 running；
2. 写 `desired_model`；
3. 创建后台任务执行原 `SwitchService`（备份、压缩、探针、原子提交、审计全部保留）；
4. 立即返回 `202 { switched: "pending", job_id, model }`；
5. 通过现有 SSE / status 接口推送 `model_switch_started/progress/completed/failed`。

用户点击后立即可见；真正发下一条消息时如果迁移未完成，发送流程等待该 job（显示「正在准备上下文」），而不是让用户重复点击。

#### 方案 B：回合前惰性迁移（更接近 opencode）

1. `PATCH` 只写 `desired_model`，立刻返回；
2. 每个回合开始时比较 `desired_model` 与 `active_model`；
3. 需要迁移则在回合内执行，完成后用目标模型跑本轮；
4. 若目标窗口不小于当前 token 数，直接切换，跳过摘要；
5. 当前回合运行中允许改 `desired_model`，只影响下一轮。

方案 B 语义最干净，但需要把 `SwitchService` 从「API 同步调用」改成「回合前置步骤」，并让 `run_tool_loop` 的溢出恢复逻辑与迁移逻辑共用同一套压缩门面（`harness/compression.py`）。

建议先做 A（低风险、不改回合主链路），再演进到 B。

### 4.4 保留现有安全约束

现有 `AGENTS.md §4.3` 的 8 条约束不应取消，只是改变执行时机：

| 约束 | 保留方式 |
| --- | --- |
| 切换前写原文快照 | 后台迁移任务内照旧 `_backup` |
| 目标窗口未知拒绝 | `PATCH` 轻量校验阶段就拒绝 |
| running 拒绝 | 若允许「下一轮生效」，则 running 时允许改 desired_model，但禁止立即迁移 |
| 阈值/保留量与目标窗口绑定 | 迁移任务照旧重算 `CompressionPlan` |
| 预算累计不清零 | 不受影响 |
| 摘要 + tail 通过精确值探针 | 后台迁移照旧 `require_precision=True` |
| 绑定/模型/审计原子 + 回滚 | 后台任务照旧 `upsert_epoch` + `persist_session` |
| 消息 ID 稳定 | 不变 |

### 4.5 避免不必要的压缩

在 `SwitchService.switch()`（`backend/harness/switch.py:198`）进入摘要前增加快速通道：

- 目标 `context_length >=` 当前估算 token + 预留输出：无需摘要，直接切换；
- 目标窗口未知：拒绝（保持现状）；
- 只有目标窗口更小且确实放不下时才走 LLM 摘要与精确值探针。

当前代码用 `tokens > plan.threshold` 判断，理论上已覆盖“放不下”的情况，但阈值还包含输出预留与 buffer；建议明确区分「必须压缩」和「仅接近阈值」，避免切到大模型仍触发摘要。

### 4.6 与消息级模型兼容

若未来要完全对齐 opencode，可让 `session_messages` 的 user 行记录 `model` 字段（现在 assistant 已有 `event.model`）。好处：

- 历史重建时知道每条消息当时用的模型；
- 回溯/撤销后模型选择可恢复；
- 迁移不再需要“会话只有一个模型”的强假设；
- 与现有 `projector.py` 的 `message_parts` 投影兼容，只多一列元数据，不改正文。

可以先只加字段、不改变当前回合模型选择逻辑，作为后续能力。

---

## 5. 分阶段落地

### P0：去掉体感等待（1 天内）

- `ChatInput.pickModel` 不再 `await` 整个迁移；先 `selectedModel.value = value`。
- `persistSelectedModel` 删除 `loadModels(true)`、前后两次 `getSessionMessages`、末尾 `loadConversations`。
- 失败时回滚并提示；添加 `switching` 状态防重复提交。
- 预期：点击到标签变化从「秒级~十秒级」降到「一帧」。

### P1：后台迁移（1~2 天）

- 新增 `desired_model` / `model_switch_job` 状态；
- `PATCH` 返回 202 + job id；
- 复用现有 SSE 推送迁移状态；
- 前端显示迁移中/失败，允许取消或重选。

### P2：回合前惰性迁移（2~3 天）

- 回合入口检查 desired/active 差异；
- 迁移与 overflow 恢复共用 `harness/compression.py`；
- 支持运行中切换（只影响下一轮）；
- 补 `test_switch_*`、`test_turn_model_lazy_switch`、前端 reducer 单测。

---

## 6. 风险与测试

- **一致性风险**：后台迁移期间用户发送消息。处理方式：发送时等待 job；或本轮继续用旧模型并在 UI 标注「下一轮生效」。
- **并发切换**：用户连续点 A→B→C。只保留最新 desired_model，旧 job 可取消或完成后丢弃。
- **压缩成本重复**：切到大窗口时不要摘要；切回旧窗口时不要重复备份/摘要。
- **断线重连**：迁移状态应可通过 `GET /api/sessions/{id}/status` 恢复，类似现有 `active_turn`。
- **移动端**：`MobileShell.vue:120` 也调用 `persistSelectedModel`，前端改动要保证两端一致。
- **测试**：
  - 前端：`persistSelectedModel` 乐观更新/失败回滚；模型菜单立即关闭且标签立即更新；迁移中状态。
  - 后端：`tests/unit/test_harness_switch.py` 增加「无需压缩快速路径」「后台任务幂等」「并发切换最后一次胜出」。
  - 集成：运行中切换只影响下一轮；切换失败不污染 `session_messages`。

---

## 7. 代码索引

| 位置 | 作用 |
| --- | --- |
| `frontend/src/components/ChatInput.vue:31` | 模型选择回调，当前 await 整个切换 |
| `frontend/src/stores/multiAgent.ts:1299` | 前端切换全链路（目录刷新 + 双拉历史 + PATCH + 列表刷新） |
| `frontend/src/stores/multiAgent.ts:86` | `loadModels` stale-while-revalidate |
| `backend/app/api/sessions.py:100` | `PATCH /api/sessions/{id}` 触发同步切换 |
| `backend/app/deps.py:892` | `switch_model` 装配 `SwitchService` |
| `backend/harness/switch.py:142` | 同步迁移状态机 |
| `backend/harness/switch.py:198` | 超阈值时同步 LLM 摘要 |
| `C:\tool\document\opencode-dev\packages\app\src\context\local.tsx:292` | opencode 本地模型选择 |
| `C:\tool\document\opencode-dev\packages\app\src\components\prompt-input\submit.ts:155` | 模型随消息下发 |
| `C:\tool\document\opencode-dev\packages\opencode\src\session\prompt.ts:1104` | 服务端把模型写入 user message |
| `C:\tool\document\opencode-dev\packages\opencode\src\session\prompt.ts:1140` | 模型变化时发 `ModelSwitched` 事件 |

---

## 8. 一句话建议

**不要把「选模型」做成「迁历史」。** 选择应当是零等待的本地意图，迁移应当在后台或下一条消息前完成；opencode 已经证明这套模型级语义可以工作，AgentSuper 只需保留自己的备份/探针/原子提交约束，把执行时机从点击路径挪走即可。
