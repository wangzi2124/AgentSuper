from __future__ import annotations

import json
from pathlib import Path
from typing import get_args

from pydantic import TypeAdapter

from app.contracts.v2 import (
    DURABLE_EVENT_TYPES,
    EVENT_ADAPTER,
    EVENT_DATA_MODELS,
    MESSAGE_LIST_ADAPTER,
    VOLATILE_EVENT_TYPES,
    EventEnvelope,
    EventType,
    FormInfo,
    InboxInfo,
    PermissionRequest,
    SessionInfoV2,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "contracts" / "v2" / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_session_fixture_matches_contract() -> None:
    session = SessionInfoV2.model_validate(load("session.json"))
    assert session.id == "ses_01J8Z3M4X5"
    assert session.model is not None
    assert session.model.provider_id == "deepseek"


def test_message_fixture_uses_ordered_parts() -> None:
    messages = MESSAGE_LIST_ADAPTER.validate_python(load("messages.json"))
    assert [m.type for m in messages] == ["user", "assistant", "compaction", "idle"]
    assistant = messages[1]
    assert assistant.type == "assistant"
    assert [part.type for part in assistant.content] == ["reasoning", "tool", "text"]
    tool = assistant.content[1]
    assert tool.type == "tool"
    assert tool.state.status == "completed"


def test_inbox_and_permission_fixtures() -> None:
    inbox = TypeAdapter(list[InboxInfo]).validate_python(load("inbox.json"))
    assert [item.status for item in inbox] == ["pending", "pending"]
    assert inbox[0].item.delivery == "queue"

    permissions = TypeAdapter(list[PermissionRequest]).validate_python(load("permissions.json"))
    assert permissions[0].action == "edit"
    assert permissions[0].source is not None
    assert permissions[0].source.id == "call_002"


def test_form_fixture() -> None:
    forms = TypeAdapter(list[FormInfo]).validate_python(load("forms.json"))
    assert forms[0].state == "pending"
    assert forms[0].fields[0].options == ["npm", "pnpm", "bun"]


def test_event_fixture_matches_envelope_and_durability() -> None:
    events = [EVENT_ADAPTER.validate_python(raw) for raw in load("events.json")]
    assert events
    assert any(event.type == "session.text.delta" for event in events)
    assert any(event.type == "permission.asked" for event in events)
    for event in events:
        if event.type in DURABLE_EVENT_TYPES:
            assert event.durable is not None, event.type
        if event.type in VOLATILE_EVENT_TYPES:
            assert event.durable is None, event.type


def test_event_registry_is_complete_and_disjoint() -> None:
    declared = set(get_args(EventType))
    assert set(EVENT_DATA_MODELS) == declared
    assert DURABLE_EVENT_TYPES.isdisjoint(VOLATILE_EVENT_TYPES)
    assert DURABLE_EVENT_TYPES | VOLATILE_EVENT_TYPES == declared


def test_event_schema_export() -> None:
    schema = EventEnvelope.model_json_schema()
    assert schema["title"] == "EventEnvelope"
    assert "durable" in schema["properties"]
    assert "data" in schema["properties"]
