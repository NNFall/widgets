from __future__ import annotations

import asyncio
import json
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit
from uuid import UUID

from aiohttp import ClientSession


REQUIRED_ENVIRONMENT = (
    "KAIGO_CANARY_APP_ORIGIN",
    "KAIGO_CANARY_ALLOWED_ORIGIN",
    "KAIGO_CANARY_DENIED_ORIGIN",
    "KAIGO_CANARY_PROJECT_ID",
    "KAIGO_CANARY_BASELINE_VERSION_ID",
    "KAIGO_CANARY_CANDIDATE_VERSION_ID",
    "KAIGO_CANARY_COOKIE_FILE",
    "KAIGO_CANARY_EVIDENCE_FILE",
)
_FORBIDDEN_EVIDENCE_FIELDS = {
    "authorization",
    "capability",
    "cookie",
    "csrf",
    "csrf_token",
    "html",
    "prompt",
    "raw_html",
    "response",
    "response_text",
    "text",
}


class CanaryBlocked(RuntimeError):
    """The external acceptance cannot prove its contract safely."""


@dataclass(frozen=True)
class CanaryConfig:
    app_origin: str
    allowed_origin: str
    denied_origin: str
    project_id: UUID
    baseline_version_id: UUID
    candidate_version_id: UUID
    cookie_file: Path
    evidence_file: Path


def parse_https_origin(value: str, *, field: str) -> str:
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(f"{field} must be an absolute HTTPS origin")
    port = (
        f":{parsed.port}"
        if parsed.port is not None and parsed.port != 443
        else ""
    )
    return f"https://{parsed.hostname.lower()}{port}"


