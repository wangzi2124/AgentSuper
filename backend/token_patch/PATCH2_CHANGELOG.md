# 第二波 Token 优化补丁 v2 — 变更说明与核查报告

日期: 2026-08-11
执行: `python token_patch/apply_token_patch2.py`（应用后重启后端服务）

---

## 一、核查结论（提示词 / Skill / Plugin 加载链路）

### ✅ 无问题项

| 检查点 | 结论 |
|---|---|
| Skill 加载 | `runtime.py _do_init` 启动时 `SkillLoader.load_all()` **仅执行一次**（`ensure_runtime_state` 双检锁单例），热更新仅在管理员 toggle API 时按需触发 → **不存在每次请求重复扫描** |
| Plugin 加载 | 同上：启动一次 + toggle 时按需 `load_all()` + `refresh_tools()` → 无每次请求重复 exec_module |
| 主 Agent 工具循环 | `graph.py _generate` 每轮已有 `_truncate_messages` + `sanitize_tool_messages` + 工具输出 bound，无上下文裸奔 |
| 子 Agent 循环 | 第一波补丁已生效：轮数 8→5、工具结果 1500 字符截断、`_trim_messages` 每轮调用（第 319 行） |

### ⚠️ 发现的问题（本波补丁已修复）

1. **【核心】RAG 上下文拼在 system 消息内部**
   - 原代码：`full_system_prompt = _system_prompt_with_kb() + "\n\n" + "Retrieved Context:\n{context_text}"`
   - 后果：检索结果每次请求都不同 → **system 消息整体变化 → DeepSeek 前缀缓存（context caching）完全失效**，即使系统提示词本身是稳定模板也命中不了缓存，等于每次请求都按全价重付 system + 历史前缀。
   - 修复：system 恒为稳定模板；RAG 上下文改拼到 **user 消息前缀**（行为等价，模型仍能看到检索内容）。
   - 收益：**system + 固定历史前缀可命中缓存（命中 token 按 0.1× 计费）**，多轮对话与工具循环收益显著。

2. **【次要】RAG 检索片段数偏多**
   - `retriever.invoke(k=5)` → `k=3`，每次检索少注入约 2 段文档内容（每段数百 token）。

3. **【次要】子 Agent 上下文仍偏宽松**
   - 软上限 16K→12K、保留轮数 4→3、工具轮数 5→4（3 轮工具 + 1 次收尾，够用）。

---

## 二、本波补丁清单（6 项 / 2 个文件）

| # | 文件 | 改动 | 目的 |
|---|---|---|---|
| A1 | `app/agent/graph.py` | system 消息不再拼接 RAG 上下文，恒为稳定模板 | 命中 DeepSeek 前缀缓存（0.1×） |
| A2 | `app/agent/graph.py` | RAG 上下文移到 user 消息前缀 | 配合 A1，行为等价 |
| B | `app/agent/graph.py` | 检索 k=5 → k=3 | 减少注入内容 |
| C1 | `app/agent/sub_tools.py` | `SUB_AGENT_MAX_ROUNDS` 5 → 4 | 减调用次数 |
| C2 | `app/agent/sub_tools.py` | `_SUB_CTX_MAX_TOKENS` 16K → 12K | 防上下文膨胀 |
| C3 | `app/agent/sub_tools.py` | `_SUB_CTX_KEEP_ROUNDS` 4 → 3 | 同上 |

> 说明：`_system_prompt_with_kb` 方法保留定义不再被调用（无副作用）；`context_text` 在无检索时初始化为空串，不影响无知识库场景。

---

## 三、预期收益

- **缓存命中**：有知识库 + 多轮对话 + 工具循环场景，system 与历史前缀稳定 → 输入 token 大幅转为 0.1× 计费。
- **注入减少**：每次检索少 2 段、子 Agent 每请求少 1 次调用、上下文上限压缩。
- 综合预期：**再降 20%~40%**（叠加第一波 40~60% 后，总体可比原状态降 60%~75%）。

---

## 四、回滚

```bash
cd E:\AgentSuper\backend
python token_patch/apply_token_patch2.py --verify    # 校验
python token_patch/apply_token_patch2.py --rollback  # 回滚（恢复 .bak_token_patch2 备份）
```

---

## 五、后续可选（未包含在本波）

- **系统提示词瘦身**：`build_system_prompt_no_kb`（tools.py）instructions 偏长（约 2.5KB），可压缩 30%；但改动会触发一次 cache 重写，且影响模型行为，**建议在缓存收益稳定后再做**。
- **Plugin 增量加载**：`PluginLoader.load_all()` 每次全量 `exec_module`，仅 toggle 时发生（非 token 热点），如插件数量多可改为按模块时间戳增量加载。
- **记忆/历史**：chat.py `MAX_HISTORY_TOKENS` 已由第一波降到 48K；如仍偏高可再降 32K。
