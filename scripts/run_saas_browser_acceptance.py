from __future__ import annotations

import argparse
import asyncio
import json
import secrets
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from html import escape
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aiohttp import web
from aiohttp_session import get_session, setup as setup_session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.oauth import OAuthIdentity
from app.auth.routes import OAUTH_PROVIDERS_KEY, setup_auth_routes
from app.auth.session_storage import DatabaseSessionStorage
from app.billing.payments import BillingService
from app.billing.routes import BILLING_SERVICE_KEY, setup_billing_routes
from app.billing.service import TrialSettlementReconciler
from app.db.base import Base
from app.db.session import SESSION_FACTORY_KEY
from app.projects.routes import setup_project_routes
from app.publication.routes import setup_publication_routes
from app.saas.models import (
    CompositionPlanItem,
    CompositionPlanRecord,
    GenerationArtifact,
    GenerationRun,
    Project,
    Publication,
    Subscription,
    TrialEntitlement,
    WidgetPatternVersion,
)
from builder_lab.models import Stage
from builder_lab.patterns.models import PatternCategory
from builder_lab.patterns.registry import load_builtin_registry
from builder_lab.worker import BuilderWorker, PostgresWorkerQueue, StageResult
from scripts.smoke_saas_foundation import _PaymentProvider, _artifact


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
ACCEPTANCE_PREFIX = "/__acceptance__"
_WORKER_TASK_KEY = web.AppKey("browser_acceptance_worker_task", asyncio.Task)


def require_loopback_host(host: str) -> str:
    """Reject DNS names and interfaces that could expose the harness."""

    try:
        address = ip_address(host)
    except ValueError as error:
        raise ValueError(
            "browser acceptance host must be a literal loopback address"
        ) from error
    if not address.is_loopback:
        raise ValueError("browser acceptance host must be a loopback address")
    return host


def _request_is_loopback(request: web.Request) -> bool:
    try:
        return ip_address(str(request.remote or "")).is_loopback
    except ValueError:
        return False


def _require_loopback_request(request: web.Request) -> None:
    if not _request_is_loopback(request):
        raise web.HTTPForbidden(text="localhost only")


async def _local_health(request: web.Request) -> web.Response:
    _require_loopback_request(request)
    return web.json_response({"status": "ok", "mode": "localhost_browser_acceptance"})


async def _local_readiness(request: web.Request) -> web.Response:
    _require_loopback_request(request)
    try:
        factory = request.app[SESSION_FACTORY_KEY]
        async with factory() as database:
            await database.scalar(select(1))
    except Exception:  # noqa: BLE001
        return web.json_response(
            {
                "status": "not_ready",
                "database": {"status": "error"},
                "worker": {"status": "unknown"},
            },
            status=503,
        )
    worker_task = request.app.get(_WORKER_TASK_KEY)
    worker_ready = worker_task is not None and not worker_task.done()
    return web.json_response(
        {
            "status": "ready" if worker_ready else "not_ready",
            "database": {"status": "ok"},
            "worker": {"status": "ok" if worker_ready else "stopped"},
        },
        status=200 if worker_ready else 503,
    )


async def _local_ai_health(request: web.Request) -> web.Response:
    _require_loopback_request(request)
    return web.json_response(
        {
            "status": "disabled",
            "mode": "localhost_browser_acceptance",
            "network": False,
        }
    )


class _LocalGoogleOAuthProvider:
    def authorization_url(self, transaction) -> str:
        return f"{ACCEPTANCE_PREFIX}/oauth/google?{urlencode({'state': transaction.state})}"

    async def exchange(self, transaction, callback) -> OAuthIdentity:
        if callback.code != "acceptance-code" or transaction.state != callback.state:
            raise RuntimeError("unexpected local OAuth callback")
        return OAuthIdentity(
            provider="google",
            subject="browser-acceptance-user",
            email="browser-acceptance@localhost.invalid",
            email_verified=True,
            display_name="Browser Acceptance User",
            profile={"source": "localhost-browser-acceptance"},
        )


