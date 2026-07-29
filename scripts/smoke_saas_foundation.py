from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import ClientSession, ClientTimeout, web
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web_app import NotAppKeyWarning
from aiohttp_session import setup as setup_session
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.oauth import OAuthIdentity
from app.auth.routes import OAUTH_PROVIDERS_KEY, setup_auth_routes
from app.auth.session_storage import DatabaseSessionStorage
from app.billing.contracts import (
    CheckoutCommand,
    Money,
    PaymentStatus,
    ProviderCheckout,
    ProviderNotification,
    ProviderPayment,
)
from app.billing.payments import BillingService
from app.billing.routes import BILLING_SERVICE_KEY, setup_billing_routes
from app.billing.service import TrialSettlementReconciler
from app.db.base import Base
from app.db.session import SESSION_FACTORY_KEY
from app.projects.routes import setup_project_routes
from app.publication.routes import setup_publication_routes
from app.saas.models import (
    AnonymousDraft,
    GenerationArtifact,
    GenerationRun,
    PaymentWebhookEvent,
    Project,
    Publication,
    Subscription,
    UsageLedger,
)
from builder_lab.models import Stage, WidgetArtifact
from builder_lab.worker import BuilderWorker, PostgresWorkerQueue, StageResult


warnings.filterwarnings("ignore", category=NotAppKeyWarning)

_HTML = """
<section class="kaigo-widget" data-region="root" aria-label="AI assistant">
  <button class="kaigo-widget__launcher" data-region="launcher" aria-label="Open AI assistant" type="button"><span>AI</span></button>
  <div class="kaigo-widget__panel" data-region="panel" role="dialog" aria-label="AI assistant dialog">
    <header class="kaigo-widget__header" data-region="header"><h2>Kaigo assistant</h2></header>
    <main class="kaigo-widget__messages" data-region="messages" aria-live="polite"><p>How can I help?</p></main>
    <div class="kaigo-widget__suggestions" data-region="suggestions"><button type="button">Choose a service</button></div>
    <div class="kaigo-widget__composer" data-region="composer" role="group" aria-label="Message"><input aria-label="Enter a message"><button type="button">Send</button></div>
  </div>
</section>
"""
_CSS = """
.kaigo-widget { --accent: #f38b55; color: #18212b; position: relative; }
.kaigo-widget .kaigo-widget__panel { animation: kaigo-rise 600ms ease-out 1 both; }
@keyframes kaigo-rise { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
@media (prefers-reduced-motion: reduce) { .kaigo-widget * { animation: none !important; transition: none !important; } }
"""


