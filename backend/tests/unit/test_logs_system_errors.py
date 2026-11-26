from types import SimpleNamespace

from app.api import logs as logs_api


def test_system_errors_merges_error_and_critical_and_deduplicates(monkeypatch):
    calls = []

    class FakeStore:
        def list_entries(self, **kwargs):
            calls.append(kwargs)
            if kwargs["level"] == "ERROR":
                return {"entries": [{"id": "1", "ts": 2}, {"id": "2", "ts": 1}], "total": 2}
            return {"entries": [{"id": "1", "ts": 2}, {"id": "3", "ts": 3}], "total": 2}

    monkeypatch.setattr(logs_api, "_store", lambda: FakeStore())
    request = SimpleNamespace(
        state=SimpleNamespace(user_id="u1"),
        headers={},
        client=SimpleNamespace(host="127.0.0.1"),
    )

    result = logs_api.system_errors(request, limit=5)

    assert result["code"] == 0
    assert [row["id"] for row in result["data"]["entries"]] == ["3", "1", "2"]
    assert result["data"]["total"] == 3
    assert all(call["stage"] == "system" for call in calls)
    assert {call["level"] for call in calls} == {"ERROR", "CRITICAL"}