def _uuid(value: str, *, field: str) -> UUID:
    try:
        return UUID(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be a UUID") from error


def load_config(environment: Mapping[str, str] | None = None) -> CanaryConfig:
    values = os.environ if environment is None else environment
    missing = [name for name in REQUIRED_ENVIRONMENT if not values.get(name, "").strip()]
    if missing:
        raise ValueError(f"missing required environment: {', '.join(missing)}")

    app_origin = parse_https_origin(
        values["KAIGO_CANARY_APP_ORIGIN"], field="KAIGO_CANARY_APP_ORIGIN"
    )
    allowed_origin = parse_https_origin(
        values["KAIGO_CANARY_ALLOWED_ORIGIN"],
        field="KAIGO_CANARY_ALLOWED_ORIGIN",
    )
    denied_origin = parse_https_origin(
        values["KAIGO_CANARY_DENIED_ORIGIN"],
        field="KAIGO_CANARY_DENIED_ORIGIN",
    )
    if len({app_origin, allowed_origin, denied_origin}) != 3:
        raise ValueError("app, allowed and denied origins must be distinct")

    baseline_version_id = _uuid(
        values["KAIGO_CANARY_BASELINE_VERSION_ID"],
        field="KAIGO_CANARY_BASELINE_VERSION_ID",
    )
    candidate_version_id = _uuid(
        values["KAIGO_CANARY_CANDIDATE_VERSION_ID"],
        field="KAIGO_CANARY_CANDIDATE_VERSION_ID",
    )
    if baseline_version_id == candidate_version_id:
        raise ValueError("baseline and candidate version IDs must be distinct")

    return CanaryConfig(
        app_origin=app_origin,
        allowed_origin=allowed_origin,
        denied_origin=denied_origin,
        project_id=_uuid(
            values["KAIGO_CANARY_PROJECT_ID"], field="KAIGO_CANARY_PROJECT_ID"
        ),
        baseline_version_id=baseline_version_id,
        candidate_version_id=candidate_version_id,
        # Keep the final path component unresolved so read_owner_cookie() can
        # reject symlinks instead of silently following one.
        cookie_file=Path(values["KAIGO_CANARY_COOKIE_FILE"]).expanduser().absolute(),
        evidence_file=Path(values["KAIGO_CANARY_EVIDENCE_FILE"]).expanduser().absolute(),
    )


def read_owner_cookie(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("cookie file must not be a symlink")
    try:
        metadata = path.stat()
    except OSError as error:
        raise ValueError("cookie file is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("cookie file must be regular")
    if os.name == "posix":
        if metadata.st_uid != os.getuid():
            raise ValueError("cookie file must be owned by the current user")
        if metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise ValueError("cookie file must be owner-only")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("cookie file is empty")
    if any(character in value for character in "\r\n"):
        raise ValueError("cookie file must contain one Cookie header value")
    return value


def _validate_evidence(value: Any, *, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for raw_key, nested in value.items():
            key = str(raw_key).strip().lower().replace("-", "_")
            if key in _FORBIDDEN_EVIDENCE_FIELDS:
                dotted = ".".join((*path, key))
                raise ValueError(f"forbidden evidence field: {dotted}")
            _validate_evidence(nested, path=(*path, key))
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            _validate_evidence(nested, path=(*path, str(index)))
    elif not isinstance(value, (str, int, float, bool, type(None))):
        raise ValueError("evidence must be JSON serializable")


def write_evidence(path: Path, payload: Mapping[str, Any]) -> None:
    _validate_evidence(payload)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(f"{serialized}\n", encoding="utf-8")
    os.replace(temporary, path)


async def owner_json_request(
    session: ClientSession,
    method: str,
    url: str,
    *,
    cookie_header: str,
    csrf_token: str | None = None,
    json_body: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "Cookie": cookie_header,
    }
    if csrf_token is not None:
        headers["X-CSRF-Token"] = csrf_token
    async with session.request(
        method,
        url,
        headers=headers,
        json=json_body,
        allow_redirects=False,
    ) as response:
        if 300 <= response.status < 400 or response.headers.get("Location"):
            raise CanaryBlocked("authenticated owner request attempted a redirect")
        try:
            payload = await response.json()
        except Exception as error:  # noqa: BLE001
            raise CanaryBlocked(
                f"owner API returned non-JSON status {response.status}"
            ) from error
        if not isinstance(payload, dict):
            raise CanaryBlocked("owner API returned an invalid JSON object")
        return response.status, payload


def _require_status(
    operation: str,
    result: tuple[int, dict[str, Any]],
    expected: set[int],
) -> dict[str, Any]:
    status, payload = result
    if status not in expected:
        error_code = None
        error = payload.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            error_code = error["code"][:80]
        suffix = f" ({error_code})" if error_code else ""
        raise CanaryBlocked(f"{operation} returned status {status}{suffix}")
    return payload


def _release_id(payload: Mapping[str, Any], *, operation: str) -> str:
    value = payload.get("release_id")
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError) as error:
        raise CanaryBlocked(f"{operation} returned an invalid release ID") from error


async def _load_release_marker(page, config: CanaryConfig, stable_key: str) -> str:
    await page.goto(
        f"{config.allowed_origin}/?{urlencode({'key': stable_key})}",
        wait_until="domcontentloaded",
    )
    public_state = await page.evaluate(
        """() => ({
          cookieEmpty: document.cookie === '',
          localEmpty: localStorage.length === 0,
          sessionEmpty: sessionStorage.length === 0
        })"""
    )
    if public_state != {
        "cookieEmpty": True,
        "localEmpty": True,
        "sessionEmpty": True,
    }:
        raise CanaryBlocked("allowed canary origin persisted browser state")

    outer = page.locator("iframe[data-kaigo-widget-key]")
    await outer.wait_for(state="attached", timeout=20_000)
    outer_frame = page.frame_locator("iframe[data-kaigo-widget-key]")
    marker_element = outer_frame.locator(
        "[data-kaigo-release][data-kaigo-release-id]"
    )
    await marker_element.wait_for(state="attached", timeout=20_000)
    marker_key = await marker_element.get_attribute(
        "data-kaigo-release", timeout=20_000
    )
    marker = await marker_element.get_attribute(
        "data-kaigo-release-id", timeout=20_000
    )
    if marker_key != stable_key or not marker:
        raise CanaryBlocked("runtime release marker is missing")
    return marker


async def _exercise_public_widget(page) -> None:
    outer_frame = page.frame_locator("iframe[data-kaigo-widget-key]")
    widget = outer_frame.frame_locator("#kaigo-generated-widget")
    launcher = widget.locator('[data-region="launcher"]')
    await launcher.click(timeout=20_000)
    close = widget.locator('[data-action="close"]')
    await close.click(timeout=10_000)
    await launcher.click(timeout=10_000)
    input_box = widget.locator('[data-kaigo-runtime-input="true"]')
    await input_box.fill("Какая услуга здесь основная?")
    await input_box.press("Enter")
    await widget.locator(
        '[data-kaigo-runtime-message="assistant"]'
    ).last.wait_for(timeout=30_000)


async def _browser_acceptance(
    config: CanaryConfig,
    *,
    stable_key: str,
    baseline_release_id: str,
    candidate_release_id: str,
    publish_candidate,
    rollback_baseline,
) -> tuple[dict[str, bool], dict[str, int]]:
    try:
        from playwright.async_api import async_playwright
    except ImportError as error:
        raise CanaryBlocked(
            "Playwright is unavailable; run from requirements.builder-lab.txt"
        ) from error

    leaked_credentials = False
    response_statuses: dict[str, int] = {}
    phase = "initial"
    runtime_responses: dict[str, tuple[int, str]] = {}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(service_workers="block")
        page = await context.new_page()

        def inspect_request(request) -> None:
            nonlocal leaked_credentials
            lowered = {key.lower() for key in request.headers}
            if "cookie" in lowered or "authorization" in lowered:
                leaked_credentials = True

        def inspect_response(response) -> None:
            if response.url.startswith(
                f"{config.app_origin}/runtime/{stable_key}"
            ) and not response.url.endswith("/chat"):
                runtime_responses[phase] = (
                    response.status,
                    response.headers.get("content-security-policy", ""),
                )

        page.on("request", inspect_request)
        page.on("response", inspect_response)
        try:
            phase = "baseline"
            first_marker = await _load_release_marker(page, config, stable_key)
            await _exercise_public_widget(page)
            if first_marker != baseline_release_id:
                raise CanaryBlocked("allowed origin did not load baseline release")

            candidate_status = await publish_candidate()
            response_statuses["publish_candidate"] = candidate_status
            phase = "candidate"
            second_marker = await _load_release_marker(page, config, stable_key)
            if second_marker != candidate_release_id:
                raise CanaryBlocked("allowed origin did not load candidate release")

            rollback_status = await rollback_baseline()
            response_statuses["rollback"] = rollback_status
            phase = "rollback"
            third_marker = await _load_release_marker(page, config, stable_key)
            if third_marker != baseline_release_id:
                raise CanaryBlocked("allowed origin did not restore baseline release")

            phase = "denied"
            await page.goto(
                f"{config.denied_origin}/?{urlencode({'key': stable_key})}",
                wait_until="domcontentloaded",
            )
            denied = page.locator("iframe[data-kaigo-widget-key]")
            denied_blocked = False
            try:
                await denied.wait_for(state="attached", timeout=5_000)
                denied_frame = page.frame_locator("iframe[data-kaigo-widget-key]")
                launcher_count = await denied_frame.frame_locator(
                    "#kaigo-generated-widget"
                ).locator('[data-region="launcher"]').count()
                denied_blocked = launcher_count == 0
            except Exception:  # noqa: BLE001
                denied_blocked = True
            if not denied_blocked:
                raise CanaryBlocked("denied origin rendered a usable launcher")
            denied_runtime = runtime_responses.get("denied")
            if denied_runtime is None or denied_runtime[0] != 200:
                raise CanaryBlocked("denied runtime response was not observed")
            denied_csp = denied_runtime[1]
            if (
                "frame-ancestors" not in denied_csp
                or config.allowed_origin not in denied_csp
                or config.denied_origin in denied_csp
            ):
                raise CanaryBlocked("runtime frame-ancestors policy is invalid")
            if leaked_credentials:
                raise CanaryBlocked("public browser request contained credentials")
            return (
                {
                    "allowed_loaded": True,
                    "launcher_roundtrip": True,
                    "chat_replied": True,
                    "candidate_loaded": True,
                    "rollback_loaded": True,
                    "denied_blocked": True,
                    "runtime_csp_exact": True,
                    "public_storage_empty": True,
                    "credential_headers_absent": True,
                },
                response_statuses,
            )
        finally:
            await context.close()
            await browser.close()


async def run(config: CanaryConfig) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    cookie = read_owner_cookie(config.cookie_file)
    statuses: dict[str, int] = {}

    async with ClientSession() as session:
        auth_result = await owner_json_request(
            session,
            "GET",
            f"{config.app_origin}/api/auth/session",
            cookie_header=cookie,
        )
        statuses["auth"] = auth_result[0]
        auth = _require_status("auth preflight", auth_result, {200})
        csrf_token = auth.get("csrf_token")
        if auth.get("authenticated") is not True or not isinstance(csrf_token, str):
            raise CanaryBlocked("dedicated canary owner is not authenticated")

        subscription_result = await owner_json_request(
            session,
            "GET",
            f"{config.app_origin}/api/billing/subscription",
            cookie_header=cookie,
        )
        statuses["subscription"] = subscription_result[0]
        subscription_payload = _require_status(
            "entitlement preflight", subscription_result, {200}
        )
        subscription = subscription_payload.get("subscription")
        if not isinstance(subscription, dict) or subscription.get("status") != "active":
            raise CanaryBlocked(
                "dedicated canary owner has no active test subscription"
            )

        versions_result = await owner_json_request(
            session,
            "GET",
            f"{config.app_origin}/api/projects/{config.project_id}/versions",
            cookie_header=cookie,
        )
        statuses["versions"] = versions_result[0]
        versions_payload = _require_status("version preflight", versions_result, {200})
        known_versions = {
            str(item.get("id"))
            for item in versions_payload.get("versions", [])
            if isinstance(item, dict)
        }
        required_versions = {
            str(config.baseline_version_id),
            str(config.candidate_version_id),
        }
        if not required_versions.issubset(known_versions):
            raise CanaryBlocked("canary project does not contain both reviewed versions")

        state_result = await owner_json_request(
            session,
            "GET",
            f"{config.app_origin}/api/projects/{config.project_id}/publication",
            cookie_header=cookie,
        )
        statuses["publication_state"] = state_result[0]
        state_payload = _require_status("publication preflight", state_result, {200})
        publication_state = state_payload.get("publication")
        expected_release_id = None
        if isinstance(publication_state, dict):
            active_release = publication_state.get("active_release")
            if not isinstance(active_release, dict):
                raise CanaryBlocked("publication preflight returned invalid active release")
            expected_release_id = _release_id(
                active_release, operation="publication preflight"
            )

        baseline_result = await owner_json_request(
            session,
            "POST",
            f"{config.app_origin}/api/projects/{config.project_id}/publish",
            cookie_header=cookie,
            csrf_token=csrf_token,
            json_body={
                "project_version_id": str(config.baseline_version_id),
                "expected_active_release_id": expected_release_id,
                "allowed_domains": [config.allowed_origin],
            },
        )
        statuses["publish_baseline"] = baseline_result[0]
        baseline = _require_status("baseline publish", baseline_result, {200, 201})
        baseline_release_id = _release_id(baseline, operation="baseline publish")
        stable_key = baseline.get("stable_key")
        if not isinstance(stable_key, str) or not 20 <= len(stable_key) <= 128:
            raise CanaryBlocked("baseline publish returned an invalid stable key")
        publication_id = baseline.get("publication_id")
        try:
            publication_id = str(UUID(str(publication_id)))
        except (TypeError, ValueError) as error:
            raise CanaryBlocked(
                "baseline publish returned an invalid publication ID"
            ) from error

        candidate_release_id: str | None = None

        async def publish_candidate() -> int:
            nonlocal candidate_release_id
            result = await owner_json_request(
                session,
                "POST",
                f"{config.app_origin}/api/projects/{config.project_id}/publish",
                cookie_header=cookie,
                csrf_token=csrf_token,
                json_body={
                    "project_version_id": str(config.candidate_version_id),
                    "expected_active_release_id": baseline_release_id,
                    "allowed_domains": [config.allowed_origin],
                },
            )
            payload = _require_status("candidate publish", result, {200, 201})
            candidate_release_id = _release_id(
                payload, operation="candidate publish"
            )
            return result[0]

        async def rollback_baseline() -> int:
            if candidate_release_id is None:
                raise CanaryBlocked("candidate release is unavailable for rollback")
            result = await owner_json_request(
                session,
                "POST",
                f"{config.app_origin}/api/publications/{publication_id}/rollback",
                cookie_header=cookie,
                csrf_token=csrf_token,
                json_body={
                    "target_release_id": baseline_release_id,
                    "expected_active_release_id": candidate_release_id,
                },
            )
            payload = _require_status("baseline rollback", result, {200})
            restored_release_id = _release_id(payload, operation="baseline rollback")
            if restored_release_id != baseline_release_id:
                raise CanaryBlocked("rollback did not restore the baseline release")
            return result[0]

        # Publish the candidate once before entering browser acceptance so its
        # public release marker can be supplied without leaking owner state.
        candidate_status = await publish_candidate()
        statuses["publish_candidate_prepared"] = candidate_status
        assert candidate_release_id is not None

        # Restore baseline before the public browser begins its baseline check.
        rollback_status = await rollback_baseline()
        statuses["rollback_prepared"] = rollback_status

        browser, browser_statuses = await _browser_acceptance(
            config,
            stable_key=stable_key,
            baseline_release_id=baseline_release_id,
            candidate_release_id=candidate_release_id,
            publish_candidate=publish_candidate,
            rollback_baseline=rollback_baseline,
        )
        statuses.update(browser_statuses)

    finished_at = datetime.now(UTC)
    return {
        "status": "passed",
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "origins": {
            "app": config.app_origin,
            "allowed": config.allowed_origin,
            "denied": config.denied_origin,
        },
        "preflight": {
            "authenticated": True,
            "entitled": True,
            "versions_reviewed": True,
        },
        "publication": {
            "publication_id": publication_id,
            "stable_key": stable_key,
            "baseline_version_id": str(config.baseline_version_id),
            "candidate_version_id": str(config.candidate_version_id),
            "baseline_release_id": baseline_release_id,
            "candidate_release_id": candidate_release_id,
            "rollback_release_id": baseline_release_id,
        },
        "browser": browser,
        "responses": statuses,
    }


async def _main_async() -> int:
    config: CanaryConfig | None = None
    try:
        config = load_config()
        evidence = await run(config)
        write_evidence(config.evidence_file, evidence)
        print(json.dumps({"status": "passed", "evidence": str(config.evidence_file)}))
        return 0
    except (CanaryBlocked, ValueError, OSError) as error:
        payload = {
            "status": "blocked",
            "reason": (
                "invalid_configuration"
                if config is None
                else "acceptance_not_proven"
            ),
            "detail": str(error)[:320],
        }
        if config is not None:
            try:
                write_evidence(
                    config.evidence_file,
                    {
                        "status": "blocked",
                        "finished_at": datetime.now(UTC).isoformat(),
                        "origins": {
                            "app": config.app_origin,
                            "allowed": config.allowed_origin,
                            "denied": config.denied_origin,
                        },
                        "reason": "acceptance_not_proven",
                    },
                )
            except (OSError, ValueError):
                pass
        print(json.dumps(payload, ensure_ascii=False))
        return 2
    except Exception as error:  # noqa: BLE001
        # Network/browser failures are still a blocked gate, never an implicit
        # skip or a traceback that could expose owner-session diagnostics.
        payload = {
            "status": "blocked",
            "reason": "acceptance_not_proven",
            "detail": type(error).__name__,
        }
        if config is not None:
            try:
                write_evidence(
                    config.evidence_file,
                    {
                        "status": "blocked",
                        "finished_at": datetime.now(UTC).isoformat(),
                        "origins": {
                            "app": config.app_origin,
                            "allowed": config.allowed_origin,
                            "denied": config.denied_origin,
                        },
                        "reason": "acceptance_not_proven",
                    },
                )
            except (OSError, ValueError):
                pass
        print(json.dumps(payload, ensure_ascii=False))
        return 2


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
