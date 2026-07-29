import logging
import hmac
from datetime import UTC, datetime, timedelta

from aiohttp import web

from app.db.session import get_session_factory
from app.saas.models import WorkerServiceLease
from core import ai_service


logger = logging.getLogger(__name__)
WORKER_ACTIVITY_MAX_AGE = timedelta(minutes=5)


async def healthcheck(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def readinesscheck(request: web.Request) -> web.Response:
    config = request.app.get("config")
    readiness_token = getattr(config, "readiness_token", None)
    supplied_token = request.headers.get("X-Kaigo-Readiness-Token", "")
    if readiness_token and not hmac.compare_digest(readiness_token, supplied_token):
        # Hide the operational endpoint rather than exposing an authentication
        # oracle. This branch never opens a database session.
        raise web.HTTPNotFound()

    now = datetime.now(UTC)
    try:
        session_factory = get_session_factory(request.app)
        async with session_factory() as database:
            lease = await database.get(WorkerServiceLease, "builder")
    except Exception:  # noqa: BLE001
        logger.exception("Database readiness check failed")
        return web.json_response(
            {
                "status": "not_ready",
                "database": {"status": "error"},
                "worker": {"status": "unknown"},
            },
            status=503,
        )

    latest_heartbeat = lease.heartbeat_at if lease is not None else None
    if latest_heartbeat is not None and latest_heartbeat.tzinfo is None:
        latest_heartbeat = latest_heartbeat.replace(tzinfo=UTC)
    heartbeat_is_recent = (
        latest_heartbeat is not None
        and latest_heartbeat >= now - WORKER_ACTIVITY_MAX_AGE
    )
    expected_identity = (
        getattr(config, "expected_worker_boot_id", None),
        getattr(config, "expected_worker_deployment_id", None),
        getattr(config, "expected_worker_image_identity", None),
    )
    actual_identity = (
        lease.boot_id if lease is not None else None,
        lease.deployment_id if lease is not None else None,
        lease.image_identity if lease is not None else None,
    )
    identity_matches = all(
        expected is None or expected == actual
        for expected, actual in zip(expected_identity, actual_identity, strict=True)
    )
    if lease is None:
        worker_status = "not_started"
    elif not identity_matches:
        worker_status = "identity_mismatch"
    elif not heartbeat_is_recent:
        worker_status = "no_recent_activity"
    else:
        worker_status = "ok"
    ready = worker_status == "ok"
    return web.json_response(
        {
            "status": "ready" if ready else "not_ready",
            "database": {"status": "ok"},
            "worker": {
                "status": worker_status,
                "heartbeat_at": (
                    latest_heartbeat.isoformat()
                    if latest_heartbeat is not None
                    else None
                ),
                "max_age_seconds": int(WORKER_ACTIVITY_MAX_AGE.total_seconds()),
            },
        },
        status=200 if ready else 503,
    )


async def ai_healthcheck(request: web.Request) -> web.Response:
    model = request.query.get("model") or None
    status = await ai_service.check_provider(model=model)
    status_code = (
        200 if status.get("status") == "ok" else int(status.get("status_code", 502))
    )
    return web.json_response(status, status=status_code)


def setup_api_routes(app: web.Application) -> None:
    app.router.add_get("/api/health", healthcheck)
    app.router.add_get("/api/ready", readinesscheck)
    app.router.add_get("/api/health/ai", ai_healthcheck)