def _builtin_composition_plan() -> dict[str, object]:
    registry = load_builtin_registry()
    preferred_patterns = {
        PatternCategory.LAUNCHER: "orb-pulse",
        PatternCategory.SHELL: "compact-chat",
        PatternCategory.MESSAGES: "paired-bubbles",
        PatternCategory.COMPOSER: "single-line-pill",
        PatternCategory.MOTION: "spring-reveal",
    }
    selections = []
    for category in PatternCategory:
        pattern = registry.resolve(preferred_patterns[category], 1)
        if pattern.category is not category:
            raise RuntimeError("built-in acceptance pattern category drifted")
        selections.append(
            {
                "slot": category.value,
                "pattern_id": pattern.pattern_id,
                "version": pattern.version,
                "parameters": {},
                "reason": "Deterministic built-in pattern for localhost acceptance",
            }
        )
    return {
        "schema_version": 1,
        "direction_id": "localhost-acceptance-direction",
        "selections": selections,
        "summary": "Deterministic composition from the built-in registry",
        "custom_escape": None,
    }


def _stage_handler_factory():
    revisions = {
        Stage.ART_DIRECTION.value: 1,
        Stage.FOUNDATION.value: 2,
        Stage.IDENTITY.value: 3,
        Stage.CONVERSATION.value: 4,
        Stage.MOTION_POLISH.value: 5,
    }
    composition_plan = _builtin_composition_plan()

    async def stage_handler(claim) -> StageResult:
        if claim.next_stage == "reference_analysis":
            return StageResult(public_message="Reference analyzed locally")
        if claim.next_stage == "composition":
            return StageResult(
                public_message="Composition planned from the built-in registry",
                context={"composition_plan": composition_plan},
            )
        revision = revisions[claim.next_stage]
        return StageResult(
            public_message=f"Completed {claim.next_stage}",
            artifact=_artifact(revision, Stage(claim.next_stage)),
        )

    return stage_handler


async def _mock_google_consent(request: web.Request) -> web.Response:
    _require_loopback_request(request)
    state = request.query.get("state", "")
    if not state:
        raise web.HTTPBadRequest(text="missing OAuth state")
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Local OAuth consent</title></head>
<body><main>
  <h1>Local browser acceptance</h1>
  <p>This page never contacts Google and grants only the disposable local account.</p>
  <form action="/api/auth/google/callback" method="get">
    <input type="hidden" name="state" value="{escape(state, quote=True)}">
    <input type="hidden" name="code" value="acceptance-code">
    <button type="submit">Approve local Google sign-in</button>
  </form>
</main></body></html>"""
    return web.Response(text=html, content_type="text/html")


async def _activation_page(request: web.Request) -> web.Response:
    _require_loopback_request(request)
    session = await get_session(request)
    csrf = session.get("csrf_token")
    if not isinstance(session.get("user_id"), int) or not isinstance(csrf, str):
        raise web.HTTPUnauthorized(text="complete local OAuth first")
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Activate acceptance plan</title></head>
<body><main>
  <h1>Local acceptance subscription</h1>
  <p>This activates a disposable SQLite subscription. No payment provider is called.</p>
  <form action="{ACCEPTANCE_PREFIX}/subscription/activate" method="post">
    <input type="hidden" name="csrf" value="{escape(csrf, quote=True)}">
    <button type="submit">Activate local acceptance subscription</button>
  </form>
</main></body></html>"""
    return web.Response(text=html, content_type="text/html")


async def _activate_subscription(request: web.Request) -> web.StreamResponse:
    _require_loopback_request(request)
    session = await get_session(request)
    user_id = session.get("user_id")
    csrf = session.get("csrf_token")
    form = await request.post()
    supplied_csrf = form.get("csrf")
    if not isinstance(user_id, int):
        raise web.HTTPUnauthorized(text="complete local OAuth first")
    if (
        not isinstance(csrf, str)
        or not isinstance(supplied_csrf, str)
        or not secrets.compare_digest(csrf, supplied_csrf)
    ):
        raise web.HTTPForbidden(text="csrf failed")

    now = datetime.now(UTC)
    factory = request.app[SESSION_FACTORY_KEY]
    async with factory() as database, database.begin():
        subscription = await database.scalar(
            select(Subscription).where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
            )
        )
        if subscription is None:
            subscription = Subscription(
                user_id=user_id,
                provider="acceptance-local",
                plan_code="starter_monthly",
                plan_snapshot={"source": "localhost-browser-acceptance"},
                plan_fingerprint="localhost-browser-acceptance-v1",
                status="active",
            )
            database.add(subscription)
        subscription.current_period_start = now
        subscription.current_period_end = now + timedelta(days=30)
    raise web.HTTPFound("/studio#studio-publication")


