from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import sys
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from builder_lab.demo import DemoUnavailable, save_demo


DEFAULT_BRIEF = (
    "Создай премиального AI-сотрудника для архитектурного бюро: русский текст, "
    "метафора чертежа и света, выразительная типографика и аккуратное движение."
)
CHAT_COOKIE_NAMES = {"kaigo_chat_session", "__Host-kaigo_chat_session"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke-test Kaigo builder-lab")
    parser.add_argument("--base-url", default="http://127.0.0.1:8091")
    parser.add_argument("--engine", choices=("direct", "antigravity"), default="direct")
    parser.add_argument("--brief", default=DEFAULT_BRIEF)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--demo-output", type=Path)
    parser.add_argument(
        "--real-chat",
        action="store_true",
        help="require two real Gemini replies in one server-side chat session",
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
    parser.add_argument("--chat-system-prompt-file", type=Path)
    parser.add_argument(
        "--model", default=os.getenv("GEMINI_BUILDER_MODEL", "gemini-3.5-flash")
    )
    return parser.parse_args()


def validate_smoke_evidence(
    engine: str,
    events: list[dict],
    snapshot: dict,
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
    run_id: str,
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
            f"{base_url}/api/runs/{run_id}/chat",
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


async def run_smoke(args: argparse.Namespace) -> int:
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
            validate_smoke_evidence(args.engine, events, snapshot)
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
            if args.real_chat:
                chat_replies = await run_real_chat_smoke(
                    client,
                    base_url=base_url,
                    run_id=run_id,
                    revision=int(snapshot["artifact"]["revision"]),
                    questions=(args.chat_question_one, args.chat_question_two),
                )
            if args.demo_output:
                if (args.source_url is None) != (args.chat_system_prompt_file is None):
                    raise ValueError(
                        "--source-url and --chat-system-prompt-file must be supplied together"
                    )
                system_prompt = (
                    args.chat_system_prompt_file.read_text(encoding="utf-8")
                    if args.chat_system_prompt_file
                    else None
                )
                save_demo(
                    args.demo_output,
                    snapshot,
                    model=args.model,
                    source_url=args.source_url,
                    chat_system_prompt=system_prompt,
                )
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
