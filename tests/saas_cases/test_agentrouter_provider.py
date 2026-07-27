from __future__ import annotations

import json

from app.models.providers.agentrouter_qwen import parse_qwen_json_output


def test_qwen_event_stream_result_is_normalized() -> None:
    payload = [
        {"type": "system", "subtype": "init", "session_id": "session-1", "model": "glm-5.2"},
        {
            "type": "result",
            "subtype": "success",
            "session_id": "session-1",
            "result": '{"artifact":"ok"}',
            "usage": {
                "input_tokens": 8013,
                "output_tokens": 122,
                "cache_read_input_tokens": 200,
                "total_tokens": 8135,
            },
            "stats": {
                "models": {"glm-5.2": {"tokens": {"thoughts": 41}}},
            },
        },
    ]

    response = parse_qwen_json_output(json.dumps(payload), model="glm-5.2")

    assert response.text == '{"artifact":"ok"}'
    assert response.parsed == {"artifact": "ok"}
    assert response.request_id == "session-1"
    assert response.usage.input_tokens == 8013
    assert response.usage.output_tokens == 122
    assert response.usage.thinking_tokens == 41


def test_markdown_fenced_json_is_parsed_without_losing_text() -> None:
    payload = [{
        "type": "result",
        "subtype": "success",
        "session_id": "session-2",
        "result": "```json\n{\"ok\": true}\n```",
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }]

    response = parse_qwen_json_output(json.dumps(payload), model="gpt-5.5")

    assert response.parsed == {"ok": True}
    assert response.text.startswith("```json")
