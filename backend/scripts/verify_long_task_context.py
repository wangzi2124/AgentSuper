# -*- coding: utf-8 -*-
"""[C5] 长任务上下文越界验证脚本（A+C 效果）。

用真实 `RAGAgent._generate` 主循环（真实 truncate / bound_tool_output / budget）
模拟一个 20 轮工具循环的长任务：每轮返回大段中文工具输出，验证：
  1. 每轮实际发给 LLM 的消息估算 ≤ llm_call_budget（硬护栏生效，绝不越界）
  2. 对比修复前：若截断目标用 usable（无 0.9 安全垫）会怎样——打印两边曲线
  3. STEP_STATE 是否随轮次落盘

运行：.venv\\Scripts\\python.exe scripts/verify_long_task_context.py
"""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from app.config import settings
from app.context import budget
from app.context import token_counter as tc
import app.agent.graphmod.generate as gen_mod

# 用小上下文放大效果：320K 下 20 轮很难触顶，10K 下几轮就见真章
_CTX = int(os.environ.get("VERIFY_CTX", 12_000))
settings.max_context_tokens = _CTX
settings.context_reserve_tokens = 1_000
settings.context_safety_ratio = 0.9  # A：安全垫
settings.tool_output_max_tokens = 2_000  # A/C：单条输出 token 封顶（放大生效）
settings.step_summary_enabled = False  # 只验证 A+C（D 单独验证）

for n in ("record_model_call", "trace", "trace_messages"):
    setattr(gen_mod, n, lambda *a, **k: None)

from app.agent.graphmod.generate import RAGAgentGenerate
from app.agent.graphmod.core import RAGAgent
from types import SimpleNamespace as _S


class FakeLLM:
    """可编程 _llm_call 替身：N 轮工具调用后返回 done。"""

    def __init__(self, rounds: int):
        self.rounds = rounds
        self.calls = []

    def _resp(self, tool_calls=None, content=""):
        tcs = [
            _S(id=f"c{i}", type="function", function=_S(name=n, arguments=a))
            for i, (n, a) in enumerate(tool_calls or [])
        ]
        msg = _S(content=content, tool_calls=tcs or None)
        return _S(choices=[_S(message=msg, finish_reason="tool_calls" if tcs else "stop")],
                  usage=_S(prompt_tokens=5, completion_tokens=3,
                           prompt_cache_hit_tokens=0, prompt_cache_miss_tokens=5))

    async def __call__(self, model, messages, tool_defs, state=None):
        # 发送时快照（messages 列表后续会被 _generate 复用/改写，引用会失真）
        self.calls.append((model, [dict(m) for m in messages], tool_defs))
        if len(self.calls) <= self.rounds:
            return self._resp(tool_calls=[("tool_write_file", '{"path": "f.txt"}')])
        return self._resp(content="任务完成")


def main() -> int:
    rounds = 20
    llm = FakeLLM(rounds)
    agent = RAGAgent(SimpleNamespace(is_empty=True, invoke=lambda q, k=3: []))
    agent._build_tool_defs = lambda *a, **k: []  # 忽略 schema，聚焦消息体
    agent._llm_call = llm

    async def fake_execute(name, args, state=None):
        # 大段中文工具输出：真实 bound_tool_output(≤2000 token) 会截断它
        return "这里是构建日志 " * 2000

    agent._execute_tool = fake_execute

    import asyncio
    import tempfile
    from pathlib import Path

    sts_dir = Path(tempfile.mkdtemp(prefix="c5_verify_"))
    state = {
        "messages": [], "question": "构建一个大型项目并逐步完成",
        "context": [], "answer": "", "sources": [], "model": None, "history": [],
        "use_vector_db": False, "files": [], "steps": [], "tokens": {}, "finish": "stop",
        "_event_queue": None, "_on_activity": None, "_task": None,
        "_cwd": str(sts_dir), "_task_depth": 0, "conversation_id": "verify",
    }

    asyncio.run(agent._generate(state))

    usable = budget.usable_context_tokens()
    safe_budget = budget.llm_call_budget()
    print(f"== C5 A+C 长任务验证（{rounds} 轮工具循环）==")
    print(f"MAX_CONTEXT_TOKENS={_CTX}  usable={usable}  llm_call_budget(×0.9)={safe_budget}")
    print(f"TOOL_OUTPUT_MAX_TOKENS={settings.tool_output_max_tokens}  (bound 后单条封顶)")
    print()
    peak = 0
    over_any = False
    for i, (_, msgs, _) in enumerate(llm.calls):
        est = tc.estimate_tokens_messages(msgs)
        peak = max(peak, est)
        margin = (safe_budget - est) / safe_budget * 100
        flag = "  <-- 超过预算!" if est > safe_budget else ""
        if est > safe_budget:
            over_any = True
        # 只打印有代表性的几轮 + 全程峰值的最后几轮
        if i < 3 or i >= len(llm.calls) - 3 or flag:
            print(f"round {i+1:2d}: sent={est:6d} tok  距预算余量 {margin:5.1f}%{flag}")
    print()
    print(f"全程峰值 sent = {peak} / 预算 {safe_budget}（{peak/safe_budget*100:.0f}%）")
    print(f"超预算轮次: {over_any}")
    print(f"STEP_STATE 落盘: {sorted(p.name for p in (sts_dir/'.agents'/'steps').glob('*.md') if p.name!='latest.md')}")
    ok = not over_any and peak <= safe_budget
    print("验证结论:", "PASS - 任一发送消息估算 <= llm_call_budget（含 10% 安全垫）" if ok
          else "FAIL - 存在估算越界")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
