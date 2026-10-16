"""A/B 实验（M5 · 规模化）。

两部分：
1. 变体路由 `route_by_variant`：基于 user_id 的稳定哈希把流量分到某个变体。
2. 指标记录 `ABExperiment`：记录每个变体的**暴露量**与**成功/失败/耗时**，
   并提供 `summary()` 供评估与灰度决策（对齐 §12 M5「A/B 评估」）。

线程安全；纯内存实现（生产可替换为埋点上报）。
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field


def route_by_variant(user_id: str, variants: list[str], salt: str = "ab") -> str:
    """按 user_id 稳定路由到某个变体（同一用户始终命中同一变体）。"""
    if not variants:
        raise ValueError("variants cannot be empty")
    h = hashlib.blake2b((user_id + salt).encode("utf-8"), digest_size=4).hexdigest()
    idx = int(h, 16) % len(variants)
    return variants[idx]


@dataclass
class VariantStats:
    """单个变体的累计指标。"""

    exposures: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: float = 0.0

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return (self.successes / total) if total else 0.0

    @property
    def avg_latency_ms(self) -> float:
        total = self.successes + self.failures
        return (self.total_latency_ms / total) if total else 0.0

    def to_dict(self) -> dict[str, float]:
        return {
            "exposures": self.exposures,
            "successes": self.successes,
            "failures": self.failures,
            "success_rate": round(self.success_rate, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
        }


@dataclass
class ABExperiment:
    """A/B 实验：变体路由 + 指标记录。"""

    name: str
    variants: list[str]
    salt: str = "ab"
    _stats: dict[str, VariantStats] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.variants:
            raise ValueError("variants cannot be empty")
        for v in self.variants:
            self._stats.setdefault(v, VariantStats())

    def route(self, user_id: str) -> str:
        """为用户选择变体并记一次暴露。"""
        variant = route_by_variant(user_id, self.variants, salt=self.salt)
        return self.record_exposure(variant)

    def record_exposure(self, variant: str) -> str:
        with self._lock:
            self._stats.setdefault(variant, VariantStats()).exposures += 1
        return variant

    def record_result(
        self, variant: str, *, success: bool, latency_ms: float = 0.0
    ) -> None:
        """记录一次结果（成功/失败与耗时）。"""
        with self._lock:
            st = self._stats.setdefault(variant, VariantStats())
            if success:
                st.successes += 1
            else:
                st.failures += 1
            st.total_latency_ms += max(0.0, float(latency_ms))

    def stats(self, variant: str) -> VariantStats:
        with self._lock:
            return self._stats.setdefault(variant, VariantStats())

    def summary(self) -> dict[str, dict[str, float]]:
        with self._lock:
            return {v: st.to_dict() for v, st in self._stats.items()}

    def best_variant(self, *, metric: str = "success_rate") -> str | None:
        """按指标选出最优变体（样本为 0 的变体不参与）。"""
        with self._lock:
            candidates = [
                (v, getattr(st, metric))
                for v, st in self._stats.items()
                if (st.successes + st.failures) > 0
            ]
        if not candidates:
            return None
        return max(candidates, key=lambda kv: kv[1])[0]

    def reset(self) -> None:
        with self._lock:
            self._stats = {v: VariantStats() for v in self.variants}
