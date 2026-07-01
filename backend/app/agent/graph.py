import json
import logging
from typing import Any, List, Optional, TypedDict, Annotated, Sequence

logger = logging.getLogger(__name__)

import litellm
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from app.rag.retriever import Retriever
from app.rag.reranker import Reranker
from app.skills.loader import SkillLoader
from app.plugins.loader import PluginLoader
from app.config import settings
from app.agent.tools import (
    ToolDef,
    create_skill_tools,
    create_plugin_tools,
    build_system_prompt_no_kb,
)


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], "messages"]
    question: str
    context: list[dict]
    answer: str
    sources: list[dict]
    model: Optional[str]
    history: list[dict]
    use_vector_db: bool
    files: list[dict]


class RAGAgent:
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

        self.system_prompt = build_system_prompt_no_kb(
            skill_loader or SkillLoader(""),
            plugin_loader or PluginLoader(""),
        )

        self.tools: List[ToolDef] = []
        if skill_loader:
            self.tools.extend(create_skill_tools(skill_loader))
        if plugin_loader:
            self.tools.extend(create_plugin_tools(plugin_loader))

        self.graph = self._build_graph()

    def _retrieve(self, state: AgentState) -> dict:
        if self.retriever.is_empty or not state.get("use_vector_db", True):
            return {"context": [], "sources": []}
        results = self.retriever.invoke(state["question"], k=5)
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
        return {"context": context, "sources": sources}

    def _rerank(self, state: AgentState) -> dict:
        if not self.reranker or not state.get("context"):
            return {}
        reranked = self.reranker.rerank(
            query=state["question"],
            documents=state["context"],
            top_k=3,
        )
        context = [{"content": doc["content"], "metadata": doc["metadata"]} for doc, _ in reranked]
        return {"context": context}

    def _system_prompt_with_kb(self) -> str:
        return (
            "You are a knowledgeable AI assistant with access to a knowledge base."
            "\n\nUse the retrieved context below to answer the user's question."
            "\n- Cite sources using [Source 1], [Source 2], etc."
            "\n- If a source has 'chapter_title' in its metadata, use that exact title when referring to the chapter."
            "\n- If a source has 'chapter_summary', it is a chapter overview — use it to describe the chapter's content."
            "\n- If you don't have enough information, say so."
            "\n\nYou also have access to skill tools (load_skill_*) and plugin tools. If the user asks to create/edit/manipulate documents (Word, PDF, PPT, Excel), generate visual designs, build web pages, or use other specialized capabilities, call the relevant skill tool to get instructions first."
        )

    def _build_tool_defs(self) -> Optional[List[dict]]:
        if not self.tools:
            return None
        return [t.to_openai_tool() for t in self.tools]

    async def _execute_tool(self, name: str, args: dict) -> str:
        for t in self.tools:
            if t.name == name:
                try:
                    import asyncio
                    result = await asyncio.to_thread(t.fn, **args)
                    return str(result)
                except Exception as e:
                    return f"Error executing {name}: {e}"
        return f"Tool '{name}' not found"

    async def _llm_call(self, model: str, messages: list, tool_defs: list) -> litellm.ModelResponse:
        import time as tmod
        from app.monitor import record_model_call

        start = tmod.time()
        try:
            response = await litellm.acompletion(
                model=model,
                messages=messages,
                tools=tool_defs,
                api_key=self.api_key,
                api_base=self.api_base,
                temperature=0.1,
                max_tokens=4096,
                timeout=500,
            )
        except Exception as e:
            dur = (tmod.time() - start) * 1000
            record_model_call(model, duration_ms=dur)
            raise

        dur = (tmod.time() - start) * 1000
        usage = getattr(response, "usage", None)
        pt = getattr(usage, "prompt_tokens", 0) if usage else 0
        ct = getattr(usage, "completion_tokens", 0) if usage else 0
        record_model_call(model, prompt_tokens=pt, completion_tokens=ct, duration_ms=dur)
        logger.info(
            "LLM call | model=%s pt=%d ct=%d dur=%.0fms",
            model, pt, ct, dur,
        )
        return response

    async def _generate(self, state: AgentState) -> dict:
        if state["context"]:
            context_parts = [
                f"[Source {i+1}]: {c['content']}"
                for i, c in enumerate(state["context"])
            ]
            context_text = "\n\n".join(context_parts)
            full_system_prompt = (
                self._system_prompt_with_kb()
                + "\n\n"
                + f"Retrieved Context:\n{context_text}"
            )
        else:
            full_system_prompt = self.system_prompt

        tool_defs = self._build_tool_defs()

        messages = [
            {"role": "system", "content": full_system_prompt},
        ]
        if state.get("history"):
            messages.extend(state["history"])

        # Build user content: text only or multimodal if files attached
        user_files = state.get("files", [])
        if user_files:
            user_content: list[dict] = [{"type": "text", "text": state["question"]}]
            for f in user_files:
                if f.get("mime_type", "").startswith("image/"):
                    user_content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{f['mime_type']};base64,{f['data']}"},
                    })
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": state["question"]})

        model = state.get("model") or self.model
        if "/" not in model:
            if self.api_base and "deepseek" in self.api_base:
                model = f"deepseek/{model}"
            elif self.api_base and "openai" in self.api_base:
                model = f"openai/{model}"

        import time as tmod
        _gen_start = tmod.time()

        response = await self._llm_call(model, messages, tool_defs)
        msg = response.choices[0].message

        max_tool_rounds = 10
        rounds = 0
        while msg.tool_calls and rounds < max_tool_rounds:
            rounds += 1
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ],
            })

            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError as e:
                    result = f"Error parsing arguments for '{tc.function.name}': {e}"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
                    continue
                result = await self._execute_tool(tc.function.name, args)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

            response = await self._llm_call(model, messages, tool_defs)
            msg = response.choices[0].message

        from app.monitor import record_model_call
        record_model_call(
            model, duration_ms=(tmod.time() - _gen_start) * 1000,
            tool_rounds=rounds,
        )

        answer = msg.content or ""
        return {
            "answer": answer,
            "messages": [AIMessage(content=answer)],
        }

    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("retrieve", self._retrieve)
        if self.reranker:
            builder.add_node("rerank", self._rerank)
        builder.add_node("generate", self._generate)
        builder.set_entry_point("retrieve")
        if self.reranker:
            builder.add_edge("retrieve", "rerank")
            builder.add_edge("rerank", "generate")
        else:
            builder.add_edge("retrieve", "generate")
        builder.add_edge("generate", END)
        return builder.compile()

    def refresh_tools(self):
        self.system_prompt = build_system_prompt_no_kb(
            self.skill_loader or SkillLoader(""),
            self.plugin_loader or PluginLoader(""),
        )
        self.tools = []
        if self.skill_loader:
            self.tools.extend(create_skill_tools(self.skill_loader))
        if self.plugin_loader:
            self.tools.extend(create_plugin_tools(self.plugin_loader))
        self.graph = self._build_graph()

    async def invoke(self, question: str, model: Optional[str] = None, history: Optional[list[dict]] = None, use_vector_db: bool = True, files: Optional[list[dict]] = None) -> dict:
        state = AgentState(
            messages=[HumanMessage(content=question)],
            question=question,
            context=[],
            answer="",
            sources=[],
            model=model,
            history=history or [],
            use_vector_db=use_vector_db,
            files=files or [],
        )
        return await self.graph.ainvoke(state)
