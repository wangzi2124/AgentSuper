"""SwitchService: extract the model-switch state machine from app.deps.

The service is storage-agnostic. Callers inject session, graph, epoch and audit
dependencies, so the same flow can be tested without importing the FastAPI layer.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .compression import CompressionError, CompressionPlan, compress_messages, estimate_messages

logger = logging.getLogger(__name__)


class ModelSwitchError(Exception):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class SwitchDeps:
    get_session: Callable[[str], Any]
    persist_session: Callable[[str], None]
    read_messages: Callable[[str], Awaitable[list[Any]]]
    graph_state: Callable[[str], Awaitable[dict[str, Any]]]
    context_binding: Callable[[str], dict[str, Any]]
    read_limits: Callable[[str], dict[str, Any]]
    get_epoch: Callable[[str], Any]
    upsert_epoch: Callable[[str, str, int, dict[str, Any]], None]
    stable_message_id: Callable[[str, Any], str]
    is_running: Callable[[str], bool] = lambda _sid: False
    backup_dir: str = ""
    set_request_model: Callable[[str], Any] | None = None
    reset_request_model: Callable[[Any], None] | None = None


class SwitchService:
    def __init__(self, deps: SwitchDeps) -> None:
        self.deps = deps

    # ── audit/backup ────────────────────────────────────────────────
    def _audit(
        self,
        session_id: str,
        old_model: str | None,
        target: str,
        status: str,
        *,
        error: str = "",
        summarized: bool = False,
        tokens: int = 0,
        threshold: int = 0,
        tail_start_id: str | None = None,
    ) -> None:
        path = os.path.join(self.deps.backup_dir, "switches.jsonl")
        record: dict[str, Any] = {
            "session_id": session_id,
            "from": old_model,
            "to": target,
            "status": status,
            "summarized": bool(summarized),
            "tokens": tokens,
            "threshold": threshold,
            "at": time.time(),
        }
        if tail_start_id:
            record["tail_start_id"] = tail_start_id
        if error:
            record["error"] = error
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            logger.warning("写入模型切换审计失败", exc_info=True)

    def _backup(
        self,
        session_id: str,
        old_model: str | None,
        target: str,
        messages: list[Any],
        tokens: int,
        threshold: int,
        keep_tokens: int,
    ) -> str:
        from langchain_core.messages import messages_to_dict

        os.makedirs(self.deps.backup_dir, exist_ok=True)
        stamp = int(time.time() * 1000)
        path = os.path.join(self.deps.backup_dir, f"{session_id}_{stamp}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "session_id": session_id,
                    "from": old_model,
                    "to": target,
                    "tokens": tokens,
                    "threshold": threshold,
                    "keep_recent_tokens": keep_tokens,
                    "status": "prepared",
                    "messages": messages_to_dict(messages),
                },
                fh,
                ensure_ascii=False,
            )
        return path

    @staticmethod
    def _mark(path: str, status: str) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            data["status"] = status
            data["updated_at"] = time.time()
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            logger.warning("更新模型切换备份标记失败: %s", path, exc_info=True)

    def _fail(
        self,
        backup_path: str | None,
        session_id: str,
        old_model: str | None,
        target: str,
        status: str,
        reason: str,
    ) -> None:
        if backup_path:
            self._mark(backup_path, "rolled_back")
        self._audit(session_id, old_model, target, status, error=reason)

    # ── switch ──────────────────────────────────────────────────────
    async def switch(self, session_id: str, target: str) -> dict[str, Any]:
        target = (target or "").strip()
        if not target:
            self._audit(session_id, None, "", "rejected", error="目标模型为空")
            raise ModelSwitchError("目标模型为空", 400)

        sess = self.deps.get_session(session_id)
        if sess is None:
            self._audit(session_id, None, target, "rejected", error="会话不存在")
            raise ModelSwitchError("会话不存在", 404)
        if target == sess.model:
            return {"switched": False, "model": sess.model, "summarized": False}

        limits = self.deps.read_limits(target) or {}
        window = int(limits.get("context_length") or 0)
        if window <= 0:
            reason = f"目标模型上下文窗口未知，已拒绝切换：{target}"
            self._audit(session_id, sess.model, target, "rejected", error=reason)
            raise ModelSwitchError(reason, 400)
        if sess.status == "running" or self.deps.is_running(session_id):
            reason = "会话正在运行，请等待本轮完成后再切换模型"
            self._audit(session_id, sess.model, target, "rejected", error=reason)
            raise ModelSwitchError(reason, 409)

        messages = await self.deps.read_messages(session_id)
        plan = CompressionPlan.for_window(window)
        tokens = estimate_messages(messages)
        threshold = plan.threshold
        keep_tokens = plan.keep_tokens
        old_model = sess.model
        before_ids = [
            self.deps.stable_message_id(session_id, m) for m in messages
        ]

        try:
            backup_path = self._backup(
                session_id, old_model, target, messages, tokens, threshold, keep_tokens
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("切换模型备份失败: %s", exc)
            self._audit(
                session_id,
                old_model,
                target,
                "failed",
                error=f"迁移备份失败：{exc}",
                tokens=tokens,
                threshold=threshold,
            )
            raise ModelSwitchError(f"迁移备份失败，已保留原模型：{exc}", 500) from exc

        summary = ""
        tail_start_id: str | None = None
        try:
            if tokens > threshold and messages:
                existing_summary = self.deps.context_binding(session_id).get("summary", "")
                try:
                    graph_summary = str(
                        (await self.deps.graph_state(session_id)).get("context_summary") or ""
                    )
                except Exception:  # noqa: BLE001
                    graph_summary = ""
                if graph_summary and graph_summary != existing_summary:
                    existing_summary = graph_summary

                token = (
                    self.deps.set_request_model(target)
                    if self.deps.set_request_model is not None
                    else None
                )
                try:
                    result = await compress_messages(
                        messages,
                        existing_summary=existing_summary,
                        model=None,
                        threshold=threshold,
                        keep_tokens=keep_tokens,
                        segment_tokens=max(4_000, min(12_000, threshold // 4)),
                        require_precision=True,
                        raise_on_error=True,
                    )
                finally:
                    if token is not None and self.deps.reset_request_model is not None:
                        self.deps.reset_request_model(token)

                summary = str(result.summary or "").strip()
                if not summary:
                    raise ModelSwitchError(
                        "历史无法压缩到目标模型窗口，已保留原模型", 400
                    )
                tail_start_id = result.tail_start_id
        except ModelSwitchError as exc:
            self._fail(backup_path, session_id, old_model, target, "rolled_back", str(exc))
            raise
        except CompressionError as exc:
            reason = f"事实探针失败，已保留原模型：{exc}"
            self._fail(backup_path, session_id, old_model, target, "rolled_back", reason)
            raise ModelSwitchError(reason, 500) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("切换模型时历史摘要失败：%s", exc)
            reason = f"历史摘要生成失败，已保留原模型：{exc}"
            self._fail(backup_path, session_id, old_model, target, "rolled_back", reason)
            raise ModelSwitchError(reason, 500) from exc

        epoch = self.deps.get_epoch(session_id)
        baseline = epoch.baseline if epoch else ""
        baseline_seq = epoch.baseline_seq if epoch else 0
        old_snapshot = dict(epoch.snapshot or {}) if epoch else {}
        snapshot = dict(old_snapshot)
        snapshot["context_window"] = window
        snapshot["compress_threshold"] = threshold
        snapshot["keep_recent_tokens"] = keep_tokens
        if summary:
            snapshot["summary"] = summary
            snapshot["tail_start_id"] = tail_start_id
            snapshot["summary_model"] = target
            snapshot["summary_at"] = time.time()
        if snapshot.get("summary") or snapshot.get("tail_start_id"):
            snapshot["model_switch"] = True
        try:
            self.deps.upsert_epoch(session_id, baseline, baseline_seq, snapshot)
        except Exception as exc:  # noqa: BLE001
            logger.warning("模型切换绑定写入失败：%s", exc)
            reason = f"模型绑定写入失败，已保留原模型：{exc}"
            self._fail(backup_path, session_id, old_model, target, "rolled_back", reason)
            raise ModelSwitchError(reason, 500) from exc

        after_ids = [
            self.deps.stable_message_id(session_id, m)
            for m in await self.deps.read_messages(session_id)
        ]
        lost = [mid for mid in before_ids if mid not in after_ids]
        if lost:
            try:
                self.deps.upsert_epoch(
                    session_id, baseline, baseline_seq, old_snapshot
                )
            except Exception:  # noqa: BLE001
                logger.warning("回滚上下文绑定失败", exc_info=True)
            self._fail(
                backup_path,
                session_id,
                old_model,
                target,
                "rolled_back",
                f"消息完整性校验失败，丢失 {len(lost)} 条消息，已回滚模型切换",
            )
            raise ModelSwitchError(
                f"消息完整性校验失败，丢失 {len(lost)} 条消息，已回滚模型切换",
                500,
            )

        try:
            sess.model = target
            sess.updated_at = time.time()
            self.deps.persist_session(session_id)
        except Exception as exc:  # noqa: BLE001
            sess.model = old_model
            try:
                self.deps.upsert_epoch(
                    session_id, baseline, baseline_seq, old_snapshot
                )
            except Exception:  # noqa: BLE001
                logger.warning("回滚上下文绑定失败", exc_info=True)
            self._fail(
                backup_path,
                session_id,
                old_model,
                target,
                "rolled_back",
                f"模型持久化失败，已保留原模型：{exc}",
            )
            raise ModelSwitchError(
                f"模型持久化失败，已保留原模型：{exc}", 500
            ) from exc

        self._mark(backup_path, "committed")
        self._audit(
            session_id,
            old_model,
            target,
            "committed",
            summarized=bool(summary),
            tokens=tokens,
            threshold=threshold,
            tail_start_id=tail_start_id,
        )
        return {
            "switched": True,
            "from": old_model,
            "to": target,
            "summarized": bool(summary),
            "tail_start_id": tail_start_id,
            "context_window": window,
            "compress_threshold": threshold,
            "keep_recent_tokens": keep_tokens,
        }


__all__ = ["ModelSwitchError", "SwitchDeps", "SwitchService"]
