"""租户配额（M5 · 规模化）。

⚠ 预留未接线：当前仅被单元测试引用，生产请求链路未启用租户配额。

按租户（tenant_id）限制并发/请求数/token 预算。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

class TenantQuota:
    def __init__(self, default_rpm=60, default_tpm=100000):
        self.default_rpm = default_rpm
        self.default_tpm = default_tpm
        self._req = defaultdict(lambda: deque())
        self._tok = defaultdict(int)
        self._last_reset = defaultdict(int)

    def allow(self, tenant_id: str, tokens=0, now=None) -> bool:
        now = now or time.time()
        dq = self._req[tenant_id]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= self.default_rpm:
            return False
        used = self._tok[tenant_id]
        if used + tokens > self.default_tpm:
            return False
        dq.append(now)
        self._tok[tenant_id] += tokens
        return True

    def reset(self, tenant_id: str):
        self._req.pop(tenant_id, None)
        self._tok.pop(tenant_id, None)
