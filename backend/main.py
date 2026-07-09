import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import documents, chat, skills, plugins, vectors, generated, permission as perm_api
from app.monitor import RequestLogMiddleware, get_stats
from app.runtime import ensure_runtime_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await asyncio.to_thread(ensure_runtime_state, app)
    try:
        yield
    except asyncio.CancelledError:
        pass


app = FastAPI(
    title="Knowledge Base System",
    description="RAG-powered knowledge base with AI agent (LangChain + LangGraph + ChromaDB)",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestLogMiddleware)  # type: ignore

app.include_router(documents.router, prefix="/api/documents", tags=["Documents"])
app.include_router(chat.router, prefix="/api/chat", tags=["Chat"])
app.include_router(skills.router, prefix="/api/skills", tags=["Skills"])
app.include_router(plugins.router, prefix="/api/plugins", tags=["Plugins"])
app.include_router(vectors.router, prefix="/api/vectors", tags=["Vectors"])
app.include_router(generated.router, prefix="/api/generated", tags=["Generated"])
app.include_router(perm_api.router, prefix="/api", tags=["Permission"])


@app.get("/")
async def root():
    return {
        "service": "Knowledge Base System",
        "version": "0.1.0",
        "docs": "/docs",
    }


@app.get("/health")
async def health():
    if not hasattr(app.state, "vector_store"):
        return {"status": "initializing"}
    return {"status": "ok", "vector_store_size": app.state.vector_store.count}


@app.get("/api/monitor/stats")
async def monitor_stats():
    return get_stats()
