# -*- coding: utf-8 -*-
"""语音快速自检（开发用）：CLI 合成 + 常驻转写 worker 计时，无鉴权、无 HTTP。

用法（后端 venv）：
    backend\\.venv\\Scripts\\python.exe -X utf8 backend\\scripts\\voice_quickcheck.py [synth|worker|all]

    synth   直接合成一句（0.6B，~15s）
    worker  常驻转写 worker：请求2 次，看第 2 次稳态耗时（预期 <2s/块）
    all     两项都跑
"""
import json
import os
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/
PY = sys.executable
CLONE = os.path.join(BASE, "ttsclone", "clone.py")
WAV = next(
    (
        os.path.join(BASE, "ttsclone", "outputs", f)
        for f in sorted(os.listdir(os.path.join(BASE, "ttsclone", "outputs")))
        if f.startswith("custom_") and f.endswith(".wav")
    ),
    None,
)
TXT = "你好，我是知识库语音助手。"


def cli_synth():
    t0 = time.time()
    r = subprocess.run(
        [PY, CLONE, "custom", TXT, "--speaker", "Vivian", "--lang", "Chinese", "--small"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    tail = (r.stdout or r.stderr).strip().splitlines()[-1] if (r.stdout or r.stderr).strip() else "?"
    print(f"[cli-synth 0.6B] {time.time()-t0:.1f}s | {tail[:90]}")


def worker_bench():
    if not WAV:
        print("[worker] 未找到 ttsclone/outputs 测试音频，先跑一次 synth 生成。", flush=True)
        cli_synth()
        return
    proc = subprocess.Popen(
        [PY, CLONE, "transcribe-serve"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        cwd=os.path.join(BASE, "ttsclone"),
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    try:
        for i in (1, 2):
            t0 = time.time()
            proc.stdin.write((json.dumps({"path": WAV}) + "\n").encode()); proc.stdin.flush()
            line = proc.stdout.readline().decode("utf-8", "replace").strip()
            print(f"[worker req{i}] {time.time()-t0:.2f}s | {line[:90]}")
    finally:
        proc.stdin.write(b"shutdown\n"); proc.stdin.flush()
        proc.wait(timeout=10)
        print(f"[worker] rc={proc.returncode}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("all", "synth"):
        cli_synth()
    if mode in ("all", "worker"):
        worker_bench()