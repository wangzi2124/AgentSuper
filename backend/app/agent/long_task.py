"""[C5 · 方案 E/F] 长任务小步快走多请求接力执行器。

核心思想：把「一次请求内单上下文大循环」拆成「多步骤、每步独立 fresh-context 请求」。
每步请求只装 `system + 计划 + 已完成 STEP_STATE + 该步指令`，旧步骤的完整对话不携带
（状态以文件/摘要为准，而非对话），从根源上杜绝上下文随任务推进线性膨胀。

- 方案 E：每步一个独立请求（`agent.invoke`），步间经 `context/step_state.py`
  落盘衔接，支持断点续跑（同会话工作目录即可恢复）。
- 方案 F：等价地，每步可改为委派 code 子 Agent（fresh context）——本执行器
  直接用主 RAGAgent 的 `invoke`（本身就是 fresh-context + 全量文件工具），
  与 tool_task 委派语义一致；接入处可自由切换。

非长任务（计划 ≤ 1 步）自动回落普通单请求路径，零额外开销。
"""

import json
import logging

logger = logging.getLogger(__name__)

PLAN_PROMPT = """你是任务规划器。把下面的长任务拆解为最多 {max_steps} 个**小步骤**，
每一步必须在一次独立请求内能完成（读写文件 / 执行命令 / 构建验证等），步骤间彼此独立可衔接。
只输出一个 JSON 字符串数组，例如：
["步骤1：创建项目骨架与依赖文件", "步骤2：实现核心模块", ...]
严格只输出数组，不要 markdown 代码块标记，不要任何其它文字。

任务: {question}"""

STEP_PROMPT = """任务目标: {question}

执行计划（共 {total} 步），当前第 {current}/{total} 步。
已完成进度:
{progress}

{handoff}
当前步骤: {step}

请完成该步骤（可用 tool_write_file/append_file/edit_file/execute 等文件工具）。
务必先读取「已完成步骤产出的文件」（如适用）再继续，避免重复创建。
在结尾用一两句简要汇报改动，并单独用一行列出你创建/修改的文件路径：
产出文件: path1, path2, ...
"""

# 协调器落盘 STEP_STATE 的 seq 偏移：避免与 _generate 每轮落盘的 1..N 冲突
_COORD_SEQ_OFFSET = 1000

_FILES_LINE_RE = __import__("re").compile(r"产出文件[:：]\s*(.+)")


def _collect_files_from_steps(directory: str) -> list[str]:
    """收集会话目录下已落盘 STEP_STATE 中的产出文件（in-loop 写入 + 协调器写入）。"""
    from app.context.step_state import step_state_dir
    d = step_state_dir(directory)
    if d is None or not d.exists():
        return []
    import re
    out: list[str] = []
    for p in sorted(d.glob("*.md")):
        if p.name == "latest.md":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        m = re.search(r"## Relevant Files\n(.*?)(?=\n## |\Z)", text, re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            line = line.strip().lstrip("- ").strip()
            if line and line != "(none)":
                out.append(line)
    return list(dict.fromkeys(out))


def _parse_files_from_answer(answer: str) -> list[str]:
    """从步骤回答的 `产出文件: a, b` 行提取文件路径。"""
    m = _FILES_LINE_RE.search(answer or "")
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(",") if p.strip()]


def _parse_plan(text: str, max_steps: int) -> list[str]:
    """解析规划器的 JSON 输出为步骤字符串列表。"""
    if not text:
        return []
    cleaned = text.strip()
    cleaned = cleaned.replace("```json", "").replace("```", "").strip()
    try:
        data = json.loads(cleaned)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    return [str(s).strip() for s in data if isinstance(s, str) and s.strip()][:max_steps]


class LongTaskCoordinator:
    """长任务小步快走协调器：计划 → 逐步骤 fresh-context 执行 → STEP_STATE 衔接。"""

    def __init__(self, agent, max_steps: int = 6):
        self.agent = agent  # RAGAgent（fresh context per step）
        self.max_steps = max(2, max_steps)

    async def _plan(self, question: str) -> list[str]:
        """用 agent 的 LLM 做一次纯规划调用（无工具，小上下文）。"""
        prompt = PLAN_PROMPT.format(max_steps=self.max_steps, question=question)
        try:
            resp = await self.agent._llm_call(
                self.agent.model, [{"role": "user", "content": prompt}], [], state=None
            )
            content = resp.choices[0].message.content or ""
        except Exception as e:  # noqa: BLE001
            logger.warning("LongTask planning failed: %s", e)
            return []
        return _parse_plan(content, self.max_steps)

    async def run(self, question: str, directory: str = "", conversation_id: str = "") -> dict:
        """执行长任务。计划 >1 步 → 接力；否则回落普通单请求。"""
        plan = await self._plan(question)
        if len(plan) <= 1:
            # 非长任务：正常单请求执行（不额外开销）
            logger.info("LongTask: plan=%d steps, fall back to single request", len(plan))
            return await self.agent.invoke(
                question, use_vector_db=False, directory=directory,
                conversation_id=conversation_id,
            )

        logger.info("LongTask: %d steps, small-step execution", len(plan))
        results: list[str] = []
        progress: list[str] = []
        produced_files: list[str] = []
        token_total = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
        for i, step in enumerate(plan, 1):
            # 步骤交接：把已完成步骤产出的文件注入当前步（fresh context 下唯一的信息桥）
            collected = _collect_files_from_steps(directory) or produced_files
            handoff = (
                "已完成步骤产出的文件（可直接读取/引用，避免重复创建）:\n"
                + "\n".join(f"- {f}" for f in collected)
                if collected
                else "(尚无产出文件)"
            )
            step_prompt = STEP_PROMPT.format(
                question=question, total=len(plan), current=i, step=step,
                progress="\n".join(f"- {s}" for s in progress) or "- (刚开始，尚未完成任何步骤)",
                handoff=handoff,
            )
            result = await self.agent.invoke(
                step_prompt, use_vector_db=False, directory=directory,
                conversation_id=conversation_id,
            )
            answer = (result.get("answer") or "").strip()
            results.append(answer)
            progress.append(f"step{i}: {step} -> {answer[:120]}")
            # 收集本步产出文件：优先答案 `产出文件:` 行，其次落盘 STEP_STATE（in-loop）
            produced_files = list(dict.fromkeys(
                produced_files + _parse_files_from_answer(answer) + _collect_files_from_steps(directory)
            ))
            # STEP_STATE 落盘：断点续跑/审计用（seq 偏移避免与 in-loop 轮次文件冲突）
            from app.context.step_state import write_step_state
            write_step_state(
                directory, _COORD_SEQ_OFFSET + i,
                {
                    "objective": question,
                    "completed": [f"step{i}: {step}"],
                    "active": ["所有步骤已完成"] if i == len(plan) else [f"等待执行步骤 {i + 1}"],
                    "blocked": [],
                    "next_move": [] if i == len(plan) else [f"执行步骤 {i + 1}: {plan[i]}"],
                    "files": produced_files,
                },
            )
            for k in ("input", "output", "reasoning", "cache_read", "cache_write"):
                token_total[k] += int((result.get("tokens") or {}).get(k, 0))

        body = "\n\n---\n\n".join(
            f"【步骤{i}】{r}" for i, r in enumerate(results, 1) if r
        )
        return {
            "answer": body or "(各步骤均未返回具体内容，请检查日志)",
            "sources": [],
            "steps": [],
            "tokens": token_total,
            "plan": plan,
        }
