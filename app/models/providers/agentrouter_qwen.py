from __future__ import annotations

import asyncio
import json
import os
import signal
import shutil
import subprocess
from contextlib import suppress
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from app.models.contracts import (
    InvalidModelResponse,
    ModelRequest,
    ModelResponse,
    ModelUnavailable,
    ModelUsage,
    ProviderCapabilities,
    ProviderQuotaExceeded,
    ProviderUnavailable,
    UnsupportedModelRequest,
)

# Verified with npm `latest` metadata and the CLI `--version` on 2026-07-28.
QWEN_CODE_VERSION = "0.21.0"
QWEN_CODE_PACKAGE = f"@qwen-code/qwen-code@{QWEN_CODE_VERSION}"
_SIGKILL = getattr(signal, "SIGKILL", 9)

_HOST_ENVIRONMENT_ALLOWLIST = (
    "PATH",
    "Path",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "COMSPEC",
    "PATHEXT",
)


class AgentRouterQwenProvider:
    """AgentRouter adapter executed through its supported Qwen Code client."""

    capabilities = ProviderCapabilities(images=False, structured_output=True)

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://agentrouter.org/v1",
        timeout_seconds: float = 900.0,
        working_directory: str | Path | None = None,
        executable: str | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("AgentRouter API key is required")
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._working_directory = str(working_directory) if working_directory else None
        self._uses_package_runner = executable is None
        self._executable = executable or shutil.which("npx") or "npx"

    async def generate(self, request: ModelRequest, *, model: str) -> ModelResponse:
        if request.images:
            raise UnsupportedModelRequest(
                "AgentRouter Qwen adapter does not yet accept binary image inputs"
            )
        prompt = _provider_prompt(request)
        environment = _allowlisted_host_environment()
        environment.update(
            {
                "OPENAI_API_KEY": self._api_key,
                "OPENAI_BASE_URL": self._base_url,
                "OPENAI_MODEL": model,
                "NO_COLOR": "1",
            }
        )
        if self._working_directory is not None:
            environment["HOME"] = self._working_directory
            environment["TMPDIR"] = self._working_directory
        try:
            package_args = (
                ("-y", QWEN_CODE_PACKAGE)
                if self._uses_package_runner
                else ()
            )
            process = await asyncio.create_subprocess_exec(
                self._executable,
                *package_args,
                "--safe-mode",
                "-m",
                model,
                "-p",
                "",
                "-o",
                "json",
                cwd=self._working_directory,
                env=environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_process_start_options(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise ProviderUnavailable(
                "AgentRouter client could not be started"
            ) from error

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")),
                timeout=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            await _terminate_process_tree(process)
            raise
        except TimeoutError as error:
            await _terminate_process_tree(process)
            raise ProviderUnavailable("AgentRouter generation timed out") from error
        except Exception as error:
            await _terminate_process_tree(process)
            raise ProviderUnavailable(
                "AgentRouter client communication failed"
            ) from error

        output = stdout.decode("utf-8", errors="replace").strip()
        diagnostic = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise _cli_error(output or diagnostic or f"exit code {process.returncode}")
        try:
            response = parse_qwen_json_output(
                output,
                model=model,
                response_schema=request.response_schema,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise InvalidModelResponse(
                f"Qwen Code returned an invalid event stream: {type(error).__name__}"
            ) from error
        if request.response_schema is not None and response.parsed is None:
            raise InvalidModelResponse(
                "Qwen Code did not return valid structured JSON data",
                usage=response.usage,
                request_id=response.request_id,
            )
        return response


def _allowlisted_host_environment() -> dict[str, str]:
    return {
        key: value
        for key in _HOST_ENVIRONMENT_ALLOWLIST
        if (value := os.environ.get(key)) is not None
    }


def _process_start_options(platform: str | None = None) -> dict[str, Any]:
    platform = platform or os.name
    if platform == "nt":
        return {
            "creationflags": getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0x00000200,
            )
        }
    return {"start_new_session": True}


async def _terminate_process_tree(
    process: Any,
    *,
    platform: str | None = None,
) -> None:
    platform = platform or os.name
    if process.returncode is None and getattr(process, "pid", None):
        if platform == "nt":
            with suppress(Exception):
                taskkill = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    env=_allowlisted_host_environment(),
                    **_process_start_options("nt"),
                )
                await taskkill.communicate()
        else:
            with suppress(ProcessLookupError, PermissionError):
                os.killpg(process.pid, _SIGKILL)
    with suppress(ProcessLookupError):
        process.kill()
    with suppress(Exception):
        await process.wait()


def parse_qwen_json_output(
    raw: str,
    *,
    model: str,
    response_schema: Mapping[str, Any] | None = None,
) -> ModelResponse:
    events = json.loads(raw)
    if not isinstance(events, list):
        raise ValueError("Qwen output must be an event array")
    result = next(
        (
            event
            for event in reversed(events)
            if isinstance(event, dict) and event.get("type") == "result"
        ),
        None,
    )
    if not isinstance(result, dict):
        raise ValueError("Qwen output has no result event")
    if result.get("subtype") != "success" or result.get("is_error") is True:
        error = result.get("error")
        message = error.get("message") if isinstance(error, dict) else result.get("result")
        raise _cli_error(str(message or "AgentRouter request failed"))
    text = result.get("result")
    if not isinstance(text, str):
        raise ValueError("Qwen result text is missing")
    if "[API Error:" in text:
        raise _cli_error(text)
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
    thinking = _thinking_tokens(result, model)
    model_usage = ModelUsage(
        input_tokens=_integer(usage.get("input_tokens")),
        output_tokens=_integer(usage.get("output_tokens")),
        thinking_tokens=thinking,
    )
    request_id = str(result.get("session_id")) if result.get("session_id") else None
    parsed = _parse_optional_json(text)
    if response_schema is not None:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise InvalidModelResponse(
                "Qwen Code did not return exact structured JSON",
                usage=model_usage,
                request_id=request_id,
            ) from error
        if not isinstance(parsed, (dict, list)):
            raise InvalidModelResponse(
                "Qwen Code structured output is not JSON data",
                usage=model_usage,
                request_id=request_id,
            )
        try:
            Draft202012Validator.check_schema(response_schema)
            Draft202012Validator(response_schema).validate(parsed)
        except ValidationError as error:
            raise InvalidModelResponse(
                "Qwen Code structured output does not match the response schema",
                usage=model_usage,
                request_id=request_id,
            ) from error
        except SchemaError as error:
            raise ValueError("response schema is invalid") from error
    return ModelResponse(
        text=text,
        parsed=parsed,
        usage=model_usage,
        request_id=request_id,
        raw={"result": result},
    )


def _provider_prompt(request: ModelRequest) -> str:
    instructions = [
        "Work only on the request below.",
        "Do not inspect the repository, call tools, modify files, or ask questions.",
        "Return the requested final payload directly, without a progress report.",
    ]
    if request.response_schema is not None:
        instructions.extend(
            [
                "Return exactly one valid JSON value and no Markdown fences.",
                "The JSON must satisfy this schema:",
                json.dumps(request.response_schema, ensure_ascii=False, separators=(",", ":")),
            ]
        )
    instructions.extend(["REQUEST:", request.prompt])
    return "\n".join(instructions)


def _parse_optional_json(text: str) -> Mapping[str, Any] | list[Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidate = "\n".join(lines[1:-1])
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        start_candidates = [position for position in (candidate.find("{"), candidate.find("[")) if position >= 0]
        if not start_candidates:
            return None
        start = min(start_candidates)
        end = max(candidate.rfind("}"), candidate.rfind("]"))
        if end <= start:
            return None
        try:
            parsed = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _thinking_tokens(result: Mapping[str, Any], model: str) -> int:
    stats = result.get("stats")
    if not isinstance(stats, dict):
        return 0
    models = stats.get("models")
    if not isinstance(models, dict):
        return 0
    model_stats = models.get(model)
    if not isinstance(model_stats, dict):
        return 0
    tokens = model_stats.get("tokens")
    return _integer(tokens.get("thoughts")) if isinstance(tokens, dict) else 0


def _integer(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _cli_error(message: str) -> Exception:
    lower = message.lower()
    if "429" in lower or "quota" in lower or "余额" in lower:
        return ProviderQuotaExceeded("AgentRouter quota is unavailable")
    if "model" in lower and any(marker in lower for marker in ("404", "not found", "无渠道")):
        return ModelUnavailable("AgentRouter model is unavailable")
    return ProviderUnavailable("AgentRouter request failed")
