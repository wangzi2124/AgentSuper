from __future__ import annotations

import asyncio
from typing import Any

from app.events.emitter import emit_event
from app.session import repository as repo


async def cancel_pending_forms(ctx: Any, session_id: str) -> list[dict[str, Any]]:
    forms = await asyncio.to_thread(repo.cancel_pending_forms, session_id)
    if not forms:
        return []
    try:
        from execution.tools.question_tools import wake_form

        for form in forms:
            wake_form(str(form["id"]))
    except Exception:  # noqa: BLE001 - no in-process waiter is fine
        pass
    for form in forms:
        try:
            await emit_event(
                ctx,
                "form.cancelled",
                {
                    "session_id": session_id,
                    "form_id": form["id"],
                    "revision": int(form.get("revision", 0)),
                },
                session_id=session_id,
            )
        except Exception:  # noqa: BLE001 - cleanup must not break turn unwinding
            pass
    return forms
