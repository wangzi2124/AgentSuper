# 第五/六波 Token 优化补丁 v5+v6 — 变更说明与核查报告

日期: 2026-08-11
执行: `python token_patch/apply_token_patch6.py`（应用后重启后端服务）
覆盖范围: v5（按需挂载）+ v6（自定义工具 Skill 添加接口）

---

## 一、核查结论（数值核对 + 对齐验证）

### ✅ 预算数值（`app/context/budget.py` 实际运行值）

| 配置项 | 值 | 说明 |
|---|---|---|
| `max_context_tokens` | **32_000**（v5 前 48_000） | `app/config.py:52` |
| `context_reserve_tokens` | 8_192 | `app/config.py:53` |
| `usable_context_tokens()` | **23_808** | = max(0, 32_000 − 8_192)，`budget.py:20` |
| `compaction_threshold_tokens()` | **19_046** | = 0.8 × usable（配置为 0 时自动），`budget.py:30` |
| `chat.py MAX_HISTORY_TOKENS` | 32_000 | v3 已设，本波未动 |

> 单次调用上下文天花板较 v5 前净降 33%（48K→32K）；压缩先于截断触发（19K 阈值），信息不丢。

### ✅ 脚本与源码完全对齐（沙盒重放验证）

对 `git HEAD~1`（v5/v6 应用前的基线）在沙盒中重放 `apply_token_patch6.py`
（自动先应用 v5 再应用 v6），产出文件与当前已提交状态**逐字节比对**：

| 文件 | 结果 |
|---|---|
| `app/config.py` | 逐字节一致 |
| `app/agent/graph.py` | 逐字节一致 |
| `app/runtime.py` | 逐字节一致 |
| `main.py` | 逐字节一致 |
| `app/skills/custom_tools.py` | 逐字节一致 |
| `app/api/custom_tools.py` | 逐字节一致 |

即：`--rollback` 后重新 `apply` 可精确复现当前状态；`--verify` 11/11 通过。

### ✅ 修复的脚本问题（本波脚本自身）

1. **导入即崩溃**：`_TEMPLATE` 用 `'''` 定界，提前终止外层 `r'''...'''` 原始字符串 → `NameError: name 'script' is not defined`；改 `"""` 定界 + 内层 docstring 转义。
2. **`.format()` 脆弱**：用户脚本含 `{}`（dict/f-string）会抛 `ValueError`；改 `.replace()` 链，`{script}` 最后替换，用户代码不再被二次扫描。
3. **`_data_dir` 未定义**：v6 补丁把 `CustomToolStore` 插在 `_data_dir` 定义之前 → 启动 `NameError`；锚点改到 `_data_dir` 之后。
4. **`--verify` 误报**：追加式补丁（new = old + 新增行，如 V6-1/5/7/8/9、P5-3/7）应用后 old 仍在 → 先查 `new in text` 再查 `old not in text`（patch5/patch6 同步修正）。
5. **性能**：`_pinned_tool_names()` 从工具循环内提升到循环外，避免每工具每轮重读 `pinned_tools.json`。

---

## 二、v5 补丁清单（按需挂载，9 项 / 3 个文件）

| # | 文件 | 改动 | 目的 |
|---|---|---|---|
| P5-1 | `app/config.py` | `max_context_tokens` 48K → 32K | 单次调用天花板 −33% |
| P5-2 | `app/agent/graph.py` | `_build_tool_defs` 改为按需挂载（核心工具常驻 + 意图关键词命中 + 已使用保留） | schema 固定开销 8-12K → 2-4K |
| P5-3 | `app/agent/graph.py` | 新增 `_bound_plugin_result()`：天气/台风类结果 >1500 字符截断 | 避免大块结构化数据每轮重发 |
| P5-4/5 | `app/agent/graph.py` | `_execute_tool` 两处 return 接 `_bound_plugin_result` | 同上 |
| P5-6 | `app/agent/graph.py` | 首轮 `_build_tool_defs(state.question)` | 首轮即按关键词筛 schema |
| P5-7 | `app/agent/graph.py` | 初始化 `used_tools` 集合 | 已使用工具保留挂载 |
| P5-8 | `app/agent/graph.py` | 循环内累积 `used_tools` | 同上 |
| P5-9 | `app/agent/graph.py` | 每轮重挂载 tool_defs（核心 + 意图 + 已使用） | 多轮工具循环 schema 不再全量重发 |

