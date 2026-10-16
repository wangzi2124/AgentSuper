"""L6 性能冒烟（agent.md §10.6）。

用假模型端到端跑一次 `/chat`，断言单请求 P50 远低于真实模型场景的门限
（这里是确定性上界，防止沙箱回归导致整体卡死）。
"""

from __future__ import annotations

import time


def test_chat_latency_smoke(client):
    # 预热（首次请求含路由懒加载）
    client.post("/chat", json={"message": "warmup"})

    samples = []
    for i in range(5):
        start = time.perf_counter()
        resp = client.post("/chat", json={"message": f"q{i}"})
        samples.append(time.perf_counter() - start)
        assert resp.status_code == 200

    samples.sort()
    p50 = samples[len(samples) // 2]
    # 假模型无 LLM 调用，单次应远小于 1s；给宽松上界避免 CI 抖动。
    assert p50 < 1.0, f"P50={p50:.3f}s 超出上界"


def test_health_latency_smoke(client):
    start = time.perf_counter()
    assert client.get("/health").status_code == 200
    assert (time.perf_counter() - start) < 0.5
