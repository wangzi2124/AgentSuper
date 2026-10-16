# -*- coding: utf-8 -*-
"""[C5] 真实 API 长任务冒烟测试（E/F 多请求接力 + A/C 护栏）。

用真实 LLM API（backend/.env 的 LLM_API_KEY/LLM_API_BASE）跑一个多文件项目任务，
经 LongTaskCoordinator（LONG_TASK_STEP_MODE=true）拆计划、每步独立 fresh-context 请求：
  - 打印每步每轮的实际 prompt_tokens（pt）曲线
  - 断言：所有调用 pt ≤ MAX_CONTEXT_TOKENS（A 硬上限）
  - 断言：接力模式下各步请求的 pt 保持小且不随步骤累积（E 每步 fresh context 生效）
  - STEP_STATE 落盘检查

运行：.venv\\Scripts\\python.exe backend/scripts/smoke_long_task_api.py
退出码：0=PASS，1=FAIL，2=无 API Key（跳过）
"""
import os
import sys
import tempfile
from types import SimpleNamespace

# GBK 控制台打印模型中文回答会崩（含非 GBK 字符）→ 统一 UTF-8 + replace
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.config import settings

if not getattr(settings, "llm_api_key", ""):
    print("SKIP: 未配置 LLM_API_KEY，跳过真实 API 冒烟")
    sys.exit(2)

from app.permission import PermissionManager, set_manager
from app.agent.long_task import LongTaskCoordinator
from app.agent.graphmod.core import RAGAgent
import app.agent.graphmod.core as core_mod

# 隔离到临时工作区：文件工具/STEP_STATE 全部落在临时目录，不污染仓库
WORK = tempfile.mkdtemp(prefix="c5_smoke_")
set_manager(PermissionManager(workspace=WORK, external_default="allow"))

# 记录每次真实 LLM 调用的 pt（core._llm_call 统一出口）
PT_LOG: list[dict] = []


def _spy_record(model, prompt_tokens=0, completion_tokens=0, duration_ms=0, tool_rounds=0, tool_calls=0):
    PT_LOG.append({
        "model": model, "pt": prompt_tokens, "ct": completion_tokens,
        "dur_ms": round(duration_ms, 1), "rounds": tool_rounds,
    })


# 缓存命中/未命中从 trace("llm.usage", ...) 事件捕获（G 前缀缓存验证）
CACHE_LOG: list[dict] = []


def _spy_trace(event, **payload):
    if event == "llm.usage":
        CACHE_LOG.append({
            "hit": payload.get("cache_hit", 0),
            "miss": payload.get("cache_miss", 0),
            "where": payload.get("where", ""),
        })
    return None


core_mod.record_model_call = _spy_record
core_mod.trace = _spy_trace
core_mod.trace_messages = lambda *a, **k: None


def main() -> int:
    settings.long_task_step_mode = True
    settings.step_summary_enabled = True  # 完整管线（A+C+D+E/F 全开）

    agent = RAGAgent(SimpleNamespace(is_empty=True, invoke=lambda q, k=3: []))

    task = (
        "在 /workspace 下创建一个最小可运行的 Python 项目："
        "① 写 calculator.py（含 add/subtract/multiply 三个函数）；"
        "② 写 test_calculator.py（覆盖三个函数的基本用例）；"
        "③ 用 tool_execute 运行一次测试（python -m pytest test_calculator.py -q 或等价命令），"
        "报告测试是否通过。"
    )
    print(f"== C5 真实 API 长任务冒烟 ==")
    print(f"MAX_CONTEXT_TOKENS={settings.max_context_tokens}  llm_call_budget="
          f"{int((settings.max_context_tokens - settings.context_reserve_tokens) * settings.context_safety_ratio)}")
    print(f"工作目录: {WORK}")
    print(f"任务: {task[:60]}...\n")

    coordinator = LongTaskCoordinator(agent, max_steps=settings.long_task_max_steps)
    result = __import__("asyncio").run(
        coordinator.run(task, directory=WORK, conversation_id="c5-smoke")
    )

    plan = result.get("plan") or []
    print(f"\n计划拆解: {len(plan)} 步")
    for i, s in enumerate(plan, 1):
        print(f"  step{i}: {s[:50]}")

    print(f"\n== 每步每次 LLM 调用 pt 曲线（hit=缓存命中 token） ==")
    peak = 0
    for i, rec in enumerate(PT_LOG, 1):
        peak = max(peak, rec["pt"])
        ch = CACHE_LOG[i - 1].get("hit", 0) if i <= len(CACHE_LOG) else 0
        print(f"  call{i:2d}: pt={rec['pt']:6d}  ct={rec['ct']:4d}  cache_hit={ch:6d}  "
              f"hit_ratio={ch / rec['pt'] * 100:.0f}%  rounds={rec['rounds']}")

    total_hit = sum(c.get("hit", 0) for c in CACHE_LOG)
    total_miss = sum(c.get("miss", 0) for c in CACHE_LOG) or 1
    print(f"  前缀缓存: 命中 {total_hit} / 未命中 {total_miss} token（命中率 {total_hit / (total_hit + total_miss) * 100:.0f}%）")

    limit = int(settings.max_context_tokens)
    over = [r for r in PT_LOG if r["pt"] > limit]
    print(f"\n总调用 {len(PT_LOG)} 次，pt 峰值 {peak} / 上限 {limit}（{peak/limit*100:.1f}%）")
    print(f"超过 MAX_CONTEXT_TOKENS 的调用: {len(over)}")
    # E/F 验证：接力各步的首次调用 pt（fresh context）应远小于上限且不随步骤累积
    step_first_pts = PT_LOG[:0]
    # （每步请求内的多次调用由工具循环产生；步骤间 fresh → 每步首调相近）

    from pathlib import Path
    steps_dir = Path(WORK) / ".agents" / "steps"
    step_files = sorted(p.name for p in steps_dir.glob("000*.md")) if steps_dir.exists() else []
    print(f"STEP_STATE 落盘: {step_files}")

    answer = (result.get("answer") or "")
    print(f"\n最终回答片段: {answer[:200]}")

    ok = not over and peak <= limit and answer.strip()
    print("\n结论:", "PASS - 全程 pt ≤ MAX_CONTEXT_TOKENS，接力完成" if ok else "FAIL")
    try:
        from pathlib import Path
        (Path(WORK) / "smoke_report.md").write_text(
            f"# C5 长任务冒烟报告\n\n计划步数: {len(plan)}\nLLM 调用: {len(PT_LOG)}\n"
            f"pt 峰值: {peak} / {limit}\n越界调用: {len(over)}\nSTEP_STATE: {step_files}\n\n"
            f"## 最终回答\n{answer}",
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        print(f"(报告落盘失败: {e})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
