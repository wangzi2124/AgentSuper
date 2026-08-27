"""多 Agent 子任务执行事件的收集与转发。

对齐 opencode 的 session events（step.* / tool.* / agent.switched）：
子 Agent 把细粒度执行事件（步骤开始/结束、工具调用、完成/出错）打成
agent_step 等事件，经 chat.py 的 AgentEventCollector 转发给
/multi-agent/stream 的 SSE 流，供前端按 agent 面板实时展示。

设计文档：docs/multi-agent-realtime-events-design.md
"""

import asyncio
from typing import Optional

# RAG graph._push_event 产出的、值得转发给前端的步骤类事件
STEP_EVENT_TYPES = {"step_start", "step_end", "tool_start", "tool_end"}

# [A5] TaggedEventQueue 的 显式放行 (allowlist) / 透传 / 丢弃 (drop) 清单：
#   - ALLOW（放行并转发给前端面板）：graph 的步骤类事件 → 统一改标为 agent_step
#   - PASSTHROUGH（原样透传）：permission_request（前端 multi-agent 复用共享审批面板，不能丢）
#   - DROP（明确丢弃）：tool_output / tool_heartbeat —— 高频瞬时噪音（工具输出/心跳），
#     会随每次工具调用涌入、前端只按 step upsert 展示最终态，中转无意义；且高强度
#     循环下直接透传会与 [A4] 背压冲突。保持显式丢弃，避免意外累积。
ALLOW_STEP_EVENTS = STEP_EVENT_TYPES
PASSTHROUGH_EVENTS = {"permission_request"}
DROP_HIGH_FREQ_EVENTS = {"tool_output", "tool_heartbeat"}

AGENT_LABELS = {
    "rag": "知识库检索",
    "web_search": "网络搜索",
    "code": "代码分析",
}
AGENT_AVATARS = {
    "rag": "📚",
    "web_search": "🌐",
    "code": "💻",
}


def agent_meta(agent_id: str) -> tuple[str, str]:
    """返回子 Agent 的中文显示名与头像。"""
    return AGENT_LABELS.get(agent_id, agent_id), AGENT_AVATARS.get(agent_id, "🤖")


def emit(queue, event: dict) -> None:
    """向事件收集器推送一条子 Agent 事件（同步、空安全，任何协程可调）。"""
    if queue is None:
        return
    try:
        queue.put_nowait(event)
    except Exception:
        pass


def unwrap_tagged(queue):
    """若队列已被 TaggedEventQueue 包装（带有委派方 agent_id），剥到底层收集器。

    tool_task 委派时，子 Agent 需要用自己的 agent_id 重新打标签（否则事件会
    被外层 TaggedEventQueue 误标为委派方，或 agent_step/agent_start 等被丢弃）。
    """
    if isinstance(queue, TaggedEventQueue):
        return getattr(queue, "_collector", None) or queue
    return queue


