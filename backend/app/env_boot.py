"""在导入应用模块之前，把 `backend/.env` 注入 `os.environ`。

[P1 #7 环境注入时机] 之前 `runtime._load_env_to_os()` 在 FastAPI lifespan 里跑，
意味着任何在 **import 期** 读 `os.environ.get()` 的模块（第三方库探测、插件、
早期初始化）拿到的是空值，插件过早加载会读到 stale env。

现在 `main.py` 在 import 任何 `app.*` 业务模块之前先调用本模块的 `load_env_to_os()`，
保证 import 期即可见。语义与原实现一致：仅当 key 不在 `os.environ` 时写入
（真实环境变量优先于 .env，与 python-dotenv `override=False` 相同）。
`settings`（pydantic-settings）依旧直接读 .env，不受影响。

刻意保持零重依赖（只用标准库），以便在 import 图最顶端安全导入。
"""

from __future__ import annotations

import os
from pathlib import Path


def load_env_to_os() -> None:
    """把 `backend/.env` 的键值注入 `os.environ`（不覆盖已存在的真实环境变量）。"""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = val
