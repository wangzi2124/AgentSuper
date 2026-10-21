================================================================================
AGENT 设计补充：压缩模型（compress_model）与多 Agent 扇出（plan_model）
版本 1.1 ｜ 技术栈 LiteLLM + LangGraph + LangChain ｜ 配套 agent.md §4.3 / §6.2
================================================================================

【核心结论】
生产环境为控成本关闭的"额外 LLM 调用"，恰是 §4.3 九段式摘要与 §6.2 并行扇出生效的前提。
正确做法不是恢复原设计，而是给这两类额外调用设定：
  1) 严格触发门禁   2) 低成本模型档位   3) 会话级预算上限
原则：压缩 = 低成本高收益，可积极启用；扇出 = 高成本高收益，默认关闭、按需开启。


================================================================================
一、压缩模型（compress_model）
================================================================================

1.1 问题
  compress_model=None 时，context_compress_node 只缩消息数，context_summary 为空：
    - 精确值（路径/命令/数字）截断即丢，不可恢复
    - 无语义摘要，Supervisor 无法基于历史决策

1.2 模型选型：压缩是信息压缩任务，不是推理任务，禁用 complex tier

  +----------------------+---------------+---------------------------+------------------+
  | 压缩场景             | 模型 tier     | 触发条件                  | 预期成本         |
  +----------------------+---------------+---------------------------+------------------+
  | 常规压缩             | tier-simple   | token 总量 > 80K          | ~1K in + 500 out |
  | 紧急压缩             | tier-simple   | token 总量 > 150K         | 同上，max_tokens |
  |                      | (缩短输出)    |                           | 更低             |
  | 精确值保全校验       | 不调模型      | 每次压缩后                | 0                |
  |                      | (纯规则)      |                           |                  |
  +----------------------+---------------+---------------------------+------------------+

1.3 LiteLLM 配置：独立 model_name 隔离成本，不走 smart-router

  # litellm_config.yaml
  model_list:
    - model_name: compress-model
      litellm_params:
        model: openai/gpt-4o-mini
        api_key: os.environ/OPENAI_API_KEY

  # models/router.py
  COMPRESS_MODEL = "compress-model"

  def get_compress_model():
      return ChatLiteLLM(
          model=COMPRESS_MODEL,
          api_base="http://localhost:4000",
          api_key="sk-your-proxy-key",
          temperature=0.0,
          max_tokens=800,          # 硬上限，防摘要啰嗦
      )

1.4 触发门禁 should_use_llm_summary(state) -> bool
  全部条件为真才调模型：
    C1. 被压缩消息总 token >= 5_000（太少不划算）
    C2. 被压缩内容含精确值关键词：
        path / command / id: / 端口 / 版本 / token
    C3. 本会话 compress_count < 3（后续压缩改用规则截断）

1.5 压缩后精确值回填（零 LLM 成本，确定性保证）

  PRECISION_PATTERNS = [
      r'[/\w\-]+\.\w{2,4}',   # 文件路径
      r'\b\d{4,}\b',          # 长数字（ID、端口）
      r'`[^`]+`',             # 反引号命令
  ]

  def enforce_precision(summary, older_messages):
      original = "\n".join(str(m.content) for m in older_messages)
      critical = set()
      for pat in PRECISION_PATTERNS:
          critical.update(re.findall(pat, original))
      missing = [t for t in critical if t not in summary]
      if missing:
          summary += "\n\n[精确值补充]\n" + \
                     "\n".join(f"- {t}" for t in missing[:20])
      return summary

  效果：精确值召回从"依赖模型"变为"确定性保证"。

1.6 成本估算
  典型会话 5 次压缩，每次 8K in / 500 out，gpt-4o-mini 价格：
    5 * (8000*$0.15/1M + 500*$0.60/1M) ≈ $0.0075（不到一分钱）


================================================================================
二、多 Agent 扇出（plan_model）
================================================================================

2.1 问题
  plan_model=None 时 plan_node 恒走单任务快速路径，并行扇出在真实链路不可达。
  开启扇出：1 次 plan + N 次子任务 = 1+N 次额外调用，成本随 N 线性放大。
  => 不能无条件开启，必须按任务特征与预算决定。

