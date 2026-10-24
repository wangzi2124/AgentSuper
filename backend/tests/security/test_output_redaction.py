from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from execution.filesystem import VirtualFileSystem
from models.router import ChatResult


class _LeakyModel:
    tier = "fake"

    async def ainvoke(self, messages, *, tools=None, **kwargs):
        return ChatResult(
            content="here is the key sk-abcdefgh12345678",
            finish_reason="stop",
        )


def test_assistant_output_redacts_secrets(tmp_path):
    app = create_app(
        model=_LeakyModel(), vfs=VirtualFileSystem(root=str(tmp_path))
    )
    with TestClient(app) as client:
        body = client.post(
            "/api/chat/multi-agent", json={"message": "leak the key"}
        ).json()

    answer = body["data"]["answer"]
    assert "sk-abcdefgh12345678" not in answer
    assert "[REDACTED]" in answer
