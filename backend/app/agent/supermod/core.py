"""拆分模块 `core`（含 SupervisorAgentCore）。

原文件 docstring: Supervisor Agent — 多 Agent 系统的编排者。

核心职责:
  1. 接收用户的 "chat" 请求
  2. 用 LLM 判断用户意图，决定路由到哪个子 Agent
  3. 支持任务分解：将复杂问题拆成多个子任务并行执行
  4. 通过 AgentBus 转发请求并等待回复
  5. 将子 Agent 的回答包装后返回给用户

修复的 Bug:
  - thread_id 覆盖: 子请求使用独立 thread_id，防止覆盖调用方的 Future"""
# ── 复制自原模块的顶层 import ──
import asyncio

import logging



import uuid

import time as tmod

from typing import AsyncIterator, Optional


from app.agent.base import BaseAgent, AgentMessage





from .base import SupervisorAgentBase
logger = logging.getLogger(__name__)

_USAGE_KEYS = ("input", "output", "reasoning", "cache_read", "cache_write")


def _merge_usage(acc: dict, other: dict) -> None:
    """逐键求和 usage（五键口径），就地更新 acc（对齐 assistant 消息 data.tokens）。"""
    for k in _USAGE_KEYS:
        acc[k] = int(acc.get(k, 0)) + int(other.get(k, 0) or 0)


def _sub_usage(curr: dict, before: dict) -> dict:
    """usage 差值（curr - before），五键口径，负数归零。

    [_route_to 需要发「本轮增量」而非累计值]：plan→build 会先路由 plan 再路由 build，
    两次 yield 的都是累计快照时，合并方把 P 与 P+B 相加 → 计划用量被计两次。
    """
    return {k: max(0, int(curr.get(k, 0)) - int(before.get(k, 0))) for k in _USAGE_KEYS}


