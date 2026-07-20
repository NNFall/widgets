from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from builder_lab.demo import DemoUnavailable, _verified_source_url, save_demo
from builder_lab.visual_models import ScreenshotState

if os.name == "nt":
    import msvcrt
else:
    import fcntl


DEFAULT_BRIEF = (
    "Создай премиального AI-сотрудника для архитектурного бюро: русский текст, "
    "метафора чертежа и света, выразительная типографика и аккуратное движение."
)
CHAT_COOKIE_NAMES = {"kaigo_chat_session", "__Host-kaigo_chat_session"}
REQUIRED_VISUAL_SCREENSHOT_IDS = tuple(state.value for state in ScreenshotState)
MAX_BRIEF_CHARS = 12_000
MAX_CHAT_PROMPT_CHARS = 16_000
_SCREENSHOT_EVENT_MESSAGE = re.compile(
    r"^Снимок visual audit: (?P<screenshot_id>[a-z0-9][a-z0-9._-]{0,79}) "
    r"\((?P<byte_count>[1-9][0-9]*) bytes\)$"
)


def _read_utf8_file(value: str) -> str:
    path = Path(value)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise argparse.ArgumentTypeError(
            f"cannot read UTF-8 text from {path}: {exc}"
        ) from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(description="Smoke-test Kaigo builder-lab")
    parser.add_argument("--base-url", default="http://127.0.0.1:8091")
    parser.add_argument("--engine", choices=("direct", "antigravity"), default="direct")
    brief = parser.add_mutually_exclusive_group()
    brief.add_argument("--brief", dest="brief")
    brief.add_argument(
        "--brief-file",
        dest="brief",
        type=_read_utf8_file,
        metavar="PATH",
        help="read the generation brief as UTF-8 text",
    )
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--demo-output", type=Path)
    parser.add_argument(
        "--real-chat",
        "--require-real-chat",
        dest="real_chat",
        action="store_true",
        help="require two real Gemini replies in one server-side chat session",
    )
    parser.add_argument(
        "--require-visual-audit",
        action="store_true",
        help="require final browser screenshots and a passing Gemini visual critique",
    )
    parser.add_argument(
        "--chat-question-one",
        default="Кратко расскажите, чем вы можете помочь посетителю этого сайта.",
    )
    parser.add_argument(
        "--chat-question-two",
        default="Как мой первый вопрос связан с вашим предыдущим ответом?",
    )
    parser.add_argument("--source-url")
    parser.add_argument(
        "--chat-system-prompt-file",
        "--chat-prompt-file",
        dest="chat_system_prompt_file",
        type=Path,
    )
    parser.add_argument(
        "--model", default=os.getenv("GEMINI_BUILDER_MODEL", "gemini-3.5-flash")
    )
    args = parser.parse_args(actual_argv)
    if args.brief is None:
        args.brief = DEFAULT_BRIEF
    args._explicit_options = frozenset(
        token.split("=", 1)[0]
        for token in actual_argv
        if token.startswith("--")
    )
    return args


def preflight_smoke_args(args: argparse.Namespace) -> str | None:
    brief = args.brief.strip() if isinstance(args.brief, str) else ""
    if not brief or len(brief) > MAX_BRIEF_CHARS or "\x00" in brief:
        raise ValueError("generation brief must be non-empty, NUL-free, and at most 12000 characters")

    has_demo_output = args.demo_output is not None
    has_source_url = args.source_url is not None
    has_chat_prompt = args.chat_system_prompt_file is not None
    explicit = getattr(args, "_explicit_options", frozenset())
    ignored_chat_options = explicit.intersection(
        {"--chat-question-one", "--chat-question-two"}
    )
    if ignored_chat_options and not args.real_chat:
        raise ValueError(
            "chat question options would be ignored without --require-real-chat"
        )
    if "--model" in explicit and not has_demo_output:
        raise ValueError("--model would be ignored without --demo-output")
    if (has_source_url or has_chat_prompt) and not has_demo_output:
        raise ValueError(
            "--source-url and --chat-prompt-file require --demo-output"
        )
    if has_demo_output and has_source_url != has_chat_prompt:
        raise ValueError(
            "--source-url and --chat-prompt-file must be supplied together"
        )
    if args.real_chat and not (
        has_demo_output and has_source_url and has_chat_prompt
    ):
        raise ValueError(
            "--require-real-chat requires --demo-output, --source-url, and --chat-prompt-file"
        )
    if has_source_url:
        try:
            args.source_url = _verified_source_url(str(args.source_url))
        except DemoUnavailable as exc:
            raise ValueError(f"invalid demo source URL: {exc}") from exc

    if not has_chat_prompt:
        return None
    prompt_path = Path(args.chat_system_prompt_file)
    try:
        if not prompt_path.is_file():
            raise OSError("path is not a regular file")
        prompt = prompt_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"cannot read UTF-8 chat prompt from {prompt_path}: {exc}"
        ) from exc
    prompt = prompt.strip()
    if (
        not prompt
        or len(prompt) > MAX_CHAT_PROMPT_CHARS
        or "\x00" in prompt
    ):
        raise ValueError(
            "chat prompt must be non-empty, NUL-free, and at most 16000 characters"
        )
    return prompt


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".restore.tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(payload)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