class AgentEventCollector:
    """请求级事件收集器：转发到 SSE 队列，同时记录副本供落库/兜底快照。"""

    # [A4] 收集器副本/转发队列的上限：防止长时间工具循环（tool_output 等高频
    # agent_step）在慢速 SSE 消费端堆积导致内存无界增长。超限只丢弃高频非
    # 终态事件（agent_step），start/done/error 始终保留以保证快照完整。
    _MAX_EVENTS = 500
    _DROP_NOTICE_INTERVAL = 100

    def __init__(self, queue: asyncio.Queue):
        self._queue = queue
        self.events: list[dict] = []
        self._dropped = 0

    def _push_queue(self, event: dict) -> None:
        try:
            self._queue.put_nowait(event)
        except Exception:
            pass

    def put_nowait(self, event: dict) -> None:
        et = event.get("type")
        # [A4] 超限后：agent_step 属高频且可重建的中间态，允许丢弃并记账；
        #       其余事件（agent_start/agent_done/agent_error）仍保留。
        if len(self.events) >= self._MAX_EVENTS and et == "agent_step":
            self._dropped += 1
            if self._dropped % self._DROP_NOTICE_INTERVAL == 1:
                marker = {
                    "type": "agent_step",
                    "agent_id": event.get("agent_id"),
                    "step": {
                        "step_id": f"overflow-{self._dropped}",
                        "name": f"…已丢弃 {self._dropped} 条高频步骤事件（事件过多已压缩）",
                        "status": "running",
                        "overflow": True,
                    },
                }
                self.events.append(marker)
                self._push_queue(marker)
            return
        self.events.append(event)
        self._push_queue(event)

    def agents_snapshot(self) -> list[dict]:
        """从收集到的事件重建每个子 Agent 的执行快照（对齐前端 AgentStreamData）。

        agent_start → 建条目；agent_step → 按 step_id upsert；
        agent_done → completed + content；agent_error → failed + error。
        """
        agents: dict[str, dict] = {}
        for ev in self.events:
            aid = ev.get("agent_id")
            if not aid:
                continue
            a = agents.get(aid)
            if a is None:
                a = {
                    "agent_id": aid,
                    "agent_name": ev.get("agent_name") or aid,
                    "agent_avatar": ev.get("agent_avatar"),
                    "status": "running",
                    "content": "",
                    "steps": [],
                }
                agents[aid] = a
            et = ev.get("type")
            if et == "agent_start":
                a["agent_name"] = ev.get("agent_name") or a["agent_name"]
                a["agent_avatar"] = ev.get("agent_avatar") or a["agent_avatar"]
                a["status"] = "running"
            elif et == "agent_step":
                step = ev.get("step") or {}
                idx = next(
                    (i for i, s in enumerate(a["steps"])
                     if s.get("step_id") == step.get("step_id")),
                    -1,
                )
                if idx >= 0:
                    a["steps"][idx] = step
                else:
                    a["steps"].append(step)
            elif et == "agent_done":
                a["status"] = "completed"
                if ev.get("content"):
                    a["content"] = ev["content"]
            elif et == "agent_error":
                a["status"] = "failed"
                a["error"] = ev.get("error", "Agent error")
        return list(agents.values())

    def fail_running(self, message: str) -> None:
        """把仍处于 running 的子 Agent 标记为失败（超时/全局错误时兜底）。"""
        for a in self.agents_snapshot():
            if a["status"] == "running":
                self.events.append({
                    "type": "agent_error",
                    "agent_id": a["agent_id"],
                    "error": message,
                })


class TaggedEventQueue:
    """RAG graph 事件队列适配器：把 graph 的原始步骤事件转为 agent_step。

    graph.py 的 _push_event 只依赖 put_nowait 语义（graph.py 的 _push_event/_push_stream_event），
    传入本对象即可实现事件透传，无需改动 graph 本身。

    [A5] 分派策略见顶部常量：
      - ALLOW_STEP_EVENTS (step_start/step_end/tool_start/tool_end) → 改标 agent_step 转发
      - PASSTHROUGH_EVENTS (permission_request) → 原样透传
      - DROP_HIGH_FREQ_EVENTS (tool_output/tool_heartbeat) → 明确丢弃（高频噪音）
      - 其余未知事件类型 → 丢弃（保守：不转发未显式允许的类型，防止意外泄漏/膨胀）
    """

    def __init__(self, collector: AgentEventCollector, agent_id: str):
        self._collector = collector
        self._agent_id = agent_id

    def put_nowait(self, event: dict) -> None:
        et = event.get("type")
        if et in ALLOW_STEP_EVENTS:
            self._collector.put_nowait({
                "type": "agent_step",
                "agent_id": self._agent_id,
                "step": event,
            })
        elif et in PASSTHROUGH_EVENTS:
            self._collector.put_nowait(event)
        # 其余（DROP_HIGH_FREQ_EVENTS 或未知类型）一律丢弃：tool_output / 
        # tool_heartbeat 系高频噪音，未知类型保守不转发，避免事件流意外膨胀。


def step_event(step_id: str, name: str, status: str,
               detail: str = "", duration_ms: Optional[float] = None,
               tool_name: str = "", tool_args: Optional[dict] = None) -> dict:
    """构造一个 AgentStep 事件（供 web_search/code 等简单 Agent 使用）。"""
    ev: dict = {
        "type": "step_start" if status == "running" else "step_end",
        "step_id": step_id, "name": name, "status": status,
    }
    if detail:
        ev["detail"] = detail
    if duration_ms is not None:
        ev["duration_ms"] = round(duration_ms, 1)
    if tool_name:
        ev["tool_name"] = tool_name
    if tool_args:
        ev["tool_args"] = tool_args
    return ev
