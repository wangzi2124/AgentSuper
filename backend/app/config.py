from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """应用配置，从 .env 文件读取环境变量。"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    llm_model: str = "deepseek-chat"
    llm_api_key: Optional[str] = None
    llm_api_base: Optional[str] = "https://api.deepseek.com"

    vector_store_path: str = "data/vector_store"
    upload_dir: str = "data/uploads"

    chunk_size: int = 500
    chunk_overlap: int = 200

    embedding_model: str = "BAAI/bge-small-zh-v1.5"

    enable_reranker: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ── 模型下载容错（C3）──
    # 单次 snapshot_download 调用超时（秒）；0 = 不设整体超时（依赖底层库自身重试/续传）
    model_download_timeout: int = 600
    # 单个下载源（ModelScope / HuggingFace）内的重试次数（不含首次尝试）
    model_download_retries: int = 2

    skills_dir: str = "skills"
    plugins_dir: str = "plugins"

    summarization_model: Optional[str] = None
    summarization_api_key: Optional[str] = None
    summarization_api_base: Optional[str] = None
    summarization_keep_messages: int = 20

    # 多 Agent 超时（秒）
    # supervisor 转发到子 Agent 的等待上限（单次生成可能因 LLM 延迟/tool 循环超 60s）
    sub_agent_timeout: float = 150.0
    # 端点层等待 supervisor 返回的上限（需 > decompose + sub_agent + synthesize）
    supervisor_timeout: float = 300.0

    # 每次 LLM 调用的输出 token 上限（对齐 opencode transform.ts:maxOutputTokens 的"默认给足"设计）。
    # 默认 16_384 ≈ 模型原生上限的常用值；长任务配合系统提示"长内容写文件"规则避免截断。
    # [token 优化 v9] 16_384 → 8_192：普通问答用不到那么大输出，压低超长输出兜底成本。
    llm_max_tokens: int = 8_192

    # Token 成本控制
    # 每次 LLM 调用允许的最大上下文（system + history + 当前问题）
    # 对齐 opencode overflow.ts：usable = max_context_tokens - context_reserve_tokens
    # [token 优化 v5] 48K → 32K：配合 v4 压缩（信息不丢），单次调用天花板 -33%
    # [token 优化 v9] 32K → 24K：usable ≈ 15.8K，进一步压平单次调用体积（配套 MAX_HISTORY_TOKENS 16K）
    max_context_tokens: int = 24_000
    # 输出预留：留给模型回答的 token（≈ min(20_000, maxOutputTokens)，默认 8_192）
    context_reserve_tokens: int = 8_192
    # [token 优化 P8] cl100k_base 对 DeepSeek tokenizer 系统性低估（实测 +13.2%：
    # round8 估算 20,851 vs 实际 23,599）。用于 token_counter 估算校正，避免
    # 截断/压缩判断"以为没超、实际已超"（实测 round9 实际 25,779 超 usable 23,808）
    token_estimate_correction: float = 1.13
    # [token 优化 P8] 压缩触发比例：usable × ratio。原 0.8 实测 round 8 才触发、
    # 压缩后下一轮仍超限；降到 0.65 提前 2-3 轮介入，压平长工具循环 token 曲线
    # [token 优化 v9] 0.65 → 0.6：usable 降为 15.8K 后保持"压缩早于截断"的窗口
    compaction_threshold_ratio: float = 0.6
    # 压缩触发阈值（token）；0 表示自动 = usable × compaction_threshold_ratio，长工具循环在截断兜底之前先压缩
    compaction_threshold_tokens: int = 0
    # 压缩时尾部保留的最近轮次（对齐 opencode tail_turns，默认 2）
    context_tail_turns: int = 2
    # 尾部保留的 token 预算（对齐 opencode preserve_recent_tokens，默认 8_000）
    context_preserve_recent_tokens: int = 8_000
    # 回溯式工具输出清理：最近 N 轮之内累计工具输出超过该值时，清理更旧输出。
    # 注意：这里只是「原始配置值」，实际生效值由 budget.py 与压缩阈值联动钳制
    # （protect = min(配置值, compaction_threshold_tokens()//2)，minimum = protect//2），
    # 保证 prune 先于压缩回收旧输出（[token 优化 v12]）。
    tool_output_protect_tokens: int = 24_000
    # 清理收益低于该值时不做（避免微小收益的频繁改写）；生效值同样经联动钳制。
    tool_output_prune_minimum_tokens: int = 12_000
    # [C5 长任务上下文越界根治] 单次 LLM 调用预算的安全系数：截断/压缩目标
    # 使用 usable × context_safety_ratio（默认 0.9），为估算误差预留 ≥10% 余量，
    # 避免「截断按估算放行、Provider 按实际拒绝」。见 budget.llm_call_budget。
    context_safety_ratio: float = 0.9
    # [C5] 压缩目标系数：压缩后的上下文应尽量落到 usable × compaction_target_ratio
    # 之下（默认 0.5），保证压缩后仍有充足余量继续多轮工具循环。
    compaction_target_ratio: float = 0.5
    # [C5] 单条工具输出 token 上限（对齐 opencode 按 token 截断语义）：中文场景
    # 32KB 字符 ≈ 48K token 单条过大，bound_tool_output 在字符/行限之外再按 token
    # 封顶，超限写盘 + 续读提示。0 = 不启用 token 封顶。
    tool_output_max_tokens: int = 8_000
    # [C5 · 方案 D 小步快走] 长任务周期性地把旧轮次压成摘要（HierarchicalSummarization
    # Middleware），上下文只装 [摘要 checkpoint + 最近一轮 + 当前步]，不随轮次线性膨胀。
    step_summary_enabled: bool = True
    # 从第几步起进入小步快走（短任务保持 in-loop 快路径，不做多余摘要调用）
    step_summary_min_rounds: int = 3
    # 每隔几步做一次摘要替换（间隔 1 = 每轮都压，成本高；间隔 2 = 折中）
    step_summary_interval: int = 2
    # 摘要后原样保留的最近消息条数（覆盖最近一轮 assistant+tool 结果）
    step_summary_keep_messages: int = 4
    # [C5 · 方案 E/F 多请求接力] 长任务小步快走：code 子 Agent 对多步骤实现类任务
    # 先拆计划、每步一个独立 fresh-context 请求执行，步间只传落盘 STEP_STATE
    # （上下文永不膨胀）。默认开启（经 LONG_TASK_MIN_QUESTION_CHARS 门控，短问题
    # 不触发规划调用，零额外开销）；真实 API 冒烟已验证。
    long_task_step_mode: bool = True
    # 接力规划门控：问题字符数低于该阈值时不拆计划（保持普通单请求路径）
    long_task_min_question_chars: int = 30
    # 接力时计划最多拆几步
    long_task_max_steps: int = 6
    # ── [F8/F9] 图片上传解析给模型（多模态附件管线）──
    # 上传图片先规格化（缩放+JPEG 压缩）再投递给模型，避免大图 base64 撑爆上下文。
    image_max_dimension: int = 1024          # 长边缩放上限（px）
    image_max_kb: int = 512                  # 压缩后单图字节上限（KB）
    image_token_cap: int = 6000              # 单请求图片 token 预算上限（超限进一步降采样）
    image_thumbnail_px: int = 256            # 回显/缓存缩略图边长
    image_use_ocr: bool = False              # 图片 OCR 提取文本（截图/文档图，需 OCR 库）
    image_vlm_caption: bool = True           # 图片描述桥：视觉 LLM 生成 caption 注入文本
    image_caption_model: str = "openai/gpt-4o-mini"  # caption VLM（litellm 前缀；内网改 ollama/llava）
    image_caption_api_base: str = ""
    image_caption_api_key: str = ""
    image_caption_prompt: str = ""           # 空用默认「用中文简洁描述图片内容与关键细节」
    image_caption_timeout: int = 15          # caption 调用超时（秒），失败快速降级
    # 摘要中间件缓存大小（按历史分块缓存，避免每请求全量重算）
    summarization_cache_size: int = 200

    # 可选：管理端鉴权 token。设置后插件 toggle/call、权限审批等敏感接口需携带
    # Authorization: Bearer <token>；不设置时这些接口仅允许本机来源（deps.require_admin）。
    admin_token: Optional[str] = None

    # ── 向量库 / 章节库清理 ──
    # 启动时清空全部向量库 + 章节库 + BM25 + 上传文件（默认 false，谨慎开启）
    vector_store_auto_clear: bool = False
    # 数据保留天数（TTL）：>0 时按文档创建时间定期清理过期数据；0 = 不启用
    vector_store_ttl_days: int = 0
    # 定时清理检查间隔（小时），配合 VECTOR_STORE_TTL_DAYS 使用
    vector_store_cleanup_interval_hours: int = 24

    # ── 并发控制 ──
    # 全局同时执行的 Agent 任务上限（chat.py `_agent_semaphore`，多 Agent 请求排队）。
    # 默认 4：RAG Agent 单次执行主要耗时在 LLM 调用（I/O 等待），提高并发不会打满 CPU，
    # 但 SQLite 连接与 ChromaDB 会有锁竞争，需配合 session.db 连接池（app/session/db.py）。
    max_concurrent_agents: int = 4

    # CORS 允许的源（JSON 数组，环境变量 CORS_ORIGINS）。默认仅本机前端
    # （vite dev 5173 / preview 4173），避免局域网/公网页面调用本服务接口。
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
    ]

    # ── 权限 / 工作区（对齐 opencode external_directory 设计）──
    # 可写工作目录由前端「工作目录」面板配置（运行时生效，持久化到 data/runtime_workspaces.json）。
    # external 路径（工作区/临时目录之外）的默认策略：ask | allow | deny
    external_path_default: str = "ask"
    # 权限审批等待超时（秒），默认 60；超时视为拒绝
    permission_approval_timeout: int = 60
    # 是否放行主工作区内受保护源码路径（app/、plugins/、skills/、config/、main.py 等）的写/执行。
    # 默认 false：这些路径硬保护，添加再多工作区也不能改；
    # 设为 true 后（如开发本系统自身时）允许 Agent 修改 backend 源码。
    # .git、.env、*.db、permissions.json 始终保护，不受此开关影响。
    allow_source_writes: bool = False

    # ── Agent 执行循环护栏（对齐 opencode prompt.ts / processor.ts / max-steps.ts）──
    # 主步骤上限（对齐 opencode agent.steps，默认 40）：到达上限的最后一轮注入收尾提示，
    # 并禁用工具，强制"已完成/未完成/下一步"式总结。生效上限 = min(MAX_STEPS, MAX_TOOL_ROUNDS)。
    max_steps: int = 24
    # 硬兜底：单次请求内最多 LLM 调用轮数（每轮都是一次完整 LLM 调用）。
    # 当 MAX_STEPS >= MAX_TOOL_ROUNDS 时，MAX_STEPS 生效上限即等于该值。
    # [token 优化 v9] 16 → 8：每轮工具调用都会重发整段上下文，减少轮数即减少 token 累积。
    max_tool_rounds: int = 8
    # Doom-loop 检测：同一组工具调用指纹连续重复 N 轮后，注入策略变更提示（≥2）
    doom_loop_threshold: int = 3
    # Doom-loop 升级：首次提示之后，再次连续触发 N 次相同指纹即强制收尾（注入 MAX_STEPS_PROMPT + 禁用工具），
    # 对齐 opencode processor.ts 的 permission.ask(doom_loop) → deny 后 stop 语义
    doom_loop_max_strikes: int = 2
    # 工具密集型子 Agent（如 code）的更长等待超时（秒），避免长任务被误判超时
    sub_agent_timeout_extended: float = 300.0
    # 使用 extended 超时的子 Agent 列表（逗号分隔）
    extended_timeout_agents: str = "code"
    # 子 Agent 委派嵌套深度上限（对齐 opencode subagent_depth，默认 1 = 主 Agent 只能再委派一层）
    subagent_depth: int = 1

    # ── 共享记忆持久化 ──
    # 非空时 MemoryManager 将未过期记忆落盘到该文件，重启不丢失
    memory_persist_path: str = "data/agent_memory.json"
    # tool_memory_set 记忆的有效期（秒），默认 300（5 分钟）
    memory_ttl_seconds: int = 300

    # ── 用户身份签名（可选，默认关闭）──
    # 设置 AUTH_TOKEN_SECRET 后启用：X-User-Id 必须携带对应的签名 token
    # （前端通过 /login 页面注册账号/登录后换取 token），
    # 防止仅伪造 X-User-Id 头越权读取他人会话。默认（本地部署）不校验。
    # validation_alias 保证 .env 中的 AUTH_TOKEN_SECRET 正确映射到该字段。
    auth_secret: Optional[str] = Field(default=None, validation_alias="AUTH_TOKEN_SECRET")
    # token 有效期（秒），默认 30 天
    auth_token_ttl: int = 2592000
    # 已注册用户（user_id → 设备密钥哈希）的持久化文件
    auth_users_path: str = "data/auth_users.json"


settings = Settings()
