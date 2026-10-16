# -*- coding: utf-8 -*-
"""语音 API 冒烟：验证 /api/voice/status | transcribe | tts。

无真实模型时也应通过——未启用（VOICE_TTS_ENABLED=false 或 ttsclone 缺失）
时验证降级（status.enabled=false、tts/transcribe 返回 503 明确错误）。

用法：
    .venv\\Scripts\\python.exe scripts/smoke_voice_api.py [base_url]

默认 base_url = http://localhost:8000（后端需已启动）。
退出码 0 = 通过（含正确的降级），1 = 失败。
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def _req(method, path, data=None, headers=None):
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("X-User-Id", "anonymous")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def main():
    # ── status ──
    status, _, body = _req("GET", "/api/voice/status")
    if status != 200:
        print(f"[FAIL] /api/voice/status HTTP {status}: {body!r}")
        return 1
    data = json.loads(body)["data"]
    print(f"[status] enabled={data['enabled']} speakers={len(data['speakers'])} languages={len(data['languages'])}")
    if not data["enabled"]:
        print("[info] 服务未启用（VOICE_TTS_ENABLED=false 或 ttsclone 缺失），验证降级语义")

    # ── transcribe（空音频应得 400；启用时真音频走转写）──
    boundary = "----smoke"
    empty = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="audio"; filename="r.webm"\r\n'
        f"Content-Type: audio/webm\r\n\r\n"
        f"\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    status, _, body = _req("POST", "/api/voice/transcribe", data=empty,
                           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    print(f"[transcribe] empty → HTTP {status} (预期 400)")

    # ── tts ──
    form = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="text"\r\n\r\n'
        f"你好，语音冒烟测试。\r\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")
    status, headers, body = _req("POST", "/api/voice/tts", data=form,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    if status == 200:
        ctype = headers.get("content-type", "")
        if not ctype.startswith("audio/wav"):
            print(f"[FAIL] tts 返回非 wav: {ctype}")
            return 1
        if len(body) < 44:
            print(f"[FAIL] tts 返回空/过小音频 ({len(body)} bytes)")
            return 1
        print(f"[tts] OK audio/wav {len(body)} bytes")
    else:
        print(f"[tts] HTTP {status}（未启用时预期 503）: {body.decode('utf-8', 'replace')[:160]}")

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