async def _diagnostics(request: web.Request) -> web.Response:
    _require_loopback_request(request)
    session = await get_session(request)
    user_id = session.get("user_id")
    payload: dict[str, object] = {
        "authenticated": isinstance(user_id, int),
        "project_id": None,
        "run_id": None,
        "run_status": None,
        "run_trial_settlement": None,
        "artifact_id": None,
        "artifact_revision": None,
        "composition_patterns": [],
        "trial": None,
        "subscription_status": None,
        "publication_id": None,
        "stable_key": None,
        "activation_url": f"{ACCEPTANCE_PREFIX}/subscription",
    }
    if not isinstance(user_id, int):
        return web.json_response(payload)

    factory = request.app[SESSION_FACTORY_KEY]
    async with factory() as database:
        project = await database.scalar(
            select(Project)
            .where(Project.owner_user_id == user_id)
            .order_by(Project.created_at.desc(), Project.id.desc())
            .limit(1)
        )
        subscription = await database.scalar(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status == "active",
                Subscription.current_period_end > datetime.now(UTC),
            )
            .order_by(Subscription.created_at.desc())
            .limit(1)
        )
        entitlement = await database.scalar(
            select(TrialEntitlement).where(TrialEntitlement.user_id == user_id)
        )
        if entitlement is not None:
            payload["trial"] = {
                "state": entitlement.state,
                "granted_units": entitlement.granted_units,
                "reserved_units": entitlement.reserved_units,
                "consumed_units": entitlement.consumed_units,
            }
        if subscription is not None:
            payload["subscription_status"] = subscription.status
        if project is None:
            return web.json_response(payload)
        payload["project_id"] = str(project.id)
        run = (
            await database.get(GenerationRun, project.active_run_id)
            if project.active_run_id is not None
            else None
        )
        if run is not None:
            payload["run_id"] = str(run.id)
            payload["run_status"] = run.state
            payload["run_trial_settlement"] = run.trial_settlement
            artifact = await database.scalar(
                select(GenerationArtifact)
                .where(GenerationArtifact.run_id == run.id)
                .order_by(GenerationArtifact.revision.desc())
                .limit(1)
            )
            if artifact is not None:
                payload["artifact_id"] = str(artifact.id)
                payload["artifact_revision"] = artifact.revision
            pattern_rows = (
                await database.execute(
                    select(
                        CompositionPlanItem.slot,
                        WidgetPatternVersion.pattern_id,
                        WidgetPatternVersion.version,
                    )
                    .join(
                        CompositionPlanRecord,
                        CompositionPlanRecord.id
                        == CompositionPlanItem.composition_plan_id,
                    )
                    .join(
                        WidgetPatternVersion,
                        WidgetPatternVersion.id
                        == CompositionPlanItem.pattern_version_id,
                    )
                    .where(CompositionPlanRecord.run_id == run.id)
                )
            ).all()
            by_slot = {
                slot: f"{pattern_id}@{version}"
                for slot, pattern_id, version in pattern_rows
            }
            payload["composition_patterns"] = [
                by_slot[category.value]
                for category in PatternCategory
                if category.value in by_slot
            ]
        publication = await database.scalar(
            select(Publication).where(Publication.project_id == project.id).limit(1)
        )
        if publication is not None:
            payload["publication_id"] = str(publication.id)
            payload["stable_key"] = publication.stable_key
    return web.json_response(payload)


async def _studio_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(request.app["acceptance_frontend_dist"] / "index.html")


async def _favicon(request: web.Request) -> web.FileResponse:
    return web.FileResponse(request.app["acceptance_frontend_dist"] / "favicon.svg")


