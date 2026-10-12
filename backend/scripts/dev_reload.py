"""[P1 #4] 开发用「外层」热重载（替代不可靠的 `uvicorn --reload`）。

背景：本机（E: 盘）uvicorn 内置 WatchFiles 观察不到变更，即使
WATCHFILES_FORCE_POLLING=1 也无效，且会残留死进程的幽灵 8000 监听
（新实例 bind 失败 / [Errno 10048]）。

思路：不要依赖 uvicorn 自己 spawn 的子进程。用一个外层 watchfiles 进程
监听整个 `backend/` 目录树，文件变更时**整体重跑** uvicorn 命令——进程树
被整体替换，不残留 multiprocessing spawn 的幽灵监听。

用法（在 backend/ 下）：
    .venv\\Scripts\\python.exe scripts\\dev_reload.py [--host 0.0.0.0] [--port 8000]

dry-run（只打印将执行的命令，便于核对/排错）：
    .venv\\Scripts\\python.exe scripts\\dev_reload.py --dry-run

等价的手写命令：
    .venv\\Scripts\\python.exe -m watchfiles --filter python \
        "cd /d <backend> && <venv python> -m uvicorn main:app --host 0.0.0.0 --port 8000" <backend>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def build_target(host: str, port: int) -> str:
    """构造 watchfiles 在文件变更时重跑的 shell 命令（先切到 backend/ 再起 uvicorn）。"""
    py = f'"{sys.executable}"'
    cmd = f"{py} -m uvicorn main:app --host {host} --port {port}"
    if sys.platform.startswith("win"):
        return f'cd /d "{BACKEND}" && {cmd}'
    return f'cd "{BACKEND}" && {cmd}'


def main() -> int:
    ap = argparse.ArgumentParser(description="watchfiles outer reloader for uvicorn")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的命令后退出")
    args = ap.parse_args()

    target = build_target(args.host, args.port)
    if args.dry_run:
        print(target)
        return 0

    try:
        from watchfiles import PythonFilter, run_process
    except ImportError:
        print(
            "watchfiles 未安装。请先 `pip install watchfiles`，"
            "或直接运行上方 dry-run 打印的命令。",
            file=sys.stderr,
        )
        return 2

    run_process(str(BACKEND), target=target, target_type="command", watch_filter=PythonFilter())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
