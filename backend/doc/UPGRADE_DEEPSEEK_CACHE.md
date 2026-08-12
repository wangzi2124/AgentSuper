# 升级：DeepSeek 前缀缓存（Prefix Caching）

> 适用范围：`backend/` 服务端 LLM 调用链
> 关联文档：`docs/UPGRADE_SESSION_P8.md`（token 预算升级，二者互补）

## 目标

最大化 DeepSeek 前缀缓存命中率：命中的输入 token 按约 **0.1x** 计费（未命中按 1.0x），
是当前单请求成本大头（system + tools + 历史 + RAG 上下文）最直接的降本手段。

## 现状核对（代码事实，2026-08 已确认）

- 入口：`app/agent/graph.py` `RAGAgent._llm_call()`，调用 `litellm.acompletion(...)`，
  当前**未传** `cache_prompt` 参数（`backend/app/config.py` 中 `llm_model="deepseek-chat"`、
  `llm_api_base="https://api.deepseek.com"`、`llm_api_key` 为 None）。
- system prompt：基础模板在 `_generate` 中组装，`state["_cwd"]` 追加在末尾。
  仅 `refresh_tools()`（技能/插件/pin 热更新）触发 system prompt 重建。
  → 同一工作目录 + 未热更新时 system prompt 字节级稳定。
- history：`messages.extend(state["history"])` 注入在 system 之后、user 问题之前。
- RAG 上下文：由 `_retrieve` / `_rerank` 产出，拼入 user 消息前缀，
  不进入 system prompt（符合"稳定内容放 system、易变内容放 user"的前缀缓存最佳实践）。
- 工具循环内：`tool` 结果 / doom-loop / MAX_STEPS 提示均追加在消息尾部；
  `_truncate_messages` 超预算时从最早非 system 消息开始裁剪（会中断缓存，属预期权衡）。

### 缓存破坏因子清单（根因分析）

| # | 因子 | 位置 | 影响 | 处置 |
|---|------|------|------|------|
| 1 | 未开启 `cache_prompt` | `_llm_call` | 缓存完全不生效 | ★ 本升级核心改动 |
| 2 | system 尾部注入 `_cwd` | `_generate` 组装 | 跨目录失效 | 接受（同目录稳定；即使变化仅尾部短前缀） |
| 3 | 工具定义动态筛选 | `_build_tool_defs(state["question"])` | schema 随问题变化 | 评估静态化（见下"后续优化"） |
| 4 | RAG 结果拼 user 前缀 | `_retrieve`/`_rerank` | 随问题变化 | 已符合最佳实践，不改 |
| 5 | 超预算裁剪历史 | `_truncate_messages` | 中断前缀 | 预期权衡，不改 |

## 改动项

### 1. `_llm_call` 增加 `cache_prompt=True`（★ 必改）

```python
response = await litellm.acompletion(
    model=model,
    messages=messages,
    tools=tool_defs,
    temperature=0.1,
    max_tokens=settings.llm_max_tokens,
    timeout=500,
    num_retries=2,
    stream=True,
    stream_options={"include_usage": True},
    cache_prompt=True,   # ★ 新增：打开 DeepSeek 前缀缓存
)
```

- `cache_prompt=True` 为 DeepSeek 显式开关；未命中时无额外开销。
- **实施时复核**：`_llm_call` 中若存在 stream 失败回退非流式的异常分支（独立 `acompletion`
  调用），需同步加 `cache_prompt=True`。

### 2. 记录缓存命中指标（可观测性，建议）

- DeepSeek 响应 usage 含 `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens`
  （litellm 透传；原生字段为 `prompt_tokens_details.cached_tokens`）。
- 建议在 `record_model_call` 或 token trace 中打印 hit/miss 字节数，便于验证降本效果。

## 约束与注意事项（写进代码注释，防止回归）

1. system prompt 内**不得注入动态内容**（时间戳、随机 ID、易变的全局状态）。
2. `_cwd` 保持在 system prompt **末尾**（同目录内稳定；即使变化也只影响尾部短前缀，
   命中损失极小，可接受）。
3. 热更新（pin 工具 / 技能 / 插件变更）会重建 system prompt → 此后缓存短暂失效，属预期。
4. 多轮工具循环中 `_truncate_messages` 裁剪会中断缓存：该行为是"保留近端上下文"
   的收益与缓存命中之间的既有权衡，不因本次升级而改变。
5. DeepSeek 缓存规则（官方口径）：前缀需达到最小长度（≥64 tokens）才会命中；
   缓存有效期非线性（短则数分钟、一般数小时）。长 system prompt + 稳定前缀正是高命中场景。

## 后续优化（本期不做，仅记录）

- **工具定义静态化**：`_build_tool_defs(state.get("question",""))` 目前按问题关键词筛选工具，
  schema 随问题变化，会破坏 tools 段缓存。若工具总量可控，可改为全量静态工具定义；
  若必须筛选，可将工具列表从 system/tools 段移到 user 消息内（随问题变化，不再污染前缀）。
- **`_writable_workspaces()` / 动态工作区列表**注入 system 的部分，评估是否移入 user 前缀。

## 验证方法

1. 连续两次同一目录提问，观察第二次起 usage 中 `prompt_cache_hit_tokens` > 0。
2. 对比开启前后的成本估算（命中部分按 0.1x 计入 cost 统计）。
3. 回归：流式与非流式路径均正常返回；多轮工具调用、强制收尾路径不受影响。
4. 切换目录后再提问，确认 `prompt_cache_miss_tokens` 回升（预期行为）。

## 回滚

移除 `_llm_call` 中的 `cache_prompt=True` 即可。无其他代码或数据结构变更。
