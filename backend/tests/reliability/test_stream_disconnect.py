from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.api.compat import _graph_events
from models.router import request_model, reset_request_model, set_request_model


class _Graph:
    async def astream(self, state, config, stream_mode):
        yield ("updates", {"plan": {}})
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_graph_events_cleanup_on_client_disconnect():
    async def request_permission(**kwargs):
        return False

    ctx = SimpleNamespace(
        graph=_Graph(),
        request_permission=request_permission,
    )
    sess = SimpleNamespace(id="s1", model="model/a")
    token = set_request_model("before")
    try:
        agen = _graph_events(
            ctx, sess, {}, [], asyncio.Event(), {}
        )
        await anext(agen)
        await agen.aclose()
        assert request_model() == "before"
    finally:
        reset_request_model(token)