# ── 类分块（verbatim，继承链切片）──
class SupervisorAgentCore(SupervisorAgentBase):

    # ═══════════════════════════════════════════════════════════════
    #  Handle Message （入口）
    # ═══════════════════════════════════════════════════════════════

    async def handle_message(self, msg: AgentMessage) -> AsyncIterator[AgentMessage]:
        if msg.type != "request":
            return

        action = msg.action
        payload = msg.payload

        if action == "chat":
            question = payload.get("question", "")

            # 语音消息降级兜底：前端转写失败时 message 仍为 "[语音]"，
            # 若 payload 携带 voice.text（后端转写/历史回放），用其替换占位符
            if question == "[语音]":
                voice_text = (payload.get("voice") or {}).get("text", "")
                if voice_text:
                    question = voice_text
                    payload["question"] = question

            # [token 优化 v9] 本次请求的 LLM 用量汇总（分解 + 子 Agent + 汇总），
            # 随 response payload 落库，与单 Agent executor 口径对齐。
            # 注：bus 事件循环对每个 agent 串行处理消息，无并发写冲突。
            # [token 统计] 五键口径 + cost 一并汇总。
            self._usage = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
            self._cost = 0.0

            # [A2] Supervisor 自身心跳：整个处理（LLM 分解 / 等待子 Agent / 汇总）
            # 期间持续 touch，让上层（endpoint send_and_wait 的 grace 续期）能看见
            # supervisor 仍存活，避免其被误判超时；收尾时取消。
            beat = self._start_heartbeat()
            from app import chainlog
            chainlog.info(
                "routing", "supervisor", "supervisor.received",
                message=f"supervisor 受理请求（{len(payload.get('history') or [])} 条历史）",
                data={
                    "action": action, "thread_id": msg.thread_id,
                    "use_vector_db": payload.get("use_vector_db"),
                    "agent_mode": payload.get("agent_mode"),
                    "directory": payload.get("directory"),
                    "has_files": bool(payload.get("files")),
                },
            )
            try:
                # ── 尝试任务分解 ──
                subtasks = await self._decompose(question)

                # 安全护栏：只路由到白名单 Agent，防止 LLM 返回 "supervisor" 造成自我递归超时
                subtasks = [st for st in subtasks if st.get("agent") in self.ROUTABLE_AGENTS]
                if not subtasks:
                    subtasks = [{"agent": "build", "question": question}]

                # [C4] supervisor 不做 fan-out：`_decompose` 恒返回单个子任务
                # （顶层命令只有 build/plan，探索由 build 的 tool_task 委派 explore，
                #   对齐 opencode —— 并行由子 Agent 的委派链承担，不在 supervisor 层）。
                # 原 `len(subtasks) > 1 → _execute_parallel` 分支连同整套并行实现
                # （_execute_parallel / _synthesize / _llm_decompose / _validate_subtasks /
                #   sub_task_fresh_history）已删除 —— 它恒不可达，只是让代码看起来支持并行。
                target_agent = subtasks[0]["agent"]

                chainlog.info(
                    "routing", "supervisor", "routing.decision",
                    message=f"路由决策：{target_agent}",
                    data={
                        "subtasks": [
                            {"agent": st.get("agent"), "question": st.get("question")}
                            for st in subtasks
                        ],
                        "routable": sorted(self.ROUTABLE_AGENTS),
                        "parallel": False,
                    },
                )

                logger.info(
                    "Supervisor routing to '%s' (thread=%s)",
                    target_agent, msg.thread_id,
                )
                if target_agent == "plan" and self._should_handoff_to_build(question):
                    # [opencode 对齐] plan→build 顺序交接：plan 产出计划文件后
                    # supervisor 直接把计划交给 build 执行（对应 build-switch 语义）
                    async for reply in self._route_plan_then_build(payload, msg.thread_id):
                        yield reply
                else:
                    async for reply in self._route_to(target_agent, payload, msg.thread_id):
                        yield reply
            finally:
                if beat is not None:
                    beat.cancel()

        else:
            yield AgentMessage(
                source=self._id, target=msg.source,
                type="error", action=action,
                payload={"error": f"Supervisor doesn't support action: {action}"},
                thread_id=msg.thread_id,
            )

    # ═══════════════════════════════════════════════════════════════
    #  Bug 修复: 使用独立 thread_id 发送子请求
  # ═══════════════════════════════════════════════════════════════

    async def _route_to(
        self,
        target_agent: str,
        payload: dict,
        original_thread_id: str,
    ) -> AsyncIterator[AgentMessage]:
        """转发到目标 Agent 并等待回复。

        🔧 Bug 修复: 子请求使用独立 thread_id，避免覆盖调用方的 Future。
        """
        sub_thread_id = f"{original_thread_id}:sub:{uuid.uuid4().hex[:8]}"
        timeout = self._timeout_for(target_agent)
        from app import chainlog
        chainlog.info(
            "agent", "supervisor", "route.start", agent_id=target_agent,
            message=f"路由到 {target_agent}（超时 {timeout:.0f}s）",
            data={
                "target": target_agent, "timeout": timeout,
                "sub_thread_id": sub_thread_id,
                "question": str(payload.get("question", ""))[:500],
            },
        )
        route_started = tmod.time()
        # [C2] 本轮派发前的累计用量/成本快照：yield 的是**本轮增量**。
        # 原实现发累计值 —— 单路由时两者相等，但 plan→build 会把 P 与 P+B 相加，
        # 计划那段用量被重复计入落库的 tokens/cost。
        usage_before = dict(getattr(self, "_usage", None) or {})
        cost_before = float(getattr(self, "_cost", 0.0) or 0.0)

        try:
            reply = await self._bus.send_and_wait(
                AgentMessage(
                    source=self._id,
                    target=target_agent,
                    type="request",
                    action="chat",
                    payload=payload,
                    thread_id=sub_thread_id,  # 🔧 独立 thread_id
                ),
                timeout=timeout,
            )

            if reply.type == "response":
                # [token 优化 v9] 子 Agent 用量计入本次请求汇总
                if getattr(self, "_usage", None) is not None:
                    _merge_usage(self._usage, reply.payload.get("tokens") or {})
                    self._cost = getattr(self, "_cost", 0.0) + float(reply.payload.get("cost", 0) or 0)
                chainlog.info(
                    "agent", "supervisor", "route.done", agent_id=target_agent,
                    message=f"{target_agent} 返回结果",
                    data={
                        "target": target_agent,
                        "answer_chars": len(str(reply.payload.get("answer", ""))),
                        "tokens": reply.payload.get("tokens") or {},
                        "cost": reply.payload.get("cost", 0) or 0,
                        "plan_path": reply.payload.get("plan_path") or "",
                    },
                    duration_ms=round((tmod.time() - route_started) * 1000, 1),
                )
                yield AgentMessage(
                    source=self._id,
                    target="user",  # 由 bus.send 路由回 original 的调用者
                    type="response",
                    action="chat",
                    payload={
                        **reply.payload,
                        "routed_to": target_agent,
                        "tokens": _sub_usage(getattr(self, "_usage", None) or {}, usage_before),
                        "cost": round(float(getattr(self, "_cost", 0.0) or 0.0) - cost_before, 6),
                    },
                    thread_id=original_thread_id,  # 🔧 使用原始 thread_id 回复
                )
            elif reply.type == "error":
                # bus 现在以 AgentMessage(type="error") 交付子 Agent 错误，
                # 透传 error payload（含 completed_steps 等上下文）。
                chainlog.error(
                    "agent", "supervisor", "route.failed", agent_id=target_agent,
                    message=f"{target_agent} 返回错误: {reply.payload.get('error', '')}",
                    data={
                        "target": target_agent,
                        "error": reply.payload.get("error"),
                        "error_type": reply.payload.get("error_type"),
                        "completed_steps": reply.payload.get("completed_steps", []),
                    },
                    duration_ms=round((tmod.time() - route_started) * 1000, 1),
                )
                yield AgentMessage(
                    source=self._id, target="user",
                    type="error", action="chat",
                    payload={
                        "error": reply.payload.get("error", "Sub-agent failed"),
                        "error_type": reply.payload.get("error_type", "sub_agent_error"),
                        "completed_steps": reply.payload.get("completed_steps", []),
                    },
                    thread_id=original_thread_id,
                )
            else:
                yield AgentMessage(
                    source=self._id, target="user",
                    type="error", action="chat",
                    payload={"error": f"Sub-agent returned unexpected type: {reply.type}"},
                    thread_id=original_thread_id,
                )

        except asyncio.TimeoutError:
            # [C9] 报实际等待上限（含一次宽限追加），不再只报基础值让用户以为只等了 timeout。
            grace_grant = min(timeout, max(10.0, timeout / 2))
            max_total = timeout + grace_grant
            logger.warning("Sub-agent '%s' timed out after ~%.0fs (thread=%s)", target_agent, max_total, original_thread_id)
            completed = self._bus.agent_progress(target_agent)
            suggestion = (
                f"如果任务仍在执行（如代码脚手架/构建），可提高 SUB_AGENT_TIMEOUT "
                f"或 SUB_AGENT_TIMEOUT_EXTENDED，或改用普通对话模式重试。"
            )
            chainlog.error(
                "agent", "supervisor", "route.timeout", agent_id=target_agent,
                message=f"{target_agent} 路由超时",
                data={"target": target_agent, "timeout": timeout,
                      "max_total_wait": round(max_total, 1),
                      "completed_steps": completed, "suggestion": suggestion},
                duration_ms=round((tmod.time() - route_started) * 1000, 1),
            )
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={
                    "error": (
                        f"Agent '{target_agent}' did not respond in time "
                        f"(waited up to {max_total:.0f}s = {timeout:.0f}s + 宽限 {grace_grant:.0f}s). "
                        f"已完成步骤: {(' → '.join(completed) if completed else '无可获取的处理进度')}. "
                        f"{suggestion}"
                    ),
                    "error_type": "sub_agent_timeout",
                    "timeout": timeout,
                    "max_total_wait": round(max_total, 1),
                    "completed_steps": completed,
                    "suggestion": suggestion,
                },
                thread_id=original_thread_id,
            )
        except Exception as e:
            logger.exception("Supervisor error routing to %s", target_agent)
            chainlog.error(
                "agent", "supervisor", "route.error", agent_id=target_agent,
                message=f"路由到 {target_agent} 失败: {e}",
                data={"target": target_agent, "error": str(e),
                      "error_type": type(e).__name__},
                duration_ms=round((tmod.time() - route_started) * 1000, 1),
            )
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={
                    "error": str(e),
                    "error_type": "sub_agent_error",
                },
                thread_id=original_thread_id,
            )

    async def _collect_route(
        self,
        target_agent: str,
        payload: dict,
        original_thread_id: str,
    ) -> Optional[AgentMessage]:
        """路由到目标 Agent 并返回唯一回复消息（供顺序交接复用）。

        `_route_to` 恰好 yield 一条消息（response/error），收集后返回；
        无产出时返回 None。
        """
        reply = None
        async for m in self._route_to(target_agent, payload, original_thread_id):
            reply = m
        return reply

    async def _route_plan_then_build(
        self,
        payload: dict,
        original_thread_id: str,
    ) -> AsyncIterator[AgentMessage]:
        """[opencode 对齐] plan→build 顺序交接（build-switch 语义的无审批版）。

        当请求同时命中「规划」与「执行」意图时：
          1. 先路由 plan：产出实施计划并落盘 <data>/plans/<conv>/plan.md；
          2. 把计划文本（+ 计划文件路径）合成为 build 的 user 消息，再路由 build 执行；
          3. 合并为单条回复（计划 + 执行结果），保持与顶层 send_and_wait 的单回复契约。
        """
        plan_reply = await self._collect_route("plan", payload, original_thread_id)
        if plan_reply is None:
            yield AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={"error": "plan agent did not reply", "error_type": "sub_agent_error"},
                thread_id=original_thread_id,
            )
            return
        if plan_reply.type == "error":
            yield plan_reply
            return

        plan_answer = str(plan_reply.payload.get("answer", ""))
        plan_path = plan_reply.payload.get("plan_path", "")
        build_question = (
            "用户请求先规划再执行。plan agent 已产出以下实施计划"
            + (f"（计划文件: {plan_path}）" if plan_path else "")
            + "，请按计划逐步执行并报告完成情况：\n\n"
            + plan_answer
        )
        build_payload = dict(payload)
        build_payload["question"] = build_question
        build_reply = await self._collect_route("build", build_payload, original_thread_id)
        if build_reply is None:
            build_reply = AgentMessage(
                source=self._id, target="user",
                type="error", action="chat",
                payload={"error": "build agent did not reply", "error_type": "sub_agent_error"},
                thread_id=original_thread_id,
            )
        yield self._merge_plan_build_reply(plan_reply, build_reply)

    @staticmethod
    def _merge_plan_build_reply(
        plan_reply: AgentMessage,
        build_reply: AgentMessage,
    ) -> AgentMessage:
        """把 plan 与 build 两条子 Agent 回复合并为单条回复。

        build 成功时返回 response，answer = 计划 + ## 执行结果；
        build 失败时仍保留计划文本，返回 error 型消息且 answer 携带完整计划，便于用户跟进。
        plan_path/sources/steps/tokens 一并合并。
        """
        plan_payload = plan_reply.payload or {}
        build_payload = build_reply.payload or {}
        plan_answer = str(plan_payload.get("answer", ""))
        plan_path = plan_payload.get("plan_path", "")
        sources = list(plan_payload.get("sources") or []) + list(build_payload.get("sources") or [])
        steps = list(plan_payload.get("steps") or []) + list(build_payload.get("steps") or [])
        tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
        _merge_usage(tokens, plan_payload.get("tokens") or {})
        _merge_usage(tokens, build_payload.get("tokens") or {})
        cost = round(float(plan_payload.get("cost", 0) or 0) + float(build_payload.get("cost", 0) or 0), 6)

        if build_reply.type == "error":
            answer = (
                f"## 实施计划\n\n{plan_answer}\n\n"
                f"## 执行结果（出错）\n{build_payload.get('error', 'build 执行出错')}"
            )
            return AgentMessage(
                source="supervisor", target=build_reply.target or "user",
                type="error", action="chat",
                payload={
                    "error": build_payload.get("error", "build 执行出错"),
                    "error_type": build_payload.get("error_type", "sub_agent_error"),
                    "completed_steps": build_payload.get("completed_steps", []),
                    "answer": answer,
                    "routed_to": "plan→build",
                    "plan_path": plan_path,
                    "tokens": tokens,
                    "cost": cost,
                    "sources": sources,
                    "steps": steps,
                },
                thread_id=build_reply.thread_id or plan_reply.thread_id,
            )

        answer = f"## 实施计划\n\n{plan_answer}\n\n## 执行结果\n{build_payload.get('answer', '')}"
        return AgentMessage(
            source="supervisor", target="user",
            type="response", action="chat",
            payload={
                "answer": answer,
                "sources": sources,
                "steps": steps,
                "tokens": tokens,
                "cost": cost,
                "plan_path": plan_path,
                "routed_to": f"plan→{build_payload.get('routed_to', 'build')}",
            },
            thread_id=build_reply.thread_id or plan_reply.thread_id,
        )

__all__ = ['SupervisorAgentCore']