> 意图关键词表（`_INTENT_RULES`）：天气/文档/网页/搜索/图片/语音/角色/知识库/代码/技能/插件/教学/研究/模型/架构 共 15 类。
> 未命中工具仅出现在 system prompt 工具名列表；若模型调用未挂载工具，`_execute_tool` 仍可执行（`self.tools` 全量），下轮自动保留。

---

## 三、v6 补丁清单（自定义工具，9 项 + 2 新文件 / 5 个文件）

| # | 文件 | 改动 |
|---|---|---|
| 新文件 | `app/skills/custom_tools.py` | `CustomToolStore`：脚本型写 `plugins/custom_<name>.py`（复用 PluginLoader 链路），固定型写 `data/pinned_tools.json` |
| 新文件 | `app/api/custom_tools.py` | REST API：`GET /api/custom-tools/`、`/catalog`、`POST /script`、`POST /pin`、`POST /{name}/toggle`、`DELETE /{name}`（写操作 `require_admin`） |
| V6-1 | `app/agent/graph.py` | import `CustomToolStore` |
| V6-2 | `app/agent/graph.py` | `RAGAgent.__init__` 接收 `custom_tools` |
| V6-3 | `app/agent/graph.py` | `_build_tool_defs` 尊重 pinned 列表（固定工具始终挂载 schema） |
| V6-4 | `app/agent/graph.py` | 新增 `_pinned_tool_names()`（pinned 集合只读一次） |
| V6-5 | `app/runtime.py` | 创建 `CustomToolStore`（锚定在 `_data_dir` 之后） |
| V6-6 | `app/runtime.py` | 注入 `RAGAgent(..., custom_tools=custom_tools)` |
| V6-7 | `app/runtime.py` | `app.state.custom_tools = custom_tools` |
| V6-8 | `main.py` | import `custom_tools as custom_tools_api` |
| V6-9 | `main.py` | 挂载 `/api/custom-tools` 路由 |

### 前端接线（`frontend/src/`）

- `api/customTools.ts`、`stores/customTools.ts`、`types/customTools.ts`（PATCH6 草案安装）
- `views/CustomToolsView.vue`：脚本创建（名称/描述/源码/启用）+ 固定已有工具（目录下拉，已固定剔除）+ 启停/删除
- `router/index.ts`：新增 `/custom-tools`；`components/Sidebar.vue`：新增「Custom Tools」导航项
- 构建验证：`npm run build`（vue-tsc + vite）通过

---

## 四、预期收益

- **每次调用基数 −30~40%**：schema 固定开销 8-12K → 2-4K（多轮工具循环收益最大）。
- **上下文天花板 −33%**：48K → 32K，usable 23.8K，压缩阈值 19K（信息不丢，先压后截）。
- **固定工具兜底**：用户关心的工具可 pin 常驻，彻底消除「模型看不到工具」的顾虑。

---

## 五、回滚

```bash
cd E:\AgentSuper\backend
python token_patch/apply_token_patch6.py --verify    # 校验（v6 11 项）
python token_patch/apply_token_patch6.py --rollback  # 回滚 v6，再自动回滚 v5
python token_patch/apply_token_patch5.py --verify    # 单独校验 v5（9 项）
```

前端回滚：删除 `frontend/src/{api,stores,types}/customTools.ts`、`views/CustomToolsView.vue`，
还原 `router/index.ts` 与 `components/Sidebar.vue`。

---

## 六、核查记录 / 备注

- 启动冒烟：`ensure_runtime_state` 全量运行成功，`agent.custom_tools is app.state.custom_tools` 为 True，39 skills + 21 plugins 正常加载。
- 固定型生命周期（create→toggle→remove）实测通过。
- 回滚备份：`*.bak_token_patch5` / `*.bak_token_patch6`（v5 备份在 v5 单独应用时生成）。
- 注意：`git show HEAD:...` 取到的是已应用版本（当前 HEAD 已含 v5+v6）；做对齐基线对比需用 `HEAD~1`。