class DeployedSmokeFailure(RuntimeError):
    def __init__(
        self,
        check: str,
        message: str,
        *,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.check = check
        self.status = status


async def _deployed_response(
    client: ClientSession,
    path: str,
    *,
    check: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    json_body: dict | None = None,
) -> tuple[int, str, str]:
    try:
        async with client.request(
            method,
            path,
            headers=headers,
            json=json_body,
            allow_redirects=False,
        ) as response:
            body = await response.text()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            if response.status < 200 or response.status >= 300:
                raise DeployedSmokeFailure(
                    check,
                    f"{method} {path} returned {response.status}: {body[:500]}",
                    status=response.status,
                )
            return response.status, content_type, body
    except DeployedSmokeFailure:
        raise
    except Exception as error:  # noqa: BLE001
        raise DeployedSmokeFailure(
            check, f"{method} {path} failed: {type(error).__name__}: {error}"
        ) from error


def _object_json(body: str, *, check: str) -> dict:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise DeployedSmokeFailure(
            check, f"response is not valid JSON: {error}"
        ) from error
    if not isinstance(payload, dict):
        raise DeployedSmokeFailure(check, "JSON response must be an object")
    return payload


async def run_deployed_smoke(
    base_url: str,
    *,
    cookie: str | None = None,
    project_id: str | None = None,
    public_key: str | None = None,
    webhook_fixture: Path | None = None,
    embed_origin: str | None = None,
    timeout_seconds: float = 20,
    require_release_ready: bool = False,
    readiness_token: str | None = None,
) -> dict[str, object]:
    if bool(cookie) != bool(project_id):
        raise DeployedSmokeFailure(
            "configuration",
            "owner checks require both cookie and project_id",
        )
    if embed_origin and not public_key:
        raise DeployedSmokeFailure(
            "configuration",
            "embed_origin requires public_key",
        )
    if cookie and ("\r" in cookie or "\n" in cookie):
        raise DeployedSmokeFailure(
            "configuration", "cookie must not contain line breaks"
        )

    timeout = ClientTimeout(total=timeout_seconds)
    async with ClientSession(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        headers={"User-Agent": "kaigo-saas-canary/1"},
    ) as client:
        health_status, health_type, health_body = await _deployed_response(
            client, "/api/health", check="health"
        )
        health = _object_json(health_body, check="health")
        if health.get("status") != "ok":
            raise DeployedSmokeFailure(
                "health", "health response does not contain status=ok"
            )

        readiness_status, readiness_type, readiness_body = await _deployed_response(
            client,
            "/api/ready",
            check="readiness",
            headers=(
                {"X-Kaigo-Readiness-Token": readiness_token}
                if readiness_token
                else None
            ),
        )
        readiness = _object_json(readiness_body, check="readiness")
        if (
            readiness_type != "application/json"
            or readiness.get("status") != "ready"
            or not isinstance(readiness.get("database"), dict)
            or readiness["database"].get("status") != "ok"
            or not isinstance(readiness.get("worker"), dict)
            or readiness["worker"].get("status") != "ok"
        ):
            raise DeployedSmokeFailure(
                "readiness",
                "readiness response does not prove database and worker activity",
                status=readiness_status,
            )

        studio_status, studio_type, studio_body = await _deployed_response(
            client, "/studio/", check="studio"
        )
        if studio_type != "text/html" or "<html" not in studio_body.lower():
            raise DeployedSmokeFailure(
                "studio", "Studio response is not an HTML document"
            )

        auth_status, auth_type, auth_body = await _deployed_response(
            client,
            "/api/auth/session",
            check="auth_session",
            headers={"Cookie": cookie} if cookie else None,
        )
        auth = _object_json(auth_body, check="auth_session")
        if (
            auth_type != "application/json"
            or not isinstance(auth.get("authenticated"), bool)
            or not isinstance(auth.get("providers"), list)
        ):
            raise DeployedSmokeFailure(
                "auth_session",
                "auth session response does not match the public contract",
            )

        owner_evidence: dict[str, object] = {"status": "skipped"}
        oauth_evidence: dict[str, object]
        if cookie and project_id:
            if auth["authenticated"] is not True:
                raise DeployedSmokeFailure(
                    "auth_session",
                    "the supplied browser cookie is not authenticated",
                    status=auth_status,
                )
            owner_headers = {"Cookie": cookie}
            _, _, project_body = await _deployed_response(
                client,
                f"/api/projects/{project_id}",
                check="owner_project",
                headers=owner_headers,
            )
            project = _object_json(project_body, check="owner_project")
            active_run = project.get("active_run")
            run_id = active_run.get("id") if isinstance(active_run, dict) else None
            if project.get("id") != project_id or not isinstance(run_id, str):
                raise DeployedSmokeFailure(
                    "owner_project",
                    "project response has no matching project or active run",
                )

            _, _, run_body = await _deployed_response(
                client,
                f"/api/runs/{run_id}",
                check="owner_run",
                headers=owner_headers,
            )
            run = _object_json(run_body, check="owner_run")
            if run.get("id") != run_id or run.get("project_id") != project_id:
                raise DeployedSmokeFailure(
                    "owner_run", "run response is not bound to the supplied project"
                )

            _, sse_type, sse_body = await _deployed_response(
                client,
                f"/api/runs/{run_id}/events",
                check="owner_sse",
                headers={**owner_headers, "Last-Event-ID": "0"},
            )
            sse_terminal = (
                sse_type == "text/event-stream" and "event: run.completed" in sse_body
            )
            if not sse_terminal:
                raise DeployedSmokeFailure(
                    "owner_sse",
                    "SSE response did not contain a terminal run.completed event",
                )

            _, _, preview_body = await _deployed_response(
                client,
                f"/api/runs/{run_id}/preview",
                check="owner_preview",
                headers=owner_headers,
            )
            preview = _object_json(preview_body, check="owner_preview")
            artifact = preview.get("artifact")
            revision = artifact.get("revision") if isinstance(artifact, dict) else None
            if not isinstance(revision, int) or revision < 1:
                raise DeployedSmokeFailure(
                    "owner_preview", "preview response has no valid artifact revision"
                )
            preview_query = urlencode(
                {
                    "revision": revision,
                    "channel": "kaigo_canary_preview_channel_01",
                }
            )
            _, preview_type, preview_document = await _deployed_response(
                client,
                f"/api/runs/{run_id}/preview/document?{preview_query}",
                check="owner_preview_document",
                headers=owner_headers,
            )
            preview_document_ok = (
                preview_type == "text/html"
                and "data-kaigo-runtime-input" in preview_document
            )
            if not preview_document_ok:
                raise DeployedSmokeFailure(
                    "owner_preview_document",
                    "preview document does not contain the trusted runtime marker",
                )
            oauth_evidence = {"status": "provided_session"}
            owner_evidence = {
                "status": "passed",
                "project_id": project_id,
                "run_id": run_id,
                "run_status": run.get("status"),
                "sse_terminal": sse_terminal,
                "preview_revision": revision,
                "preview_document": preview_document_ok,
            }
        else:
            oauth_evidence = {
                "status": "skipped",
                "reason": "real browser OAuth credentials were not supplied",
                "required": ["cookie", "project_id"],
            }

        publication_evidence: dict[str, object] = {"status": "skipped"}
        if public_key:
            _, embed_type, embed_body = await _deployed_response(
                client,
                f"/embed/{public_key}.js",
                check="embed",
            )
            embed_ok = (
                embed_type in {"application/javascript", "text/javascript"}
                and "iframe" in embed_body
            )
            if not embed_ok:
                raise DeployedSmokeFailure(
                    "embed", "embed loader does not contain the expected iframe runtime"
                )
            runtime_headers = (
                {"Referer": f"{embed_origin.rstrip('/')}/kaigo-canary"}
                if embed_origin
                else None
            )
            _, runtime_type, runtime_body = await _deployed_response(
                client,
                f"/runtime/{public_key}",
                check="runtime",
                headers=runtime_headers,
            )
            runtime_ok = (
                runtime_type == "text/html"
                and 'data-kaigo-runtime="kaigo-widget"' in runtime_body
            )
            if not runtime_ok:
                raise DeployedSmokeFailure(
                    "runtime", "published runtime marker was not found"
                )
            publication_evidence = {
                "status": "passed",
                "stable_key": public_key,
                "embed": embed_ok,
                "runtime": runtime_ok,
            }

        webhook_evidence: dict[str, object] = {"status": "skipped"}
        if webhook_fixture:
            try:
                webhook_payload = json.loads(
                    webhook_fixture.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as error:
                raise DeployedSmokeFailure(
                    "configuration",
                    f"webhook fixture cannot be read as JSON: {error}",
                ) from error
            if not isinstance(webhook_payload, dict):
                raise DeployedSmokeFailure(
                    "configuration", "webhook fixture JSON must be an object"
                )
            webhook_results: list[dict] = []
            for attempt in ("first", "replay"):
                _, webhook_type, webhook_body = await _deployed_response(
                    client,
                    "/api/billing/webhooks/yookassa",
                    check=f"billing_webhook_{attempt}",
                    method="POST",
                    json_body=webhook_payload,
                )
                webhook_response = _object_json(
                    webhook_body, check=f"billing_webhook_{attempt}"
                )
                if (
                    webhook_type != "application/json"
                    or webhook_response.get("accepted") is not True
                    or not isinstance(webhook_response.get("processed"), bool)
                ):
                    raise DeployedSmokeFailure(
                        f"billing_webhook_{attempt}",
                        "webhook response does not match the verified contract",
                    )
                webhook_results.append(webhook_response)
            if webhook_results[0]["processed"] is not True:
                raise DeployedSmokeFailure(
                    "billing_webhook_first",
                    "first webhook delivery must report processed=true",
                )
            if webhook_results[1]["processed"] is not False:
                raise DeployedSmokeFailure(
                    "billing_webhook_replay",
                    "webhook replay was processed more than once",
                )
            webhook_evidence = {
                "status": "passed",
                "first_processed": webhook_results[0]["processed"],
                "replay_processed": webhook_results[1]["processed"],
            }

    release_checks = {
        "owner_journey": owner_evidence,
        "publication": publication_evidence,
        "billing_webhook": webhook_evidence,
    }
    missing_release_checks = [
        name
        for name, evidence in release_checks.items()
        if evidence.get("status") != "passed"
    ]
    release_ready = not missing_release_checks
    if require_release_ready and not release_ready:
        raise DeployedSmokeFailure(
            "release_readiness",
            "release evidence is incomplete; missing passed checks: "
            + ", ".join(missing_release_checks),
        )

    return {
        "ok": True,
        "mode": "deployed",
        "base_url": base_url.rstrip("/"),
        "release_ready": release_ready,
        "release_status": "complete" if release_ready else "incomplete",
        "missing_release_checks": missing_release_checks,
        "checks": {
            "health": {
                "status": health_status,
                "content_type": health_type,
                "provider_status": health["status"],
            },
            "readiness": {
                "status": readiness_status,
                "content_type": readiness_type,
                "database": readiness["database"],
                "worker": readiness["worker"],
            },
            "studio": {
                "status": studio_status,
                "content_type": studio_type,
            },
            "auth_session": {
                "status": auth_status,
                "content_type": auth_type,
                "authenticated": auth["authenticated"],
                "enabled": auth.get("enabled"),
                "providers": auth["providers"],
            },
        },
        "oauth": oauth_evidence,
        "owner_journey": owner_evidence,
        "publication": publication_evidence,
        "billing_webhook": webhook_evidence,
    }


class _OAuthProvider:
    def authorization_url(self, transaction) -> str:
        return f"https://identity.example/authorize?state={transaction.state}"

    async def exchange(self, transaction, callback) -> OAuthIdentity:
        if callback.code != "acceptance-code" or transaction.state != callback.state:
            raise RuntimeError("unexpected OAuth callback")
        return OAuthIdentity(
            provider="google",
            subject="acceptance-user",
            email="acceptance@example.com",
            email_verified=True,
            display_name="Acceptance User",
            profile={"source": "acceptance-smoke"},
        )


class _PaymentProvider:
    name = "fakepay"
    merchant_account_fingerprint = "a" * 64

    def __init__(self) -> None:
        self.checkout_calls: list[CheckoutCommand] = []

    async def create_checkout(self, command: CheckoutCommand) -> ProviderCheckout:
        self.checkout_calls.append(command)
        payment_id = command.metadata["payment_attempt_id"]
        return ProviderCheckout(
            provider_payment_id=f"pay-{payment_id}",
            checkout_url=f"https://pay.example/{payment_id}",
            status=PaymentStatus.PENDING,
            amount=command.amount,
            paid=False,
            metadata=command.metadata,
            test_mode=True,
        )

    def parse_notification(self, payload: object) -> ProviderNotification:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("payment_id"), str
        ):
            raise ValueError("invalid notification")
        event = payload.get("event", "payment.succeeded")
        if not isinstance(event, str):
            raise ValueError("invalid notification")
        return ProviderNotification(
            provider_payment_id=payload["payment_id"], event=event
        )

    async def verify_notification(
        self,
        notification: ProviderNotification,
        *,
        expected_amount: Money | None = None,
        expected_metadata=None,
    ) -> ProviderPayment:
        if expected_amount is None:
            raise RuntimeError("expected amount is required")
        return ProviderPayment(
            provider_payment_id=notification.provider_payment_id,
            status=PaymentStatus.SUCCEEDED,
            amount=expected_amount,
            paid=True,
            metadata=dict(expected_metadata or {}),
            test_mode=True,
        )

    async def aclose(self) -> None:
        return None


def _artifact(revision: int, stage: Stage) -> WidgetArtifact:
    return WidgetArtifact(
        schema_version="1.0",
        revision=revision,
        stage=stage,
        art_direction="Warm, accessible acceptance-test assistant",
        body_html=_HTML,
        css=_CSS,
        theme_tokens={"accent": "#f38b55"},
        suggested_actions=("Choose a service",),
        change_summary=f"Completed {stage.value}",
    )


async def _application(database: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    provider = _PaymentProvider()
    billing = BillingService(factory, provider)
    config = SimpleNamespace(
        public_auth_enabled=False,
        public_base_url="https://kaigo.example",
        environment="development",
        publication_allow_insecure_origins=False,
        publication_chat_signing_secret="acceptance-signing-secret-at-least-32-bytes",
        publication_chat_capability_ttl_seconds=300,
        publication_chat_key_rate_limit_requests=120,
        publication_chat_ip_rate_limit_requests=60,
        publication_chat_trusted_proxy_cidrs=(),
        chat_rate_limit_window_seconds=60,
    )
    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = config
    app[OAUTH_PROVIDERS_KEY] = {"google": _OAuthProvider()}
    app[BILLING_SERVICE_KEY] = billing
    app["project_sse_poll_seconds"] = 0.001
    setup_session(
        app,
        DatabaseSessionStorage(
            cookie_name="kaigo_acceptance",
            max_age=3600,
            secure=False,
            httponly=True,
            samesite="Lax",
        ),
    )
    setup_auth_routes(
        app,
        public_base_url="https://kaigo.example",
        public_auth_enabled=True,
    )
    setup_project_routes(app)
    setup_billing_routes(app)
    setup_publication_routes(app)
    client = TestClient(TestServer(app))
    await client.start_server()
    return engine, factory, billing, provider, client


async def _json(response, expected_status: int) -> dict:
    if response.status != expected_status:
        body = await response.text()
        raise AssertionError(
            f"{response.method} {response.url} returned {response.status}, "
            f"expected {expected_status}: {body}"
        )
    payload = await response.json()
    if not isinstance(payload, dict):
        raise AssertionError("JSON response must be an object")
    return payload


async def run_acceptance_journey(database: str | Path) -> dict[str, object]:
    database_path = Path(database).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.unlink(missing_ok=True)
    engine, factory, billing, payment_provider, client = await _application(
        database_path
    )
    try:
        stale = await _json(
            await client.post(
                "/api/drafts",
                json={"url": "https://stale.example.com", "brief": "Old draft"},
            ),
            201,
        )
        fresh = await _json(
            await client.post(
                "/api/drafts",
                json={"url": "https://fresh.example.com", "brief": "Fresh draft"},
            ),
            201,
        )
        start = await client.get(
            f"/api/auth/google/start?draft_id={fresh['id']}", allow_redirects=False
        )
        if start.status != 302:
            raise AssertionError(f"OAuth start returned {start.status}")
        state = parse_qs(urlsplit(start.headers["Location"]).query)["state"][0]
        callback = await client.get(
            f"/api/auth/google/callback?state={state}&code=acceptance-code",
            allow_redirects=False,
        )
        if callback.status != 302:
            raise AssertionError(f"OAuth callback returned {callback.status}")
        claimed_project_id = parse_qs(urlsplit(callback.headers["Location"]).query)[
            "project"
        ][0]
        auth = await _json(await client.get("/api/auth/session"), 200)
        csrf = auth.get("csrf_token")
        if not auth.get("authenticated") or not isinstance(csrf, str):
            raise AssertionError("OAuth session was not persisted")

        async with factory() as session:
            stale_row = await session.get(AnonymousDraft, UUID(stale["id"]))
            fresh_row = await session.get(AnonymousDraft, UUID(fresh["id"]))
            claimed_project = await session.get(Project, UUID(claimed_project_id))
            if stale_row is None or fresh_row is None or claimed_project is None:
                raise AssertionError("OAuth draft rows were not persisted")
            oauth_evidence = {
                "claimed_fresh_draft": (
                    fresh_row.claimed_at is not None
                    and claimed_project.source_url == "https://fresh.example.com/"
                ),
                "stale_draft_unclaimed": stale_row.claimed_at is None,
            }
            if not all(oauth_evidence.values()):
                raise AssertionError(
                    "OAuth draft claim evidence did not satisfy the canonical contract"
                )

        project_body = {
            "url": "https://repeat.example.com/services",
            "brief": "Build a concise service assistant",
        }
        first_project = await _json(
            await client.post(
                "/api/projects",
                json=project_body,
                headers={"X-CSRF-Token": csrf},
            ),
            201,
        )
        second_project = await _json(
            await client.post(
                "/api/projects",
                json=project_body,
                headers={"X-CSRF-Token": csrf},
            ),
            201,
        )

        async def enqueue(project_id: str, key: str):
            response = await client.post(
                f"/api/projects/{project_id}/runs",
                json={"mode": "express"},
                headers={"Idempotency-Key": key, "X-CSRF-Token": csrf},
            )
            return response.status, await response.json()

        run_results = await asyncio.gather(
            enqueue(first_project["id"], "acceptance-run-one"),
            enqueue(second_project["id"], "acceptance-run-two"),
        )
        accepted = [payload for status, payload in run_results if status == 202]
        rejected = [payload for status, payload in run_results if status == 409]
        if len(accepted) != 1 or len(rejected) != 1:
            raise AssertionError(
                f"trial race produced unexpected results: {run_results!r}"
            )
        run_id = accepted[0]["id"]
        winner_project_id = accepted[0]["project_id"]

        queue = PostgresWorkerQueue(factory, lease_seconds=30)
        stage_revisions = {
            "art_direction": 1,
            "foundation": 2,
            "identity": 3,
            "conversation": 4,
            "motion_polish": 5,
        }

        async def stage_handler(claim):
            if claim.next_stage == "reference_analysis":
                return StageResult(public_message="Reference analyzed")
            if claim.next_stage == "composition":
                return StageResult(
                    public_message="Composition planned",
                    context={
                        "composition_plan": {
                            "schema_version": 1,
                            "direction_id": "acceptance-direction",
                            "selections": [
                                {
                                    "slot": "launcher",
                                    "pattern_id": "orb-pulse",
                                    "version": 1,
                                    "parameters": {},
                                    "reason": "Verified launcher for acceptance",
                                },
                                {
                                    "slot": "shell",
                                    "pattern_id": "compact-chat",
                                    "version": 1,
                                    "parameters": {},
                                    "reason": "Verified shell for acceptance",
                                },
                                {
                                    "slot": "messages",
                                    "pattern_id": "paired-bubbles",
                                    "version": 1,
                                    "parameters": {},
                                    "reason": "Verified messages for acceptance",
                                },
                                {
                                    "slot": "composer",
                                    "pattern_id": "single-line-pill",
                                    "version": 1,
                                    "parameters": {},
                                    "reason": "Verified composer for acceptance",
                                },
                                {
                                    "slot": "motion",
                                    "pattern_id": "spring-reveal",
                                    "version": 1,
                                    "parameters": {},
                                    "reason": "Verified motion for acceptance",
                                },
                            ],
                            "summary": "Durable verified acceptance composition",
                            "custom_escape": None,
                        }
                    },
                )
            revision = stage_revisions[claim.next_stage]
            return StageResult(
                public_message=f"Completed {claim.next_stage}",
                artifact=_artifact(revision, Stage(claim.next_stage)),
            )

        worker = BuilderWorker(
            queue=queue,
            worker_id="acceptance-worker",
            stage_handler=stage_handler,
            heartbeat_interval=1,
            terminal_hook=TrialSettlementReconciler(factory).settle_run,
        )
        if not await worker.run_once():
            raise AssertionError("durable worker did not claim the queued run")

        refreshed_auth = await _json(await client.get("/api/auth/session"), 200)
        if refreshed_auth.get("authenticated") is not True:
            raise AssertionError("database session did not survive refresh")
        run = await _json(await client.get(f"/api/runs/{run_id}"), 200)
        preview = await _json(await client.get(f"/api/runs/{run_id}/preview"), 200)
        preview_revision = preview["artifact"]["revision"]
        preview_document = await client.get(
            f"/api/runs/{run_id}/preview/document"
            f"?revision={preview_revision}&channel=acceptance_channel_1234567890"
        )
        if (
            preview_document.status != 200
            or "data-kaigo-runtime-input" not in await preview_document.text()
        ):
            raise AssertionError("trusted preview document was not served")
        sse = await client.get(
            f"/api/runs/{run_id}/events", headers={"Last-Event-ID": "0"}
        )
        if sse.status != 200:
            raise AssertionError(f"SSE returned {sse.status}")
        sse_text = await sse.text()
        sse_terminal = "event: run.completed" in sse_text

        checkout_headers = {
            "Idempotency-Key": "acceptance-checkout",
            "X-CSRF-Token": csrf,
        }
        checkout = await _json(
            await client.post(
                "/api/billing/checkout",
                json={"plan_code": "starter_monthly"},
                headers=checkout_headers,
            ),
            201,
        )
        checkout_replay = await _json(
            await client.post(
                "/api/billing/checkout",
                json={"plan_code": "starter_monthly"},
                headers=checkout_headers,
            ),
            200,
        )
        provider_payment_id = f"pay-{checkout['payment']['id']}"
        webhook = await _json(
            await client.post(
                "/api/billing/webhooks/yookassa",
                json={"payment_id": provider_payment_id},
            ),
            200,
        )
        webhook_replay = await _json(
            await client.post(
                "/api/billing/webhooks/yookassa",
                json={"payment_id": provider_payment_id},
            ),
            200,
        )

        async with factory() as session:
            run_row = await session.get(GenerationRun, UUID(run_id))
            trial_settlement = run_row.trial_settlement
            artifacts = list(
                await session.scalars(
                    select(GenerationArtifact)
                    .where(GenerationArtifact.run_id == run_row.id)
                    .order_by(GenerationArtifact.revision)
                )
            )
            first_artifact = artifacts[-1]
            subscription_count = await session.scalar(
                select(func.count()).select_from(Subscription)
            )
            credit_count = await session.scalar(
                select(func.count())
                .select_from(UsageLedger)
                .where(UsageLedger.bucket == "generation_tokens")
            )
            webhook_count = await session.scalar(
                select(func.count()).select_from(PaymentWebhookEvent)
            )
            if webhook_count != 1:
                raise AssertionError("webhook replay created duplicate evidence")

        first_publish = await _json(
            await client.post(
                f"/api/projects/{winner_project_id}/publish",
                json={
                    "artifact_id": str(first_artifact.id),
                    "allowed_domains": ["https://example.com"],
                },
                headers={"X-CSRF-Token": csrf},
            ),
            201,
        )

        second_candidate = _artifact(first_artifact.revision + 1, Stage.MOTION_POLISH)
        async with factory() as session, session.begin():
            second_artifact = GenerationArtifact(
                run_id=first_artifact.run_id,
                revision=second_candidate.revision,
                stage=second_candidate.stage.value,
                html=second_candidate.body_html,
                css=second_candidate.css,
                javascript=second_candidate.javascript,
                config={"artifact": second_candidate.to_dict()},
                quality_status="verified",
                provenance={"source": "acceptance-smoke"},
            )
            session.add(second_artifact)
            await session.flush()
            second_artifact_id = second_artifact.id

        second_publish = await _json(
            await client.post(
                f"/api/projects/{winner_project_id}/publish",
                json={
                    "artifact_id": str(second_artifact_id),
                    "allowed_domains": ["https://example.com"],
                },
                headers={"X-CSRF-Token": csrf},
            ),
            201,
        )
        embed = await client.get(f"/embed/{first_publish['stable_key']}.js")
        embed_served = embed.status == 200 and "iframe" in (await embed.text())
        runtime = await client.get(
            f"/runtime/{first_publish['stable_key']}",
            headers={"Referer": "https://example.com/acceptance"},
        )
        runtime_served = (
            runtime.status == 200
            and 'data-kaigo-runtime="kaigo-widget"' in (await runtime.text())
        )
        rollback = await _json(
            await client.post(
                f"/api/publications/{first_publish['publication_id']}/rollback",
                json={"target_release_id": first_publish["release_id"]},
                headers={"X-CSRF-Token": csrf},
            ),
            200,
        )
        async with factory() as session:
            publication = await session.get(
                Publication, UUID(first_publish["publication_id"])
            )
            active_release_id = str(publication.active_release_id)

        return {
            "ok": True,
            "oauth": oauth_evidence,
            "projects": {
                "repeat_creates_distinct_ids": first_project["id"]
                != second_project["id"],
            },
            "trial": {
                "accepted_requests": len(accepted),
                "rejected_requests": len(rejected),
                "settlement": trial_settlement,
            },
            "generation": {
                "state": run["status"],
                "preview_revision": preview_revision,
                "sse_terminal": sse_terminal,
            },
            "billing": {
                "provider_checkout_calls": len(payment_provider.checkout_calls),
                "checkout_replayed": (
                    checkout_replay["payment"]["id"] == checkout["payment"]["id"]
                ),
                "webhook_first_processed": webhook["processed"],
                "webhook_replay_processed": webhook_replay["processed"],
                "subscriptions": subscription_count,
                "generation_token_credits": credit_count,
            },
            "publication": {
                "stable_key_preserved": (
                    first_publish["stable_key"]
                    == second_publish["stable_key"]
                    == rollback["stable_key"]
                ),
                "embed_served": embed_served,
                "runtime_served": runtime_served,
                "rollback_restored_first_release": (
                    rollback["release_id"] == first_publish["release_id"]
                    and active_release_id == first_publish["release_id"]
                ),
            },
        }
    finally:
        await client.close()
        await billing.close()
        await engine.dispose()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the deterministic Kaigo SaaS acceptance journey or a public "
            "deployed-stack canary."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "--base-url",
        help=(
            "Deployed Kaigo base URL. Enables public canary mode instead of the "
            "self-contained SQLite journey."
        ),
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/smoke-saas-foundation.db"),
        help="Disposable SQLite database path.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=20,
        help="Total timeout for each deployed-stack request (default: 20).",
    )
    parser.add_argument(
        "--cookie-file",
        type=Path,
        help=(
            "Root-only file containing the authenticated browser Cookie header "
            "(or KAIGO_SMOKE_COOKIE_FILE)."
        ),
    )
    parser.add_argument(
        "--readiness-token-file",
        type=Path,
        help=(
            "Root-only file containing the private readiness token "
            "(or KAIGO_SMOKE_READINESS_TOKEN_FILE)."
        ),
    )
    parser.add_argument(
        "--project-id",
        help="Owner project UUID (or KAIGO_SMOKE_PROJECT_ID).",
    )
    parser.add_argument(
        "--public-key",
        help="Published stable key for embed/runtime checks (or KAIGO_SMOKE_PUBLIC_KEY).",
    )
    parser.add_argument(
        "--embed-origin",
        help=(
            "Approved customer origin sent as Referer for runtime checks "
            "(or KAIGO_SMOKE_EMBED_ORIGIN)."
        ),
    )
    parser.add_argument(
        "--webhook-fixture",
        type=Path,
        help=(
            "Path to an explicit YooKassa sandbox webhook JSON fixture, replayed "
            "twice (or KAIGO_SMOKE_WEBHOOK_FIXTURE)."
        ),
    )
    parser.add_argument(
        "--require-release-ready",
        action="store_true",
        help="Fail unless owner, publication, and webhook replay checks all pass.",
    )
    return parser.parse_args()


def _read_cookie_file(path: Path) -> str:
    try:
        stat = path.stat()
        if not path.is_file():
            raise OSError("path is not a regular file")
        if os.name == "posix":
            if stat.st_uid != os.geteuid():
                raise OSError("file is not owned by the current user")
            if stat.st_mode & 0o077:
                raise OSError("file must not be accessible by group or others")
        cookie = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise DeployedSmokeFailure(
            "configuration", f"cookie file is not secure/readable: {error}"
        ) from error
    if not cookie:
        raise DeployedSmokeFailure("configuration", "cookie file is empty")
    return cookie


def _read_readiness_token_file(path: Path) -> str:
    try:
        stat = path.stat()
        if not path.is_file():
            raise OSError("path is not a regular file")
        if os.name == "posix":
            if stat.st_uid != os.geteuid():
                raise OSError("file is not owned by the current user")
            if stat.st_mode & 0o077:
                raise OSError("file must not be accessible by group or others")
        token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise DeployedSmokeFailure(
            "configuration", f"readiness token file is not secure/readable: {error}"
        ) from error
    if not token:
        raise DeployedSmokeFailure("configuration", "readiness token file is empty")
    return token


def main() -> int:
    arguments = _arguments()
    try:
        if arguments.base_url:
            webhook_fixture = arguments.webhook_fixture
            if webhook_fixture is None and os.environ.get(
                "KAIGO_SMOKE_WEBHOOK_FIXTURE"
            ):
                webhook_fixture = Path(os.environ["KAIGO_SMOKE_WEBHOOK_FIXTURE"])
            cookie_file = arguments.cookie_file
            if cookie_file is None and os.environ.get("KAIGO_SMOKE_COOKIE_FILE"):
                cookie_file = Path(os.environ["KAIGO_SMOKE_COOKIE_FILE"])
            cookie = (
                _read_cookie_file(cookie_file) if cookie_file is not None else None
            )
            readiness_token_file = arguments.readiness_token_file
            if readiness_token_file is None and os.environ.get(
                "KAIGO_SMOKE_READINESS_TOKEN_FILE"
            ):
                readiness_token_file = Path(
                    os.environ["KAIGO_SMOKE_READINESS_TOKEN_FILE"]
                )
            readiness_token = (
                _read_readiness_token_file(readiness_token_file)
                if readiness_token_file is not None
                else None
            )
            evidence = asyncio.run(
                run_deployed_smoke(
                    arguments.base_url,
                    cookie=cookie,
                    project_id=arguments.project_id
                    or os.environ.get("KAIGO_SMOKE_PROJECT_ID"),
                    public_key=arguments.public_key
                    or os.environ.get("KAIGO_SMOKE_PUBLIC_KEY"),
                    webhook_fixture=webhook_fixture,
                    embed_origin=arguments.embed_origin
                    or os.environ.get("KAIGO_SMOKE_EMBED_ORIGIN"),
                    timeout_seconds=arguments.timeout_seconds,
                    require_release_ready=arguments.require_release_ready,
                    readiness_token=readiness_token,
                )
            )
        else:
            evidence = asyncio.run(run_acceptance_journey(arguments.database))
    except DeployedSmokeFailure as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "mode": "deployed",
                    "base_url": arguments.base_url.rstrip("/"),
                    "failed_check": error.check,
                    "status": error.status,
                    "message": str(error),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    except Exception as error:  # noqa: BLE001
        print(
            json.dumps(
                {"ok": False, "error": type(error).__name__, "message": str(error)},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(evidence, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