class DemoPublicationLock:
    def __init__(self, demo_path: Path) -> None:
        target = Path(demo_path).resolve(strict=False)
        self.demo_path = target
        self.lock_path = target.with_name(f".{target.name}.smoke.lock")
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self.held:
            raise RuntimeError("demo publication lock is already held")
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(
                f"demo publication lock is already held: {self.lock_path}"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def protects(self, demo_path: Path) -> bool:
        return self.held and Path(demo_path).resolve(strict=False) == self.demo_path

    def __enter__(self) -> "DemoPublicationLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.release()


def restore_demo_publication(
    path: Path,
    *,
    previous_bytes: bytes | None,
    published_bytes: bytes,
    publication_lock: DemoPublicationLock,
) -> None:
    path = Path(path)
    if not publication_lock.protects(path):
        raise RuntimeError("demo publication lock is required for rollback")
    try:
        current = path.read_bytes()
    except FileNotFoundError:
        return
    if current != published_bytes:
        return
    if previous_bytes is None:
        path.unlink(missing_ok=True)
    else:
        _atomic_write_bytes(path, previous_bytes)


def _event_total_tokens(event: dict) -> int:
    usage = event.get("usage") or {}
    explicit_total = int(usage.get("total_tokens", 0) or 0)
    if explicit_total > 0:
        return explicit_total
    return sum(
        int(usage.get(field, 0) or 0)
        for field in ("prompt_tokens", "output_tokens", "thinking_tokens")
    )


def _validate_visual_audit_evidence(events: list[dict], snapshot: dict) -> None:
    passed_indices = [
        index
        for index, event in enumerate(events)
        if event.get("type") == "visual_audit.passed"
    ]
    if not passed_indices:
        raise RuntimeError("smoke requires visual audit passed evidence")

    passed_index = passed_indices[-1]
    started_indices = [
        index
        for index, event in enumerate(events[: passed_index + 1])
        if event.get("type") == "visual_audit.started"
    ]
    if not started_indices:
        raise RuntimeError("smoke requires a complete visual audit attempt")
    cycle = events[started_indices[-1] : passed_index + 1]
    final_revision = (snapshot.get("artifact") or {}).get("revision")

    started = cycle[0]
    if (
        started.get("status") != "running"
        or started.get("revision") != final_revision
    ):
        raise RuntimeError("visual audit started evidence does not match final revision")

    passed = cycle[-1]
    if (
        passed.get("status") != "completed"
        or passed.get("revision") != final_revision
    ):
        raise RuntimeError("visual audit passed evidence does not match final revision")

    screenshot_events = [
        event for event in cycle if event.get("type") == "screenshot.captured"
    ]
    screenshot_ids: list[str] = []
    for event in screenshot_events:
        message = str(event.get("message") or "")
        match = _SCREENSHOT_EVENT_MESSAGE.fullmatch(message)
        screenshot_id = match.group("screenshot_id") if match else None
        if (
            screenshot_id not in REQUIRED_VISUAL_SCREENSHOT_IDS
            or event.get("status") != "completed"
            or event.get("revision") != final_revision
        ):
            raise RuntimeError("visual audit screenshot evidence is malformed")
        screenshot_ids.append(screenshot_id)
    if tuple(screenshot_ids) != REQUIRED_VISUAL_SCREENSHOT_IDS:
        raise RuntimeError("visual audit requires six required screenshots in order")

    completed = [
        event for event in cycle if event.get("type") == "visual_audit.completed"
    ]
    if (
        len(completed) != 1
        or completed[0].get("status") != "completed"
        or completed[0].get("revision") != final_revision
    ):
        raise RuntimeError("smoke requires completed Gemini visual critic evidence")
    if _event_total_tokens(completed[0]) <= 0:
        raise RuntimeError("smoke requires positive Gemini visual critic token usage")
    expected_types = (
        "visual_audit.started",
        *("screenshot.captured" for _ in REQUIRED_VISUAL_SCREENSHOT_IDS),
        "visual_audit.completed",
        "visual_audit.passed",
    )
    if tuple(event.get("type") for event in cycle) != expected_types:
        raise RuntimeError("smoke requires ordered visual audit evidence")
    tail = events[passed_index + 1 :]
    if tuple(event.get("type") for event in tail) != (
        "artifact.committed",
        "run.completed",
    ) or any(
        event.get("status") != "completed"
        or event.get("revision") != final_revision
        for event in tail
    ):
        raise RuntimeError(
            "visual audit pass must own the final event tail through run completion"
        )


def validate_smoke_evidence(
    engine: str,
    events: list[dict],
    snapshot: dict,
    *,
    require_visual_audit: bool = False,
) -> None:
    terminal = next(
        (
            event
            for event in reversed(events)
            if event.get("type") in {"run.completed", "run.failed", "run.cancelled"}
        ),
        None,
    )
    if terminal is None or snapshot.get("status") != "completed":
        raise RuntimeError(
            f"builder run did not complete: {snapshot.get('error_code') or snapshot.get('status')}"
        )
    committed = [event for event in events if event.get("type") == "artifact.committed"]
    if engine == "direct" and len(committed) < 4:
        raise RuntimeError("direct smoke requires at least four committed revisions")
    usage = snapshot.get("usage") or {}
    if int(usage.get("total_tokens", 0) or 0) <= 0:
        raise RuntimeError("smoke requires positive provider token usage")
    if require_visual_audit:
        _validate_visual_audit_evidence(events, snapshot)


def validate_real_chat_evidence(
    replies: list[dict],
    *,
    session_cookie: str | None,
    session_cookie_sent: bool,
    history_marker: str | None = None,
) -> None:
    if not session_cookie:
        raise RuntimeError("real chat smoke requires one persisted session cookie")
    if not session_cookie_sent:
        raise RuntimeError(
            "real chat session cookie was not sent on the second request; "
            "use HTTPS or set KAIGO_CHAT_SECURE_COOKIE=false for loopback smoke"
        )
    if len(replies) != 2:
        raise RuntimeError("real chat smoke requires exactly two chat replies")
    request_ids = []
    messages = []
    for payload in replies:
        request_id = payload.get("request_id")
        reply = payload.get("reply")
        if not isinstance(request_id, str) or not request_id.strip():
            raise RuntimeError("real chat response lost request correlation")
        if not isinstance(reply, str) or not reply.strip():
            raise RuntimeError("real chat response is empty")
        request_ids.append(request_id)
        messages.append(reply.strip())
    if len(set(request_ids)) != 2 or messages[0] == messages[1]:
        raise RuntimeError("real chat responses must be distinct and correlated")
    if history_marker and history_marker not in messages[1]:
        raise RuntimeError("real chat response did not prove conversation history")


def build_preview_smoke_url(
    base_url: str,
    run_id: str,
    revision: int,
    *,
    channel: str | None = None,
) -> str:
    protocol_channel = channel or secrets.token_urlsafe(24)
    query = urlencode({"revision": revision, "channel": protocol_channel})
    return f"{base_url.rstrip('/')}/api/runs/{run_id}/preview?{query}"


async def run_real_chat_smoke(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    revision: int,
    questions: tuple[str, str],
) -> list[dict]:
    parsed = urlsplit(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    history_marker = f"context-{secrets.token_hex(6)}"
    sent_questions = (
        f"{questions[0]}\nКонтрольный маркер диалога: {history_marker}. Запомните его.",
        f"{questions[1]}\nВ конце ответа повторите контрольный маркер из первого сообщения без изменений.",
    )
    replies: list[dict] = []
    session_cookie_sent = False
    for index, question in enumerate(sent_questions):
        request_id = f"request-{uuid.uuid4()}"
        response = await client.post(
            f"{base_url}/demo/chat",
            json={
                "request_id": request_id,
                "message": question,
                "revision": revision,
            },
            headers={"Origin": origin, "X-Kaigo-Chat": "v2"},
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("request_id") != request_id:
            raise RuntimeError("real chat response lost request correlation")
        if index == 1:
            cookie_header = response.request.headers.get("Cookie", "")
            session_cookie_sent = any(
                f"{name}=" in cookie_header for name in CHAT_COOKIE_NAMES
            )
        replies.append(payload)
    validate_real_chat_evidence(
        replies,
        session_cookie=next(
            (
                cookie.value
                for cookie in client.cookies.jar
                if cookie.name in CHAT_COOKIE_NAMES
            ),
            None,
        ),
        session_cookie_sent=session_cookie_sent,
        history_marker=history_marker,
    )
    return replies


async def _run_smoke_locked(
    args: argparse.Namespace,
    *,
    chat_system_prompt: str | None,
    publication_lock: DemoPublicationLock | None,
) -> int:
    base_url = args.base_url.rstrip("/")
    events: list[dict] = []
    async with asyncio.timeout(args.timeout):
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=None)) as client:
            response = await client.post(
                f"{base_url}/api/runs",
                json={"engine": args.engine, "brief": args.brief, "creativity": 0.9},
            )
            response.raise_for_status()
            run_id = response.json()["run_id"]
            async with client.stream(
                "GET", f"{base_url}/api/runs/{run_id}/events"
            ) as stream:
                stream.raise_for_status()
                async for line in stream.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line[6:])
                    events.append(event)
                    print(
                        json.dumps(
                            {
                                "sequence": event["sequence"],
                                "type": event["type"],
                                "stage": event["stage"],
                                "status": event["status"],
                                "revision": event["revision"],
                            },
                            ensure_ascii=False,
                        )
                    )
            snapshot_response = await client.get(f"{base_url}/api/runs/{run_id}")
            snapshot_response.raise_for_status()
            snapshot = snapshot_response.json()
            validate_smoke_evidence(
                args.engine,
                events,
                snapshot,
                require_visual_audit=args.require_visual_audit,
            )
            preview = await client.get(
                build_preview_smoke_url(
                    base_url,
                    run_id,
                    int(snapshot["artifact"]["revision"]),
                )
            )
            preview.raise_for_status()
            if "kaigo-builder-preview" not in preview.text:
                raise RuntimeError("preview acknowledgement runtime is missing")
            chat_replies: list[dict] = []
            previous_demo_bytes: bytes | None = None
            published_demo_bytes: bytes | None = None
            if args.demo_output:
                demo_path = Path(args.demo_output)
                try:
                    previous_demo_bytes = demo_path.read_bytes()
                except FileNotFoundError:
                    previous_demo_bytes = None
                published_demo = save_demo(
                    args.demo_output,
                    snapshot,
                    model=args.model,
                    source_url=args.source_url,
                    chat_system_prompt=chat_system_prompt,
                )
                published_demo_bytes = json.dumps(
                    published_demo.to_dict(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            try:
                if args.real_chat:
                    chat_replies = await run_real_chat_smoke(
                        client,
                        base_url=base_url,
                        revision=int(snapshot["artifact"]["revision"]),
                        questions=(args.chat_question_one, args.chat_question_two),
                    )
            except BaseException:
                if args.demo_output and published_demo_bytes is not None:
                    if publication_lock is None:
                        raise RuntimeError(
                            "demo publication lock was lost before rollback"
                        )
                    restore_demo_publication(
                        Path(args.demo_output),
                        previous_bytes=previous_demo_bytes,
                        published_bytes=published_demo_bytes,
                        publication_lock=publication_lock,
                    )
                raise
            print(
                json.dumps(
                    {
                        "status": snapshot["status"],
                        "revision": snapshot["artifact"]["revision"],
                        "stage": snapshot["artifact"]["stage"],
                        "usage": snapshot["usage"],
                        "elapsed_seconds": snapshot["elapsed_seconds"],
                        "committed_revisions": len(
                            [event for event in events if event["type"] == "artifact.committed"]
                        ),
                        "real_chat_turns": len(chat_replies),
                    },
                    ensure_ascii=False,
                )
            )
            return 0


async def run_smoke(args: argparse.Namespace) -> int:
    chat_system_prompt = preflight_smoke_args(args)
    publication_lock = (
        DemoPublicationLock(Path(args.demo_output)) if args.demo_output else None
    )
    if publication_lock is not None:
        publication_lock.acquire()
    try:
        return await _run_smoke_locked(
            args,
            chat_system_prompt=chat_system_prompt,
            publication_lock=publication_lock,
        )
    finally:
        if publication_lock is not None:
            publication_lock.release()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run_smoke(args))
    except TimeoutError:
        print("builder-lab smoke timed out", file=sys.stderr)
        return 2
    except (httpx.HTTPError, RuntimeError, ValueError, DemoUnavailable) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
