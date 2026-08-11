from pathlib import Path
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
    llm_max_tokens: int = 16_384

    # Token 成本控制
    # 每次 LLM 调用允许的最大上下文（system + history + 当前问题）
    # 对齐 opencode overflow.ts：usable = max_context_tokens - context_reserve_tokens
    # [token 优化 v5] 48K → 32K：配合 v4 压缩（信息不丢），单次调用天花板 -33%
    max_context_tokens: int = 32_000
    # 输出预留：留给模型回答的 token（≈ min(20_000, maxOutputTokens)，默认 8_192）
    context_reserve_tokens: int = 8_192
    # 压缩触发阈值（token）；0 表示自动 = 0.8 × usable，长工具循环在截断兜底之前先压缩
    compaction_threshold_tokens: int = 0
    # 压缩时尾部保留的最近轮次（对齐 opencode tail_turns，默认 2）
    context_tail_turns: int = 2
    # 尾部保留的 token 预算（对齐 opencode preserve_recent_tokens，默认 8_000）
    context_preserve_recent_tokens: int = 8_000
    # 回溯式工具输出清理：最近 N 轮之内累计工具输出超过该值时，清理更旧输出（默认 40_000）
    tool_output_protect_tokens: int = 24_000
    # 清理收益低于该值时不做（避免微小收益的频繁改写）
    tool_output_prune_minimum_tokens: int = 12_000
    # 摘要中间件缓存大小（按历史分块缓存，避免每请求全量重算）
    summarization_cache_size: int = 200

    # 可选：管理端鉴权 token。设置后插件 toggle/call、权限审批等敏感接口需携带
    # Authorization: Bearer <token>；不设置时这些接口仅允许本机来源（deps.require_admin）。
    admin_token: Optional[str] = None

    # ── 并发控制 ──
    # 全局同时执行的 Agent 任务上限（coordinator global_semaphore + chat.py 旧信号量共用）。
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

    # ── Agent 执行循环护栏（对齐 opencode prompt.ts / processor.ts / max-steps.ts）──
    # 主步骤上限（对齐 opencode agent.steps，默认 40）：到达上限的最后一轮注入收尾提示，
    # 并禁用工具，强制"已完成/未完成/下一步"式总结。生效上限 = min(MAX_STEPS, MAX_TOOL_ROUNDS)。
    max_steps: int = 24
    # 硬兜底：单次请求内最多 LLM 调用轮数（每轮都是一次完整 LLM 调用）。
    # 当 MAX_STEPS >= MAX_TOOL_ROUNDS 时，MAX_STEPS 生效上限即等于该值。
    max_tool_rounds: int = 16
    # Doom-loop 检测：同一组工具调用指纹连续重复 N 轮后，注入策略变更提示（≥2）
    doom_loop_threshold: int = 3
    # Doom-loop 升级：首次提示之后，再次连续触发 N 次相同指纹即强制收尾（注入 MAX_STEPS_PROMPT + 禁用工具），
    # 对齐 opencode processor.ts 的 permission.ask(doom_loop) → deny 后 stop 语义
    doom_loop_max_strikes: int = 2
    # 工具密集型子 Agent（如 code）的更长等待超时（秒），避免长任务被误判超时
    sub_agent_timeout_extended: float = 300.0
    # 使用 extended 超时的子 Agent 列表（逗号分隔）
    extended_timeout_agents: str = "code"

    # ── 共享记忆持久化 ──
    # 非空时 MemoryManager 将未过期记忆落盘到该文件，重启不丢失
    memory_persist_path: str = "data/agent_memory.json"

    # ── 用户身份签名（可选，默认关闭）──
    # 设置 AUTH_TOKEN_SECRET 后启用：X-User-Id 必须携带对应的签名 token
    # （前端先注册随机 user_id + device_secret，再换取 token），
    # 防止仅伪造 X-User-Id 头越权读取他人会话。默认（本地部署）不校验。
    auth_secret: Optional[str] = None
    # token 有效期（秒），默认 30 天
    auth_token_ttl: int = 2592000
    # 已注册用户（user_id → 设备密钥哈希）的持久化文件
    auth_users_path: str = "data/auth_users.json"


settings = Settings()
