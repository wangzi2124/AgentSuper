from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """应用配置，从 .env 文件读取环境变量。"""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent / ".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    # ── LLM 基础连接（.env 兜底凭证）──
    # 请求未指定模型时的默认对话模型（models/router.py:TierModel._direct_model 的
    # 最低优先级兜底，优先级：请求选中 > TIER_{tier}_MODEL > 本字段）；无 "/" 前缀时
    # 按 api_base 自动补 deepseek/ 或 openai/ 前缀。后台任务/无请求上下文的调用也落这里。
    llm_model: str = "deepseek-chat"
    # 直连（无 LITELLM_PROXY_URL）时的 API key 兜底：模型目录 Provider 注册的凭证
    # （「模型管理」页保存）优先，目录未配时回落本字段。
    llm_api_key: Optional[str] = None
    # 同上的直连端点兜底（litellm base_url）；同样目录 Provider 优先。
    llm_api_base: Optional[str] = "https://api.deepseek.com"

    # ── 持久化后端（session.db / model_catalog.db / chapter_store.db / tasks.db 的统一后端）──
    # DB_TYPE: sqlite | mysql | postgresql
    # - sqlite（默认）：各子系统沿用各自 data/*.db 文件（建表零回归，测试/迁移工具不变）。
    # - mysql / postgresql：四个子系统的表统一落在同一个 DB_URL 数据库内，schema 由
    #   Alembic 迁移链管理（backend/alembic，`upgrade head` 在启动时执行）。
    # DB_URL 优先；未提供时按 DB_HOST/DB_PORT/DB_USERNAME/DB_PASSWORD/DB_NAME 拼装。
    db_type: str = "sqlite"
    db_url: Optional[str] = None
    db_host: str = "127.0.0.1"
    db_port: int = 0  # 0 = 按类型取默认（mysql 3306 / postgresql 5432）
    db_username: str = "root"
    db_password: str = ""
    db_name: str = "agentsuper"

    # ChromaDB 持久化目录（相对 backend/，runtime.py:VectorStore.load 加载）；
    # 结构化清理（清空/过期/单文档删除）见 app/services/kb_cleanup.py。
    vector_store_path: str = "data/vector_store"

    # 文档入库分块参数（runtime.py:DocumentProcessor）：块字符数与重叠字符数。
    # 章节感知切分：按「第X章/Chapter X」边界优先断开，chapter_title/number 入元数据
    # （cognition/document_processor.py）。
    chunk_size: int = 500
    chunk_overlap: int = 200

    # §13.2.1 强制 metadata 字段 department（部门/租户，权限过滤用）。
    # 单租户部署保持默认；多租户可在入库时按请求覆盖。
    kb_department: str = "default"

    # 本地 embedding 模型（cognition/embeddings.py:LocalEmbeddings）。
    # ModelScope 优先下载（HF 兜底）；英文回退 all-MiniLM-L6-v2。
    embedding_model: str = "BAAI/bge-small-zh-v1.5"

    # 重排序开关与模型（cognition/reranker.py）。构造/rerank 失败时管道自动降级
    # （不重排、保持向量+BM25 RRF 融合顺序），不会崩启动或查询。
    enable_reranker: bool = True
    reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # ── 模型下载容错（C3）──
    # 单次 snapshot_download 调用超时（秒）；0 = 不设整体超时（依赖底层库自身重试/续传）
    model_download_timeout: int = 600
    # 单个下载源（ModelScope / HuggingFace）内的重试次数（不含首次尝试）
    model_download_retries: int = 2

    # 插件发现目录（相对 backend/，extensions/plugins.py:PluginLoader 扫描 *.py，
    # tool_* 函数自动注册；启用状态看同名 .enabled 文件）。
    plugins_dir: str = "plugins"

    # ── 历史摘要配置（app/api/config.py 的 GET/POST /api/config/summarization，require_admin）──
    # ⚠ 接线现状：仅被该管理 API 读写（前端集成测试 api-live.spec.ts 覆盖），实际压缩
    # 路径（graph/nodes/compress.py + cognition/compressor.py）**不读取本字段** ——
    # 压缩节点的模型经 models/router.get_model("simple") 取 tier 模型，阈值/保留量是
    # compressor.py 模块常量。改这里只改 API 回显，不改压缩行为；要生效需改 compress 节点。
    # 非空语义（API 层）：model 非空视为 enabled=true。
    summarization_model: Optional[str] = None
    # 历史摘要保留消息条数（POST 时强制 ≥1；当前仅 API 回显，见上方接线现状说明）。
    summarization_keep_messages: int = 20

    # ── 压缩模型（agent-1.md §1，对齐 agent.md §4.3 九段式摘要）──
    # 压缩是「信息压缩」而非推理任务 → 固定用 simple tier（禁用 complex）。
    # 生产可用 LITELLM_PROXY_URL + COMPRESS_MODEL 独立别名隔离成本（§1.3）；
    # 直连环境不设 proxy 时，别名会落到 provider_api；故默认留空 → 走 tier-simple。
    compress_model: Optional[str] = None
    # 是否启用 LLM 摘要压缩（默认 true：压缩=低成本高收益，可积极启用，§核心结论）。
    # 关闭时压缩节点仅按规则截断（缩消息数，不调模型），与旧行为一致。
    compress_model_enabled: bool = True
    # 单次摘要输出硬上限，防摘要啰嗦（§1.3 max_tokens=800）。
    compress_max_tokens: int = 800
    # 触发 LLM 摘要的最小被压缩内容 token（太少不划算，§1.4 C1）。
    compress_min_tokens: int = 5_000
    # 单会话内允许的 LLM 摘要次数上限；超出后改用规则截断（§1.4 C3）。
    compress_max_llm_calls: int = 3

    # ── 计划模型 / 多 Agent 扇出（agent-1.md §2，对齐 agent.md §6.2）──
    # 计划分解用 medium tier（§2.2 / §2.6）；单任务快速路径不调模型。
    # 默认 true：串行分解是默认路径之一（§四「默认路径必须是串行或单任务」）。
    plan_model_enabled: bool = True
    # 并行扇出（高成本高收益）：**默认关闭**（§四【不要做】），需显式开启且过
    # should_plan_parallel 预算守卫（剩余预算 / fanout_count / 任务特征）。
    plan_parallel_enabled: bool = False
    # 单会话允许的并行扇出次数上限（§2.4 C2：防连续扇出烧钱）。
    plan_max_fanout_calls: int = 2
    # 扇出前要求的本会话剩余预算（¥，§2.4 C1）。
    plan_fanout_min_budget: float = 0.20

    # ── 语音 / TTS（本地 Qwen3-TTS 子进程，无外部 HTTP 接口调用）──
    # 目录固定为 backend/ttsclone（不配置路径）；模型不自动下载，
    # 先用 scripts/download_tts_model.py 预下载到 backend/ttsclone/models
    voice_tts_enabled: bool = False
    voice_tts_speaker: str = "Vivian"
    # 交互默认 0.6B（本机 CPU 单句 ~5s，1.7B ~38s 太慢，仅离线批量质检用大模型）
    voice_tts_model_size: str = "0.6B"
    voice_tts_timeout: int = 600

    # 每次 LLM 调用的输出 token 上限（对齐 opencode transform.ts:maxOutputTokens 的"默认给足"设计）。
    # 默认 16_384 ≈ 模型原生上限的常用值；长任务配合系统提示"长内容写文件"规则避免截断。
    # [token 优化 v9] 16_384 → 8_192：普通问答用不到那么大输出，压低超长输出兜底成本。
    # [输出上限 2026-10-03] 8_192 → 16_384：实测写俄罗斯方块时整份代码塞进单次
    # tool_write_file 参数（24,528 字符）正好撞 8K 被腰斩。注意这只在**模型目录也放开**
    # 时才有效 —— resolve_max_output_tokens 取 min(模型声明, 本值) 且绝不放大，
    # 故 builtin_catalog.py 里 deepseek 的 limits.max_output_tokens 同步提到 16_384。
    # [思考预算 2026-10-09] 16_384 → 32_000：deepseek-v4-flash 默认开思考，且思考 token
    # 计入 max_tokens（实测 ct==reasoning==16_384、finish=length、content=0），16K 上限被
    # 思考吃光 → 正文与工具调用皆空、任务失败。对齐 opencode `OUTPUT_TOKEN_MAX=32000`，
    # 给「思考 + 正文」留总预算。⚠ 输出预留必须 ≥ 输出上限，
    # 并同步 builtin_catalog deepseek 条目的 limits.max_output_tokens（resolve 取 min）。
    llm_max_tokens: int = 32_000

    # ── 已删配置（2026-10-18 审计）──
    # max_context_tokens / context_reserve_tokens：曾用于 opencode overflow.ts 的
    # usable = max − reserve 预算封顶（原 app/context/budget.py），随目录重构删除后
    # 全库已无读者（含 os.environ 读取），本轮从 Settings 移除。
    # 当前上下文压缩阈值是 cognition/compressor.py 的模块常量（非配置项）：
    # COMPRESS_THRESHOLD=80_000、KEEP_RECENT_TOKENS=20_000、TOOL_OUTPUT_MAX_CHARS=2000。
    # tool_execute_enabled（2026-10-19 删）：曾与 ALLOW_SOURCE_WRITES 共同作为
    # tool_execute 的双开关（helper tool_execute_available()），但新图里读取该
    # helper 的 tools/fstools 包已随目录重构删除，helper 与字段双双成为死代码，移除。
    # [token 优化 P8] cl100k_base 对 DeepSeek tokenizer 系统性低估（实测 +13.2%：
    # round8 估算 20,851 vs 实际 23,599）。用于 token_counter 估算校正，避免
    # 截断/压缩判断"以为没超、实际已超"（实测 round9 实际 25,779 超 usable 23,808）
    token_estimate_correction: float = 1.13
    # 压缩时尾部保留的最近轮次（对齐 opencode tail_turns，默认 2）
    context_tail_turns: int = 2
    # 自动压缩时保留的最近消息条数（0 = 按 context_tail_turns × 2 折算）。
    # ⚠ 此前只在 .env.example 里文档化、未声明为字段：extra="allow" 下 .env 里没这行时
    # settings.compaction_tail_messages 直接 AttributeError，service._auto_tail_limit()
    # 在 compact 路径必炸。现补为正式字段（默认 0 = 用轮次折算）。
    compaction_tail_messages: int = 0
    # [C5] 单条工具输出 token 上限（对齐 opencode 按 token 截断语义）：中文场景
    # 32KB 字符 ≈ 48K token 单条过大，bound_tool_output 在字符/行限之外再按 token
    # 封顶，超限写盘 + 续读提示。0 = 不启用 token 封顶。
    # ⚠ 现状：仅被 cognition/tool_output.py:bound_tool_output 读取，而新工具循环
    # graph/nodes/base.py:run_tool_loop **未调用** bound_tool_output（工具输出原样入 convo），
    # 故本项当前为「库函数可配、生产未接线」。
    tool_output_max_tokens: int = 8_000
    # ── [F8/F9] 图片上传解析给模型（多模态附件管线）──
    # 回显/缓存缩略图边长（px）
    image_thumbnail_px: int = 256
    image_use_ocr: bool = False              # 图片 OCR 提取文本（截图/文档图，需 OCR 库）
    # OCR 引擎：tesseract 可执行文件与语言包目录（留空则依赖系统 PATH / 默认 tessdata）。
    # 典型场景：tesseract 装在受保护目录（C:\Program Files\Tesseract-OCR）且无权写入
    # 其 tessdata 时，把 chi_sim.traineddata 放到自建目录并用 tessdata_dir 指向它。
    tesseract_cmd: str = ""
    tessdata_dir: str = ""
    ocr_languages: str = "chi_sim+eng"       # OCR 语言；chi_sim 缺失时自动回退 eng
    image_vlm_caption: bool = True           # 图片描述桥：视觉 LLM 生成 caption 注入文本
    image_caption_model: str = "openai/gpt-4o-mini"  # caption VLM（litellm 前缀；内网改 ollama_chat/llava）
    image_caption_api_base: str = ""
    image_caption_api_key: str = ""
    image_caption_prompt: str = ""           # 空用默认「用中文简洁描述图片内容与关键细节」
    image_caption_timeout: int = 15          # caption 调用超时（秒），失败快速降级

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
    # 权限审批等待超时（秒），默认 180；超时视为拒绝
    # 60s 对人工审批偏紧（弹窗出现后要留给用户阅读与决策时间），配合前端倒计时自动拒绝
    permission_approval_timeout: int = 180
    # 是否放行主工作区内受保护源码路径（app/、plugins/、skills/、config/、main.py 等）的写/执行。
    # 默认 false：这些路径硬保护，添加再多工作区也不能改；
    # 设为 true 后（如开发本系统自身时）允许 Agent 修改 backend 源码。
    # .git、.env、*.db、permissions.json 始终保护，不受此开关影响。
    allow_source_writes: bool = False

    # [思考模式 2026-10-09] DeepSeek V4 思考开关：enabled / disabled / adaptive。
    # 默认 disabled —— 实测 agentic 工具循环里思考会吃光 max_tokens 预算
    # （reasoning == ct == 上限，finish=length，零工具 / 零正文），且思考轮要求把
    # reasoning_content 回传（依赖 litellm 补丁）。关闭后 finish=stop、正常产出。
    # adaptive = 不显式下发，交服务端默认（DeepSeek V4 默认 enabled）；
    # enabled 时经 extra_body 下发 thinking={"type":"enabled"}（litellm 顶层 kwarg 会被丢弃）。
    deepseek_thinking: str = "disabled"
    # [思考模式 2026-10-11] Ollama 思考开关（全局兜底默认）：enabled / disabled / adaptive。
    # 现已优先使用每模型 `thinking` 字段（前端「模型管理 → 思考模式」）；仅当模型条目未设
    # `thinking` 时才回落到此处。默认 disabled —— 实测（qwen3.5:4b）litellm+ollama 下
    # think=True 会把**最终回答**全部塞进 reasoning_content、content 恒空（工具调用也进
    # reasoning，工具循环报废），与 DeepSeek 同理。关闭后 finish=stop、content 正常产出
    # （含标准 tool_calls）。adaptive = 不显式下发，交推理服务默认；enabled 时顶层传 think=True。
    ollama_thinking: str = "disabled"

    # Ollama 上下文窗口（num_ctx，传给本地推理服务的 options）。Ollama 默认 2048，
    # 超出窗口的 prompt 会被服务器截断 -> 历史/长上下文静默丢失（模型答"记不清/无历史"）。
    # 按本机内存/显存调大，例：3090 可 32768；OOM 则下调。
    ollama_num_ctx: int = 8192

    # ── 共享记忆持久化 ──
    # 非空时 MemoryManager 将未过期记忆落盘到该文件，重启不丢失
    memory_persist_path: str = "data/agent_memory.json"

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

    # ── 全链路日志（app/chainlog/，落库 data/chain_logs.db 或统一 DB）──
    # 总开关：关闭后所有埋点为 no-op（仅一次 settings 读取，无额外开销）
    chain_log_enabled: bool = True
    # 内存缓冲队列长度（条）。写入是异步批量落库，队列满时丢弃最旧的非关键节点
    # （http.request / agent.start 等关键节点走同步直写，见 app/chainlog/store.py）
    chain_log_queue_size: int = 5000
    # 批量落盘间隔（秒）与单批最大条数
    chain_log_flush_interval: float = 1.0
    chain_log_flush_batch: int = 200
    # 保留天数（TTL）：>0 时按 ts 定期/启动时清理过期链路日志；0 = 永久保留
    chain_log_retention_days: int = 7
    # 行数上限（防爆）：>0 时每批落盘后按 ts 裁剪到该行数
    chain_log_max_rows: int = 200000
    # 单条 data（JSON 负载）的最大字符数，超出截断并标记
    chain_log_max_data_chars: int = 4000
    # 记录链路上的工具调用明细（tool_start/tool_end）。关闭可显著降低写入量
    chain_log_tool_calls: bool = True


settings = Settings()