2.2 三档计划策略（由 Supervisor 路由选择，不做二元开关）

  +------------------+-------------------------------+------------------+------------------+
  | 档位             | 条件                          | plan_model 行为  | 子任务执行       |
  +------------------+-------------------------------+------------------+------------------+
  | 单任务快速路径   | 描述 < 50 字 / 单一明确动词 /  | 不调用           | 直接 synthesize  |
  |                  | 用户未要求"分别/同时"          |                  | 或单次 execute   |
  | 串行分解         | 可分解但子任务有依赖          | tier-medium 生成 | 按序执行，前一   |
  |                  |                               | 有序计划         | 结果作后一输入   |
  | 并行扇出         | 子任务无依赖 + 子任务数>=2 +  | tier-medium 生成 | Send 动态扇出    |
  |                  | 预算允许                      | 并行计划         |                  |
  +------------------+-------------------------------+------------------+------------------+

2.3 LangGraph 动态扇出：用 Send（替代 §6.2 静态三路）

  from langgraph.types import Send

  def route_after_plan(state) -> list[Send] | str:
      plan = state["task_plan"]
      if not plan.get("parallel"):
          return "execute_subagent"            # 串行路径
      return [
          Send("parallel_worker", {
              "task": task,
              "tools": plan.get("tools_by_task", {}).get(task["id"], []),
          })
          for task in plan["tasks"]
      ]

  def parallel_worker(state, config):
      task = state["task"]
      result = execute_subagent(task)          # 子 Agent 独立上下文
      return {"sub_results": {task["id"]: result}}   # reducer 合并

  扇出数由 plan_model 输出决定，图中不写死路数。

2.4 预算守卫 should_plan_parallel(state) -> bool
  调 plan_model 之前判断，全部条件为真才走并行：
    C1. 本会话剩余预算 >= ¥0.20
    C2. 本会话 fanout_count < 2（防连续扇出烧钱）
    C3. 任务特征匹配并行：
        - 含串行信号（然后/接着/基于/根据以上）→ 走串行
        - 含并行信号（分别/同时/各自/并行/三个/多个）→ 走并行
        - 长描述性任务（>100 字且无问号）→ 可走并行
        - 其余 → 走串行/单任务

2.5 LiteLLM 兜底：即使误判也不失控

  litellm_params:
    max_budget_per_session: 0.50   # 单会话 ¥0.50 硬上限 → 429 熔断
    max_iterations: 40             # 含子 Agent 总调用次数上限

2.6 扇出子任务按类型绑定 tier（避免全部 complex）

  TASK_TIER_MAP = {
      "research": "medium",
      "code":     "complex",
      "file":     "simple",
  }

  典型三路并行成本 ≈ plan(medium) + research(medium) + file(simple) + code(complex)
  全部 complex 会翻 2~3 倍。


================================================================================
三、协同控制面（两类额外调用共用）
================================================================================

  +----------------+---------------------------+---------------------------+
  | 控制项         | 压缩模型                  | 扇出计划模型              |
  +----------------+---------------------------+---------------------------+
  | 模型 tier      | tier-simple（compress-    | tier-medium               |
  |                | model 专用）              |                           |
  | 触发门禁       | 精确值存在 + compress_    | 预算充足 + fanout_count<2 |
  |                | count<3                   | + 任务特征匹配            |
  | 单次输出上限   | max_tokens=800            | 计划 JSON 长度限制        |
  | 会话级熔断     | LiteLLM max_budget_per_   | 同上                      |
  |                | session 兜底              |                           |
  | LangFuse 标签  | stage=compress            | stage=plan_fanout         |
  +----------------+---------------------------+---------------------------+

  上线后观测：
    - 压缩模型单次平均成本
    - 扇出触发率（预期 < 10%）
    - 扇出 vs 非扇出单任务平均成本
  若扇出触发率 > 20%，收紧 should_plan_parallel 条件。


================================================================================
四、落地顺序
================================================================================

  Step 1 (1 天)   接入 compress-model + should_use_llm_summary 门禁
                  + enforce_precision 回填。改动小、风险低，§4.3 立即生效。
  Step 2 (2~3 天) plan_node 改三档策略，只实现"单任务"+"串行分解"，
                  不开启并行。验证 plan_model 成本与计划质量。
  Step 3 (1 天)   plan 质量确认后，用 Send 实现并行扇出，
                  加 should_plan_parallel 门禁 + LiteLLM session budget 兜底。
                  灰度开启（按用户/租户）。

【不要做】
  不要因为 §6.2 写了扇出就默认开启。扇出默认关闭，只对适合任务开放。
  默认路径必须是串行或单任务。压缩则可更积极启用。


================================================================================
五、一句话记忆
================================================================================

  压缩：simple tier + 规则门禁 + 精确值回填（低成本高收益，积极启用）
  扇出：medium tier + Send 动态扇出 + 预算守卫（高成本高收益，默认关闭）
  两者共用 LiteLLM session budget 兜底，开启额外调用也不会让成本失控。

================================================================================