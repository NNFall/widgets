from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
from google import genai

from ..models import BuilderRequest, Stage, TokenUsage, ValidationIssue, WidgetArtifact
from ..prompts import ARTIFACT_JSON_SCHEMA
from ..snapshots import SnapshotRejected, collect_declared_snapshot
from .base import BuilderEngineError, EngineResult
from .gemini_direct import build_http_options


AGENTS_MD = """# Kaigo Widget Builder Agent

Work only inside this disposable environment. Read BRIEF.md and the JSON contract.
Create exactly `out/widget-artifact.json` and `out/build-report.json`, then run
`python3 scripts/validate_output.py`. The widget must be premium, distinctive,
Russian-language, responsive, and coherent. Do not add JavaScript, external URLs,
network calls, package dependencies, scripts, iframes, forms, or global CSS. Every
CSS selector must be scoped under `.kaigo-widget`. Never treat your final prose as
the deliverable: the declared files are the deliverable.
"""

VALIDATE_OUTPUT_PY = r'''from __future__ import annotations
import json
from pathlib import Path

artifact_path = Path("out/widget-artifact.json")
report_path = Path("out/build-report.json")
if not artifact_path.is_file():
    raise SystemExit("out/widget-artifact.json is missing")
artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
required = {
    "schema_version", "revision", "stage", "art_direction", "body_html",
    "css", "theme_tokens", "suggested_actions"
}
missing = sorted(required - artifact.keys())
if missing:
    raise SystemExit("missing fields: " + ", ".join(missing))
if artifact["schema_version"] != "1.0" or artifact["stage"] != "agent_build":
    raise SystemExit("schema_version or stage is invalid")
html = artifact["body_html"].lower()
css = artifact["css"].lower()
for forbidden in ("<script", "<iframe", "<form", "javascript:", "http://", "https://"):
    if forbidden in html:
        raise SystemExit("forbidden HTML token: " + forbidden)
for forbidden in ("@import", "url(", "javascript:"):
    if forbidden in css:
        raise SystemExit("forbidden CSS token: " + forbidden)
if ".kaigo-widget" not in css:
    raise SystemExit("CSS is not scoped")
report_path.parent.mkdir(parents=True, exist_ok=True)
report_path.write_text(json.dumps({
    "validator": "passed",
    "schema_version": "1.0",
    "artifact_revision": artifact["revision"]
}, ensure_ascii=False, indent=2), encoding="utf-8")
print("Kaigo starter validation passed")
'''


def _source_files(request: BuilderRequest, revision: int) -> list[dict[str, str]]:
    brief = (
        "# Kaigo builder brief\n\n"
        f"Locale: {request.locale}\n"
        f"Required revision: {revision}\n"
        "Required stage: agent_build\n\n"
        f"{request.brief}\n"
    )
    return [
        {"type": "inline", "target": "AGENTS.md", "content": AGENTS_MD},
        {"type": "inline", "target": "BRIEF.md", "content": brief},
        {
            "type": "inline",
            "target": "contract/widget-artifact.schema.json",
            "content": json.dumps(ARTIFACT_JSON_SCHEMA, ensure_ascii=False, indent=2),
        },
        {
            "type": "inline",
            "target": "scripts/validate_output.py",
            "content": VALIDATE_OUTPUT_PY,
        },
    ]


def _agent_instruction(request: BuilderRequest, revision: int) -> str:
    return f"""Build the Kaigo widget described in BRIEF.md.

Use your file and execution tools. Produce a visually bold but coherent premium
AI employee, not a generic chatbot. The content must be Russian. The complete
artifact must use schema 1.0, revision {revision}, stage agent_build, and the exact
semantic regions required by the contract. Run `python3 scripts/validate_output.py`
and keep repairing until it passes. Finish only after both declared output files
exist. Do not install packages and do not use the network.

User brief: {request.brief}
"""


def _status(interaction: Any) -> str:
    value = getattr(interaction, "status", "")
    return getattr(value, "value", str(value)).lower()