async def create_browser_acceptance_app(
    *,
    database: str | Path,
    frontend_dist: str | Path,
    public_base_url: str,
) -> web.Application:
    database_path = Path(database).resolve()
    dist_path = Path(frontend_dist).resolve()
    if not (dist_path / "index.html").is_file() or not (dist_path / "assets").is_dir():
        raise FileNotFoundError("frontend/dist must contain index.html and assets")
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.unlink(missing_ok=True)

    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    payment_provider = _PaymentProvider()
    billing = BillingService(factory, payment_provider)
    config = SimpleNamespace(
        public_auth_enabled=True,
        public_base_url=public_base_url.rstrip("/"),
        environment="test",
        publication_allow_insecure_origins=True,
        publication_chat_signing_secret=secrets.token_urlsafe(32),
        publication_chat_capability_ttl_seconds=300,
        publication_chat_key_rate_limit_requests=120,
        publication_chat_ip_rate_limit_requests=60,
        publication_chat_trusted_proxy_cidrs=(),
        entry_trusted_proxy_cidrs=(),
        oauth_callback_concurrency=4,
        chat_rate_limit_window_seconds=60,
    )

    app = web.Application()
    app[SESSION_FACTORY_KEY] = factory
    app["config"] = config
    app["acceptance_frontend_dist"] = dist_path
    app[OAUTH_PROVIDERS_KEY] = {"google": _LocalGoogleOAuthProvider()}
    app[BILLING_SERVICE_KEY] = billing
    app["project_sse_poll_seconds"] = 0.02
    setup_session(
        app,
        DatabaseSessionStorage(
            cookie_name="kaigo_browser_acceptance",
            max_age=3600,
            secure=False,
            httponly=True,
            samesite="Lax",
        ),
    )
    setup_auth_routes(
        app,
        public_base_url=public_base_url,
        public_auth_enabled=True,
    )
    setup_project_routes(app)
    setup_billing_routes(app)
    setup_publication_routes(app)

    app.router.add_get("/api/health", _local_health)
    app.router.add_get("/api/ready", _local_readiness)
    app.router.add_get("/api/health/ai", _local_ai_health)
    app.router.add_get(f"{ACCEPTANCE_PREFIX}/oauth/google", _mock_google_consent)
    app.router.add_get(f"{ACCEPTANCE_PREFIX}/subscription", _activation_page)
    app.router.add_post(
        f"{ACCEPTANCE_PREFIX}/subscription/activate", _activate_subscription
    )
    app.router.add_get(f"{ACCEPTANCE_PREFIX}/diagnostics", _diagnostics)
    app.router.add_get("/", _studio_index)
    app.router.add_get("/studio", _studio_index)
    app.router.add_get("/studio/", _studio_index)
    app.router.add_get("/favicon.svg", _favicon)
    app.router.add_static("/assets/", dist_path / "assets", follow_symlinks=False)

    queue = PostgresWorkerQueue(factory, lease_seconds=30)
    worker = BuilderWorker(
        queue=queue,
        worker_id="localhost-browser-acceptance-worker",
        stage_handler=_stage_handler_factory(),
        heartbeat_interval=1,
        idle_poll_interval=0.05,
        terminal_hook=TrialSettlementReconciler(factory).settle_run,
    )
    worker_task: asyncio.Task[None] | None = None

    async def start_worker(_app: web.Application) -> None:
        nonlocal worker_task
        worker_task = asyncio.create_task(
            worker.run_forever(), name="localhost-browser-acceptance-worker"
        )
        _app[_WORKER_TASK_KEY] = worker_task

    async def cleanup(_app: web.Application) -> None:
        worker.stop()
        if worker_task is not None:
            await worker_task
        await billing.close()
        await engine.dispose()

    app.on_startup.append(start_worker)
    app.on_cleanup.append(cleanup)
    return app


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the localhost-only Kaigo Studio browser acceptance harness.",
        allow_abbrev=False,
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--frontend-dist",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "frontend" / "dist",
    )
    return parser.parse_args()


async def _serve(host: str, port: int, frontend_dist: Path, database: Path) -> None:
    require_loopback_host(host)
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    base_url = f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
    app = await create_browser_acceptance_app(
        database=database,
        frontend_dist=frontend_dist,
        public_base_url=base_url,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, host, port).start()
        print(
            json.dumps(
                {
                    "base_url": base_url,
                    "studio_url": f"{base_url}/studio",
                    "diagnostics_url": f"{base_url}{ACCEPTANCE_PREFIX}/diagnostics",
                    "database": str(database),
                }
            ),
            flush=True,
        )
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def main() -> int:
    arguments = _arguments()
    try:
        require_loopback_host(arguments.host)
        with tempfile.TemporaryDirectory(
            prefix="kaigo-browser-acceptance-"
        ) as temporary:
            asyncio.run(
                _serve(
                    arguments.host,
                    arguments.port,
                    arguments.frontend_dist,
                    Path(temporary) / "acceptance.db",
                )
            )
    except KeyboardInterrupt:
        return 0
    except (FileNotFoundError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
