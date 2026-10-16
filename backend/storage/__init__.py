"""中性存储层（对齐 agent.md §9 目标布局：共享基础设施迁出 `app/`）。

统一持久化基础设施：SQLite/MySQL/PostgreSQL 后端门面、schema 单一来源、全局路径、
文件存储、Alembic 迁移、生成文件工具。不依赖 `app.*`（仅依赖中性 `config`）。
`app/storage/*` 保留为再导出 shim，以维持既有 import 不变。
"""