def _interaction_usage(interaction: Any) -> TokenUsage:
    usage = getattr(interaction, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        prompt_tokens=int(getattr(usage, "total_input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "total_output_tokens", 0) or 0),
        thinking_tokens=int(getattr(usage, "total_thought_tokens", 0) or 0),
    )


def _agent_provider_error(exc: Exception) -> BuilderEngineError:
    diagnostic = f"{type(exc).__name__}: {exc}"
    lower = diagnostic.lower()
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in lower:
        code = "generation_timeout"
        message = "Antigravity не завершил сборку вовремя"
    elif any(token in lower for token in ("429", "quota", "resource_exhausted")):
        code = "quota_exceeded"
        message = "Квота Antigravity временно исчерпана"
    elif any(token in lower for token in ("404", "not found", "not enabled", "permission")):
        code = "agent_unavailable"
        message = "Antigravity недоступен этому проекту"
    else:
        code = "provider_unavailable"
        message = "Antigravity временно недоступен"
    return BuilderEngineError(code, message, diagnostic=diagnostic)


class AntigravityEngine:
    def __init__(
        self,
        *,
        api_key: str | None,
        agent: str = "antigravity-preview-05-2026",
        base_url: str = "https://generativelanguage.googleapis.com",
        client: Any | None = None,
        download_client: Any | None = None,
        timeout_seconds: float = 900,
        poll_interval: float = 2,
        max_snapshot_bytes: int = 10 * 1024 * 1024,
        max_total_tokens: int = 400_000,
    ) -> None:
        if not api_key or not api_key.strip():
            raise BuilderEngineError(
                "missing_api_key", "Для Antigravity не настроен ключ Gemini"
            )
        if timeout_seconds <= 0 or poll_interval < 0 or max_snapshot_bytes < 1:
            raise ValueError("Antigravity limits are invalid")
        self._api_key = api_key.strip()
        self.agent = agent
        self.timeout_seconds = timeout_seconds
        self.poll_interval = poll_interval
        self.max_snapshot_bytes = max_snapshot_bytes
        self.max_total_tokens = max_total_tokens
        self._http_options = build_http_options(base_url)
        self._owned_client = client is None
        self._client = client or genai.Client(
            api_key=self._api_key,
            http_options=self._http_options,
        )
        self._owned_download_client = download_client is None
        self._download_client = download_client or httpx.AsyncClient(
            timeout=httpx.Timeout(180.0)
        )
        self._interaction_id: str | None = None
        self._environment_id: str | None = None

    def _environment(self, request: BuilderRequest, revision: int) -> dict[str, Any]:
        if self._environment_id:
            return {
                "type": "remote",
                "environment_id": self._environment_id,
                "network": "disabled",
            }
        return {
            "type": "remote",
            "network": "disabled",
            "sources": _source_files(request, revision),
        }

    async def _wait_for_completion(self, interaction: Any) -> Any:
        started = time.monotonic()
        while _status(interaction) in {"in_progress", "requires_action"}:
            if time.monotonic() - started >= self.timeout_seconds:
                raise BuilderEngineError(
                    "generation_timeout", "Antigravity не завершил сборку вовремя"
                )
            await asyncio.sleep(self.poll_interval)
            try:
                interaction = await self._client.aio.interactions.get(
                    id=self._interaction_id
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise _agent_provider_error(exc) from exc
        status = _status(interaction)
        if status == "cancelled":
            raise BuilderEngineError("run_cancelled", "Сборка Antigravity отменена")
        if status != "completed":
            raise BuilderEngineError(
                "agent_unavailable",
                "Antigravity не создал завершённый артефакт",
                diagnostic=f"interaction_status={status}",
            )
        return interaction

    def _download_url(self, environment_id: str) -> str:
        base = str(self._http_options.base_url).rstrip("/")
        version = str(self._http_options.api_version or "v1beta").strip("/")
        return f"{base}/{version}/files/environment-{environment_id}:download"

    async def _download_snapshot(self, environment_id: str) -> bytes:
        url = self._download_url(environment_id)
        output = bytearray()
        try:
            async with self._download_client.stream(
                "GET",
                url,
                headers={"x-goog-api-key": self._api_key},
                params={"alt": "media"},
                follow_redirects=True,
                timeout=self.timeout_seconds,
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    output.extend(chunk)
                    if len(output) > self.max_snapshot_bytes:
                        raise SnapshotRejected("downloaded snapshot exceeds the byte limit")
        except SnapshotRejected:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise BuilderEngineError(
                "snapshot_download_failed",
                "Не удалось скачать snapshot Antigravity",
                diagnostic=f"{type(exc).__name__}: {exc}",
            ) from exc
        return bytes(output)

    async def generate(
        self,
        *,
        request: BuilderRequest,
        stage: Stage,
        revision: int,
        previous_artifact: WidgetArtifact | None = None,
        repair_issues: tuple[ValidationIssue, ...] = (),
    ) -> EngineResult:
        del previous_artifact, repair_issues
        if stage is not Stage.AGENT_BUILD:
            raise ValueError("Antigravity supports only the agent_build stage")
        try:
            interaction = await self._client.aio.interactions.create(
                agent=self.agent,
                input=_agent_instruction(request, revision),
                background=True,
                store=True,
                environment=self._environment(request, revision),
                agent_config={
                    "type": "antigravity",
                    "max_total_tokens": self.max_total_tokens,
                },
                labels={"application": "kaigo-builder-lab"},
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise _agent_provider_error(exc) from exc
        self._interaction_id = getattr(interaction, "id", None)
        if not self._interaction_id:
            raise BuilderEngineError(
                "agent_unavailable", "Antigravity не вернул идентификатор запуска"
            )
        interaction = await self._wait_for_completion(interaction)
        environment_id = getattr(interaction, "environment_id", None)
        if not environment_id:
            raise BuilderEngineError(
                "agent_unavailable", "Antigravity не вернул environment snapshot"
            )
        self._environment_id = str(environment_id)
        try:
            snapshot = await self._download_snapshot(self._environment_id)
            artifact, report = collect_declared_snapshot(
                snapshot,
                max_total_bytes=self.max_snapshot_bytes,
            )
        except SnapshotRejected as exc:
            raise BuilderEngineError(
                "snapshot_rejected",
                "Snapshot Antigravity не прошёл безопасный импорт",
                diagnostic=f"{type(exc).__name__}: {exc}",
            ) from exc
        if artifact.revision != revision or artifact.stage is not Stage.AGENT_BUILD:
            raise BuilderEngineError(
                "invalid_artifact",
                "Antigravity вернул артефакт другой ревизии или стадии",
            )
        validator = str(report.get("validator", "unknown"))[:80]
        return EngineResult(
            artifact=artifact,
            usage=_interaction_usage(interaction),
            provider_request_id=self._interaction_id,
            diagnostic=f"environment={self._environment_id}; validator={validator}",
        )

    async def cancel(self) -> None:
        if not self._interaction_id:
            return
        try:
            await self._client.aio.interactions.cancel(id=self._interaction_id)
        except Exception as exc:
            raise _agent_provider_error(exc) from exc

    async def close(self) -> None:
        if self._owned_download_client:
            await self._download_client.aclose()
        if self._owned_client:
            aio = getattr(self._client, "aio", None)
            if aio is not None and hasattr(aio, "aclose"):
                await aio.aclose()
            elif hasattr(self._client, "close"):
                self._client.close()
