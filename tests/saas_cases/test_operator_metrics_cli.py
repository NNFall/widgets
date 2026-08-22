from __future__ import annotations

from io import BytesIO
import json
from urllib.error import HTTPError

import pytest

from scripts import operator_metrics


class _Response:
    def __init__(self, payload: object, status: int = 200) -> None:
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_funnel_command_sends_bearer_and_safe_filters(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "t" * 32
    captured: dict[str, object] = {}

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return _Response({"stages": []})

    monkeypatch.setenv("KAIGO_OPERATOR_READ_TOKEN", token)
    monkeypatch.setattr(operator_metrics, "urlopen", opener)

    assert operator_metrics.main(
        [
            "funnel",
            "--base-url",
            "https://example.test/",
            "--from",
            "2026-08-22",
            "--to",
            "2026-08-23",
            "--source",
            "yandex",
        ]
    ) == 0

    assert captured == {
        "url": "https://example.test/api/operator/funnel?from=2026-08-22&to=2026-08-23&source=yandex",
        "authorization": f"Bearer {token}",
        "timeout": 20.0,
    }
    assert json.loads(capsys.readouterr().out) == {"stages": []}


def test_snapshot_combines_funnel_and_runs(monkeypatch, capsys) -> None:
    monkeypatch.setenv("KAIGO_OPERATOR_READ_TOKEN", "t" * 32)

    def opener(request, timeout):
        if "/funnel?" in request.full_url:
            return _Response({"stages": [{"journeys": 3}]})
        return _Response({"runs": [{"id": "run-1"}]})

    monkeypatch.setattr(operator_metrics, "urlopen", opener)

    assert operator_metrics.main(
        [
            "snapshot",
            "--base-url",
            "https://example.test",
            "--from",
            "2026-08-22",
            "--to",
            "2026-08-23",
            "--source",
            "yandex",
            "--limit",
            "5",
        ]
    ) == 0

    assert json.loads(capsys.readouterr().out) == {
        "funnel": {"stages": [{"journeys": 3}]},
        "runs": {"runs": [{"id": "run-1"}]},
    }


def test_cli_errors_do_not_echo_token_or_response_body(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "t" * 32
    private_body = "private-body-should-not-be-printed"
    monkeypatch.setenv("KAIGO_OPERATOR_READ_TOKEN", token)

    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=BytesIO(private_body.encode("utf-8")),
        )

    monkeypatch.setattr(operator_metrics, "urlopen", opener)

    assert operator_metrics.main(["runs"]) == 1
    captured = capsys.readouterr()
    assert token not in captured.err
    assert private_body not in captured.err
    assert "401" in captured.err


def test_cli_requires_a_token(monkeypatch, capsys) -> None:
    monkeypatch.delenv("KAIGO_OPERATOR_READ_TOKEN", raising=False)
    monkeypatch.delenv("KAIGO_OPERATOR_READ_TOKEN_FILE", raising=False)

    assert operator_metrics.main(["runs"]) == 1
    assert "KAIGO_OPERATOR_READ_TOKEN" in capsys.readouterr().err


def test_cli_reads_a_single_line_token_file(monkeypatch, tmp_path, capsys) -> None:
    token = "f" * 32
    token_file = tmp_path / "operator-read-token"
    token_file.write_text(f"\n{token}\n", encoding="utf-8")
    monkeypatch.delenv("KAIGO_OPERATOR_READ_TOKEN", raising=False)
    monkeypatch.setenv("KAIGO_OPERATOR_READ_TOKEN_FILE", str(token_file))
    monkeypatch.setattr(
        operator_metrics,
        "urlopen",
        lambda request, timeout: _Response({"runs": []}),
    )

    assert operator_metrics.main(["runs"]) == 0
    assert json.loads(capsys.readouterr().out) == {"runs": []}


def test_cli_rejects_multiline_token_file(monkeypatch, tmp_path, capsys) -> None:
    token_file = tmp_path / "operator-read-token"
    token_file.write_text("t" * 32 + "\nsecond-line\n", encoding="utf-8")
    monkeypatch.delenv("KAIGO_OPERATOR_READ_TOKEN", raising=False)
    monkeypatch.setenv("KAIGO_OPERATOR_READ_TOKEN_FILE", str(token_file))

    assert operator_metrics.main(["runs"]) == 1
    assert "KAIGO_OPERATOR_READ_TOKEN" in capsys.readouterr().err
