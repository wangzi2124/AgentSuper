from types import SimpleNamespace

from app.api.v2.session import _message_v2


def test_synthetic_warning_maps_to_v2_message():
    raw = SimpleNamespace(
        type="synthetic",
        id="msg_1_action_warning",
        time_created=1234,
        data={
            "text": "我还没有实际执行写入/修改/删除等工具调用，无法确认操作已完成。",
            "description": "action_not_executed",
            "metadata": {"warning_id": "msg_1_action_warning"},
        },
    )

    assert _message_v2(raw) == {
        "type": "synthetic",
        "id": "msg_1_action_warning",
        "time_created": 1234,
        "text": "我还没有实际执行写入/修改/删除等工具调用，无法确认操作已完成。",
        "description": "action_not_executed",
        "metadata": {"warning_id": "msg_1_action_warning"},
    }
