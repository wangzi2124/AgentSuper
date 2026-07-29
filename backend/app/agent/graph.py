"""RAG Agent — retrieve + rerank → CrewAI multi-agent generation.

Architecture:
  1. Pre-processing: retrieve → rerank (unchanged)
  2. Generation: CrewAI multi-agent team (coordinator/researcher/analyst/writer)
     handles all tool execution and answer generation internally.
"""

import asyncio
import logging
import time as tmod
from pathlib import Path
from typing import Optional

from app.config import settings
from app.rag.retriever import Retriever
from app.rag.reranker import Reranker
from app.skills.loader import SkillLoader
from app.plugins.loader import PluginLoader

logger = logging.getLogger(__name__)

WORKSPACE = Path(__file__).resolve().parents[2]


class RAGAgent:
    """RAG Agent — retrieve → rerank → generate via CrewAI multi-agent team."""

    def __init__(
        self,
        retriever: Retriever,
        skill_loader: Optional[SkillLoader] = None,
        plugin_loader: Optional[PluginLoader] = None,
        reranker: Optional[Reranker] = None,
    ):
        self.retriever = retriever
        self.reranker = reranker
        self.skill_loader = skill_loader
        self.plugin_loader = plugin_loader
        self.model = settings.llm_model
        self.api_key = settings.llm_api_key
        self.api_base = settings.llm_api_base

    def _push_event(self, state: dict, event: dict):
        state.setdefault("steps", []).append(event)
        eq = state.get("_event_queue")
        if eq:
            eq.put_nowait(event)

    async def _retrieve(self, state: dict) -> dict:
        start = tmod.time()
        self._push_event(state, {"type": "step_start", "step_id": "retrieve", "name": "检索中", "status": "running"})

        if self.retriever.is_empty or not state.get("use_vector_db", True):
            reason = "知识库为空" if self.retriever.is_empty else "已禁用向量检索"
            dur = (tmod.time() - start) * 1000
            self._push_event(state, {"type": "step_end", "step_id": "retrieve", "name": "检索中", "status": "completed", "detail": reason, "duration_ms": round(dur, 1)})
            return {"context": [], "sources": []}

        import functools
        results = await asyncio.to_thread(
            functools.partial(self.retriever.invoke, state["question"], k=5)
        )
        context = []
        sources = []
        for doc, score in results:
            meta = doc["metadata"]
            context.append({"content": doc["text"], "metadata": meta})
            sources.append({
                "document_id": meta.get("document_id", ""),
                "content": doc["text"][:300],
                "score": score,
            })
        dur = (tmod.time() - start) * 1000
        self._push_event(state, {"type": "step_end", "step_id": "retrieve", "name": "检索中", "status": "completed", "detail": f"找到 {len(results)} 个相关片段", "duration_ms": round(dur, 1)})
        return {"context": context, "sources": sources}

    async def _rerank(self, state: dict) -> dict:
        if not self.reranker or not state.get("context"):
            if state.get("context"):
                self._push_event(state, {"type": "step_end", "step_id": "rerank", "name": "相关性重排序", "status": "completed", "detail": "重排序已禁用"})
            return {}
        start = tmod.time()
        self._push_event(state, {"type": "step_start", "step_id": "rerank", "name": "相关性重排序", "status": "running"})
        import functools
        reranked = await asyncio.to_thread(
            functools.partial(self.reranker.rerank, query=state["question"], documents=state["context"], top_k=3)
        )
        context = [{"content": doc["content"], "metadata": doc["metadata"]} for doc, _ in reranked]
        dur = (tmod.time() - start) * 1000
        self._push_event(state, {"type": "step_end", "step_id": "rerank", "name": "相关性重排序", "status": "completed", "detail": f"筛选出 {len(reranked)} 个最相关片段", "duration_ms": round(dur, 1)})
        return {"context": context}

    async def _generate(self, state: dict) -> dict:
        """Generate answer using CrewAI multi-agent team.

        Always routes through a CrewAI crew (researcher + writer by default,
        or full orchestrated team for complex tasks).
        """
        from app.crew.crew_manager import CrewManager

        self._push_event(state, {
            "type": "step_start", "step_id": "generate",
            "name": "多Agent协作", "status": "running",
        })

        model = state.get("model") or self.model
        if "/" not in model:
            if self.api_base and "deepseek" in self.api_base:
                model = f"deepseek/{model}"
            elif self.api_base and "openai" in self.api_base:
                model = f"openai/{model}"

        # Build context text from retrieved documents
        context_text = ""
        if state.get("context"):
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(state["context"])
            ]
            context_text = "\n\n".join(context_parts)

        # Include recent conversation history
        topic = state["question"]
        if state.get("history"):
            history_text = "\n".join(
                f"{m['role']}: {str(m.get('content', ''))[:500]}"
                for m in state["history"][-5:]
            )
            topic = f"Conversation history:\n{history_text}\n\nCurrent question: {state['question']}"

        input_data = {
            "topic": topic,
            "query": state["question"],
            "context": context_text,
            "model": model,
        }

        if state.get("files"):
            input_data["files"] = state["files"]

        try:
            crew_mgr = CrewManager(
                plugin_loader=self.plugin_loader,
                skill_loader=self.skill_loader,
            )
            result = await crew_mgr.run(
                task_type="general",
                input_data=input_data,
                event_queue=state.get("_event_queue"),
            )
        except Exception as e:
            logger.exception("CrewAI generation failed")
            self._push_event(state, {
                "type": "step_end", "step_id": "generate",
                "name": "多Agent协作", "status": "failed",
                "detail": str(e)[:200],
            })
            return {"answer": f"Error: {e}"}

        self._push_event(state, {
            "type": "step_end", "step_id": "generate",
            "name": "多Agent协作", "status": "completed",
            "detail": "多Agent团队协作完成",
        })

        return {"answer": result["result"]}

    async def invoke(
        self,
        question: str,
        model: Optional[str] = None,
        history: Optional[list[dict]] = None,
        use_vector_db: bool = True,
        files: Optional[list[dict]] = None,
        event_queue: Optional[asyncio.Queue] = None,
    ) -> dict:
        """Execute full RAG pipeline: retrieve → rerank → generate via CrewAI."""
        state: dict = {
            "question": question,
            "context": [],
            "answer": "",
            "sources": [],
            "model": model,
            "history": history or [],
            "use_vector_db": use_vector_db,
            "files": files or [],
            "steps": [],
            "_event_queue": event_queue,
        }

        retrieve_result = await self._retrieve(state)
        state["context"] = retrieve_result.get("context", [])
        state["sources"] = retrieve_result.get("sources", [])

        if self.reranker:
            rerank_result = await self._rerank(state)
            if rerank_result.get("context"):
                state["context"] = rerank_result["context"]

        if not state.get("context") and state.get("use_vector_db", True) and not self.retriever.is_empty:
            self._push_event(state, {
                "type": "step_end", "step_id": "generate",
                "name": "多Agent协作", "status": "completed",
                "detail": "知识库未检索到相关内容，跳过生成",
            })
            return {
                "answer": "知识库中未找到与问题相关的信息，无法生成回答。",
                "sources": state["sources"],
                "steps": state["steps"],
            }

        gen_result = await self._generate(state)
        state["answer"] = gen_result.get("answer", "")

        return {
            "answer": state["answer"],
            "sources": state["sources"],
            "steps": state["steps"],
        }
