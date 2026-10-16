import asyncio
import uuid
from typing import Optional, TYPE_CHECKING

from app.storage.file_store import FileStore
from app.rag.bm25_index import BM25Index
from app.rag.document_processor import DocumentProcessor
from app.rag.embeddings import LocalEmbeddings
from app.rag.vector_store import VectorStore
from app.models.schemas import DocumentResponse
from datetime import datetime

if TYPE_CHECKING:
    from app.rag.chapter_store import ChapterStore


class ProcessingTask:
    """文档处理任务，跟踪任务状态和进度。"""

    def __init__(self, task_id: str, filename: str):
        self.task_id = task_id
        self.filename = filename
        self.status = "pending"
        self.progress = 0
        self.stage = ""
        self.result: Optional[dict] = None
        self.error: Optional[str] = None
        self.finished_at: Optional[datetime] = None

    def to_dict(self):
        """将任务状态转换为字典格式。"""
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "status": self.status,
            "progress": self.progress,
            "stage": self.stage,
            "result": self.result,
            "error": self.error,
        }


class TaskManager:
    """任务管理器，负责创建和管理文档处理任务。"""

    # 已完成/失败任务的保留时间窗口（秒），防止 _tasks 无界增长
    TASK_TTL_SECONDS = 3600

    def __init__(self):
        self._tasks: dict[str, ProcessingTask] = {}

    def create(self, filename: str) -> str:
        """创建新的处理任务，返回任务ID。"""
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = ProcessingTask(task_id, filename)
        self._prune_old_tasks()
        return task_id

    def _prune_old_tasks(self):
        """清理超过保留时间的已完成/失败任务，避免内存无界增长。"""
        now = datetime.now()
        stale = [
            tid for tid, t in self._tasks.items()
            if t.status in ("completed", "failed") and t.finished_at
            and (now - t.finished_at).total_seconds() > self.TASK_TTL_SECONDS
        ]
        for tid in stale:
            self._tasks.pop(tid, None)

    def get(self, task_id: str) -> Optional[ProcessingTask]:
        """根据任务ID获取任务对象。"""
        return self._tasks.get(task_id)

    async def process_document(
        self,
        task_id: str,
        content: bytes,
        filename: str,
        fs: FileStore,
        proc: DocumentProcessor,
        emb: LocalEmbeddings,
        vs: VectorStore,
        bm25: Optional[BM25Index] = None,
        chapter_store: Optional["ChapterStore"] = None,
    ):
        """异步处理文档：保存、分块、生成向量并存储。"""
        task = self._tasks.get(task_id)
        if not task:
            return

        loop = asyncio.get_event_loop()

        try:
            task.status = "processing"
            task.stage = "Saving file"
            task.progress = 5

            doc_id, file_path = fs.save(filename, content)
            fs.mark_index_state(doc_id, "processing")

            task.stage = "Reading and chunking document"
            task.progress = 15

            chunks, chapter_metas = await loop.run_in_executor(
                None, proc.process, file_path, doc_id, filename,
            )
            texts = [c[0] for c in chunks]
            metadatas = [c[1] for c in chunks]

            if not texts:
                raise ValueError("No text content could be extracted from the file")

            # Store chapter metadata
            if chapter_store and chapter_metas:
                for cm in chapter_metas:
                    chapter_store.add_chapter(
                        document_id=cm["document_id"],
                        filename=cm["filename"],
                        chapter_number=cm["chapter_number"],
                        chapter_title=cm["chapter_title"],
                        summary=cm["summary"],
                        parent_chunk_text=cm["parent_chunk_text"],
                    )

            task.stage = "Generating embeddings"
            task.progress = 25

            total = len(texts)
            all_embeddings = []

            def on_progress(done: int, _total: int):
                task.progress = 25 + int((done / total) * 65)
                task.stage = f"Embedding ({done}/{total} chunks)"

            all_embeddings = await loop.run_in_executor(
                None,
                emb.embed_documents, texts, 32, on_progress,
            )

            task.stage = "Storing to vector database"
            task.progress = 92

            await loop.run_in_executor(None, vs.add, texts, metadatas, all_embeddings)
            if bm25:
                await loop.run_in_executor(None, bm25.add, texts, metadatas)
            # 先写主库再补索引：此处发动机/BM25/章节已全部写入 → 标记 ready
            fs.mark_index_state(doc_id, "ready", chunk_count=len(chunks))

            meta = fs.get(doc_id)
            task.progress = 100
            task.stage = "Complete"
            task.status = "completed"
            task.result = DocumentResponse(
                id=doc_id,
                filename=filename,
                size=meta["size"],
                chunk_count=len(chunks),
                created_at=datetime.fromisoformat(meta["created_at"]),
            ).model_dump()

        except Exception as e:
            task.status = "failed"
            task.progress = 0
            task.stage = "Error"
            task.error = str(e)
            # [D2] 主库已保存文件与元数据（index_state!=ready），不删除：
            # 交由 kb_repair 自愈流程从文件重放建索引。此处尽力清除本次
            # 可能写入的部分向量/章节/BM25，避免当前进程内残留半成品。
            if chapter_store:
                try:
                    chapter_store.delete_by_document(doc_id)
                except Exception:  # noqa: BLE001
                    pass
            try:
                vs.delete_by_metadata("document_id", doc_id)
            except Exception:  # noqa: BLE001
                pass
            if bm25:
                try:
                    bm25.remove_by_metadata("document_id", doc_id)
                except Exception:  # noqa: BLE001
                    pass
            try:
                fs.mark_index_state(
                    doc_id, "failed",
                    error=(str(e) or e.__class__.__name__)[:400],
                )
            except Exception:  # noqa: BLE001
                pass
        finally:
            task.finished_at = datetime.now()
