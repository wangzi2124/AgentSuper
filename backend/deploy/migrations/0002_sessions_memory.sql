# 会话 / 任务 / 长期记忆状态表（对齐 agent.md §8 状态与数据层）
# 与 0001_*.sql 同库；sqlite 环境下由应用内建表（backend/app/session）。
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL DEFAULT 'anonymous',
    title       TEXT NOT NULL DEFAULT '',
    directory   TEXT,
    kind        TEXT NOT NULL DEFAULT 'chat',
    parent_id   TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived    INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS session_messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    role       TEXT NOT NULL,
    content    JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS session_messages_session_idx ON session_messages (session_id, seq);

CREATE TABLE IF NOT EXISTS session_inputs (
    id           TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    prompt       JSONB NOT NULL,
    delivery     TEXT NOT NULL DEFAULT 'queue',
    promoted_seq INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS session_inputs_session_idx ON session_inputs (session_id, created_at);

CREATE TABLE IF NOT EXISTS agent_memory (
    namespace  TEXT NOT NULL DEFAULT '',
    key        TEXT NOT NULL,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);
