import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG = ROOT / "deploy" / "nginx" / "kaigo-marketing-site.conf"
VITE_CONFIG = ROOT / "frontend" / "vite.config.ts"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_marketing_site.sh"
DIST = ROOT / "frontend" / "dist"
NGINX_TEST_CONFIG = ROOT / "tests" / "deployment_cases" / "nginx-marketing-test.conf"
NGINX_TEST_PASSWORD = ROOT / "tests" / "deployment_cases" / "nginx-test.htpasswd"
DOCKER = shutil.which("docker")
BASH = next(
    (
        candidate
        for candidate in (
            r"C:\Program Files\Git\bin\bash.exe",
            shutil.which("bash"),
        )
        if candidate
        and Path(candidate).is_file()
        and "System32" not in candidate
        and "WindowsApps" not in candidate
    ),
    None,
)


def _wait_for_docker_port(
    docker,
    container,
    *,
    timeout=5.0,
    runner=subprocess.run,
    sleeper=time.sleep,
):
    deadline = time.monotonic() + timeout
    last_result = None
    while time.monotonic() < deadline:
        last_result = runner(
            [docker, "port", container, "8088/tcp"],
            capture_output=True,
            text=True,
            check=False,
        )
        if last_result.returncode == 0:
            mapping = last_result.stdout.strip()
            if mapping and ":" in mapping:
                candidate = mapping.splitlines()[0].rsplit(":", 1)[-1]
                if candidate.isdigit():
                    return int(candidate)
        sleeper(0.05)
    detail = ""
    if last_result is not None:
        detail = (last_result.stderr or last_result.stdout).strip()
    raise AssertionError(
        f"Docker did not register the nginx port within {timeout:.1f}s: {detail}"
    )


class MarketingSitePackageTests(unittest.TestCase):
    def test_frontend_metadata_uses_truthful_timing(self):
        index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

        self.assertIn("10–20 минут", index)
        self.assertIn(
            "<title>Kaigo — AI для вашего бизнеса за 10 минут</title>",
            index,
        )

    def test_frontend_build_is_a_self_contained_hashed_static_package(self):
        index = (DIST / "index.html").read_text(encoding="utf-8")
        favicon = DIST / "favicon.svg"
        references = re.findall(
            r"""(?:src|href)=["'](/assets/[^"']+\.(?:js|css))["']""",
            index,
        )

        self.assertTrue(favicon.is_file(), "built package must include favicon.svg")
        self.assertIn(
            '<link rel="icon" type="image/svg+xml" href="/favicon.svg" />',
            index,
        )
        self.assertTrue(references, "index.html must reference built JS/CSS assets")
        self.assertTrue(any(reference.endswith(".js") for reference in references))
        self.assertTrue(any(reference.endswith(".css") for reference in references))
        for reference in references:
            self.assertRegex(
                reference,
                r"^/assets/.+-[A-Za-z0-9_-]{8}\.(?:js|css)$",
            )
            self.assertTrue((DIST / reference.removeprefix("/")).is_file())
        self.assertNotRegex(index, r"https?://")

    def test_docker_port_lookup_retries_until_mapping_is_registered(self):
        results = iter(
            (
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess([], 0, stdout="", stderr=""),
                subprocess.CompletedProcess(
                    [], 0, stdout="127.0.0.1:49152\n", stderr=""
                ),
            )
        )

        port = _wait_for_docker_port(
            "docker",
            "container",
            runner=lambda *args, **kwargs: next(results),
            sleeper=lambda _: None,
        )

        self.assertEqual(port, 49152)

    def test_nginx_contract_keeps_marketing_routes_narrow_and_old_app_as_fallback(self):
        config = NGINX_CONFIG.read_text(encoding="utf-8")

        self.assertEqual(config.count("location = / {"), 1)
        self.assertEqual(config.count("location = /studio {"), 1)
        self.assertEqual(config.count("location = /studio/ {"), 1)
        self.assertNotRegex(config, r"location\s+(?:\^~\s+)?/studio/")
        self.assertIn("root /var/www/kaigo-marketing/current;", config)
        self.assertGreaterEqual(config.count("try_files /index.html =404;"), 3)

        self.assertIn(
            'location ~* "^/assets/[^/]+-[A-Za-z0-9_-]{8}\\.',
            config,
        )
        self.assertIn('Cache-Control "public, max-age=31536000, immutable"', config)
        stable_assets = config.split("location /assets/ {", 1)[1].split("}", 1)[0]
        self.assertIn('Cache-Control "no-cache"', stable_assets)
        self.assertIn("try_files $uri =404;", config)

        favicon = config.split("location = /favicon.svg {", 1)[1].split("}", 1)[0]
        self.assertIn("root /var/www/kaigo-marketing/current;", favicon)
        self.assertIn("try_files /favicon.svg =404;", favicon)
        self.assertIn('Cache-Control "no-cache"', favicon)
        self.assertIn('X-Content-Type-Options "nosniff"', favicon)

        fallback = config.split("location / {", 1)[1]
        self.assertIn("proxy_pass http://127.0.0.1:8080;", fallback)
        self.assertIn("proxy_set_header Host $host;", fallback)

    def test_nginx_contract_owns_each_legal_route_exactly_with_static_spa_headers(self):
        config = NGINX_CONFIG.read_text(encoding="utf-8")
        legal_paths = (
            "/privacy",
            "/privacy/",
            "/personal-data-consent",
            "/personal-data-consent/",
            "/terms",
            "/terms/",
            "/offer",
            "/offer/",
        )

        for path in legal_paths:
            marker = f"location = {path} {{"
            self.assertEqual(config.count(marker), 1, path)
            block = config.split(marker, 1)[1].split("}", 1)[0]
            self.assertIn("root /var/www/kaigo-marketing/current;", block, path)
            self.assertIn("try_files /index.html =404;", block, path)
            self.assertIn('Cache-Control "no-store";', block, path)
            self.assertIn("Content-Security-Policy", block, path)
            self.assertIn("Referrer-Policy", block, path)
            self.assertIn("X-Content-Type-Options", block, path)
            self.assertIn("X-Frame-Options", block, path)

        self.assertNotRegex(
            config,
            r"location\s+(?:\^~\s+)?/(?:privacy|personal-data-consent|terms|offer)/",
        )
        fallback = config.split("location / {", 1)[1]
        self.assertIn("proxy_pass http://127.0.0.1:8080;", fallback)

    def test_only_legacy_builder_uses_basic_auth(self):
        config = NGINX_CONFIG.read_text(encoding="utf-8")
        auth_file = "/etc/nginx/.htpasswd-kaigo-builder"

        for exact_location in ("/studio", "/studio/"):
            block = config.split(f"location = {exact_location} {{", 1)[1].split("}", 1)[0]
            self.assertNotIn("auth_basic", block)

        builder = config.split("location ^~ /builder/ {", 1)[1].split("}", 1)[0]
        self.assertIn('auth_basic "Kaigo Builder";', builder)
        self.assertIn(f"auth_basic_user_file {auth_file};", builder)
        self.assertIn("proxy_pass http://127.0.0.1:8091/;", builder)
        self.assertIn("proxy_http_version 1.1;", builder)
        self.assertIn("proxy_buffering off;", builder)
        self.assertIn("proxy_request_buffering off;", builder)
        self.assertIn("proxy_read_timeout 1800s;", builder)

    def test_vite_development_server_proxies_saas_api_to_the_application(self):
        config = VITE_CONFIG.read_text(encoding="utf-8")

        self.assertRegex(
            config,
            r"['\"]\/api['\"]\s*:\s*\{[^}]*"
            r"target:\s*['\"]http:\/\/127\.0\.0\.1:8080['\"]",
        )

    def test_yookassa_webhook_has_exact_unauthenticated_application_route(self):
        config = NGINX_CONFIG.read_text(encoding="utf-8")
        block = config.split(
            "location = /api/billing/webhooks/yookassa {", 1
        )[1].split("}", 1)[0]

        self.assertIn("proxy_pass http://127.0.0.1:8080;", block)
        self.assertIn("proxy_set_header Host $host;", block)
        self.assertIn("proxy_request_buffering on;", block)
        self.assertIn("client_max_body_size 64k;", block)
        self.assertNotIn("auth_basic", block)

    def test_static_html_has_a_restrictive_same_origin_csp(self):
        config = NGINX_CONFIG.read_text(encoding="utf-8")
        csp_lines = [
            line.strip()
            for line in config.splitlines()
            if line.strip().startswith("add_header Content-Security-Policy")
        ]

        self.assertEqual(len(csp_lines), 13)
        html_csp = [line for line in csp_lines if "frame-src 'self'" in line]
        asset_csp = [line for line in csp_lines if "default-src 'none'" in line]
        self.assertEqual(len(html_csp), 11)
        self.assertEqual(len(asset_csp), 2)
        for line in html_csp:
            self.assertIn("default-src 'self'", line)
            self.assertIn("object-src 'none'", line)
            self.assertIn("frame-src 'self'", line)
            self.assertIn("frame-ancestors 'self'", line)
            self.assertNotIn("script-src 'self' 'unsafe-inline'", line)
            self.assertNotIn("*", line)
        for line in asset_csp:
            self.assertIn("object-src 'none'", line)
            self.assertIn("frame-ancestors 'none'", line)
            self.assertNotIn("*", line)

    def test_deploy_script_has_atomic_switch_preflight_and_rollback_contract(self):
        script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

        main_switch = script.index(
            "# Validate the currently loaded nginx configuration"
        )
        preflight = script.index('"${nginx_bin}" -t', main_switch)
        release_move = script.index('mv -- "${staging_dir}" "${release_dir}"')
        link_switch = script.index('mv -Tf -- "${next_link}" "${current_link}"')
        second_validation = script.index('"${nginx_bin}" -t', preflight + 1)
        reload = script.index('"${systemctl_bin}" reload nginx', second_validation)

        self.assertLess(preflight, release_move)
        self.assertLess(release_move, link_switch)
        self.assertLess(link_switch, second_validation)
        self.assertLess(second_validation, reload)
        self.assertIn('previous_target="$(readlink -- "${current_link}")"', script)
        self.assertIn('mv -Tf -- "${rollback_link}" "${current_link}"', script)
        self.assertIn("deploy failed; restored previous release", script)
        self.assertIn("immutable release already exists", script)
        self.assertIn('flock -n "${deploy_lock_fd}"', script)
        self.assertIn(
            "'/assets/[^\"[:space:]]+-[A-Za-z0-9_-]{8}\\.(js|css)'",
            script,
        )
        self.assertNotIn("[A-Za-z0-9_-]{8,}", script)
        self.assertNotIn('rm -rf -- "${current_link}"', script)

    def test_contact_legal_handoff_names_the_backend_and_compliance_boundaries(self):
        handoff = (ROOT / "docs" / "CONTACT_AND_LEGAL_HANDOFF.md").read_text(encoding="utf-8")

        for required in (
            "POST /api/feedback",
            "topic",
            "name",
            "contact",
            "message",
            "page",
            "idempotency",
            "202",
            "receipt",
            "CSRF",
            "rate limit",
            "dead-letter",
            "support@kaigo.space",
            "152-ФЗ",
            "Статья 18.1",
            "https://ips.pravo.gov.ru/api/ips/legislation/document?baseid=None&hash=98490812b3409e2a8d78a11ca9010f434ea3d9250a11dbbdb78690cd5551bdd6",
            "https://publication.pravo.gov.ru/Document/View/0001201811270056",
            "https://government.ru/docs/all/98196/?page=4",
            "https://82.rkn.gov.ru/directions/pers/p15375/",
            "Первичные нормативные источники",
            "Ведомственные разъяснения",
            "accepted_at",
            "сервером по UTC-часам",
            "клиентское время неавторитетно",
            "официальном портале",
            "юрист",
        ):
            self.assertIn(required, handoff)

        # Consent evidence is created by the backend after validation. A browser
        # clock must never become the authoritative legal timestamp.
        self.assertNotRegex(handoff, r'"timestamp"\s*:')
        self.assertNotRegex(
            handoff,
            r"(?is)(?:клиент|client)[^\n.]{0,100}(?:\bauthoritative\b|\bавторитетное\b|\bавторитетным\b)",
        )
        for forbidden_phrase in (
            "authoritative client timestamp",
            "client timestamp is authoritative",
            "authoritative client time",
            "клиентское время является авторитетным",
        ):
            self.assertNotIn(forbidden_phrase, handoff.lower())
        self.assertNotIn("timestamp принимается только в проверяемом UTC-формате", handoff)

        # These are deliberately stale/secondary URLs and must not be presented
        # as the mandatory source list for legal review.
        for forbidden in (
            "https://www.nalog.gov.ru/rn28/news/activities_fts/12403644/",
            "https://zpp.rospotrebnadzor.ru/npa/federal/turist/192115",
            "https://www.consultant.ru/document/cons_doc_LAW_5142/1a77b2ec302d6a384a228dff59e53680ccffaaca/",
        ):
            self.assertNotIn(forbidden, handoff)

    @unittest.skipUnless(DOCKER, "requires Docker with a local nginx:alpine image")
    def test_nginx_fixture_serves_real_route_and_cache_contract(self):
        image = subprocess.run(
            [DOCKER, "image", "inspect", "nginx:alpine"],
            capture_output=True,
            text=True,
            check=False,
        )
        if image.returncode != 0:
            self.skipTest("local nginx:alpine image is not available")

        container = f"kaigo-nginx-contract-{uuid.uuid4().hex[:12]}"
        run = subprocess.run(
            [
                DOCKER,
                "run",
                "-d",
                "--rm",
                "--name",
                container,
                "--publish",
                "127.0.0.1::8088",
                "--mount",
                (
                    f"type=bind,source={NGINX_CONFIG},"
                    "target=/etc/nginx/kaigo-marketing-site.conf,readonly"
                ),
                "--mount",
                (
                    f"type=bind,source={NGINX_TEST_CONFIG},"
                    "target=/etc/nginx/nginx.conf,readonly"
                ),
                "--mount",
                (
                    f"type=bind,source={NGINX_TEST_PASSWORD},"
                    "target=/etc/nginx/.htpasswd-kaigo-builder,readonly"
                ),
                "--mount",
                (
                    f"type=bind,source={DIST},"
                    "target=/var/www/kaigo-marketing/current,readonly"
                ),
                "nginx:alpine",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(run.returncode, 0, run.stderr)

        try:
            port = _wait_for_docker_port(DOCKER, container)
            base_url = f"http://127.0.0.1:{port}"

            with self._open_with_retry(f"{base_url}/") as root_response:
                self.assertEqual(root_response.status, 200)
                self.assertEqual(root_response.headers["Cache-Control"], "no-store")

            for studio_path in ("/studio", "/studio/"):
                with urllib.request.urlopen(f"{base_url}{studio_path}", timeout=2) as accepted:
                    self.assertEqual(accepted.status, 200)

            for legal_path in (
                "/privacy",
                "/privacy/",
                "/personal-data-consent",
                "/personal-data-consent/",
                "/terms",
                "/terms/",
                "/offer",
                "/offer/",
            ):
                with urllib.request.urlopen(f"{base_url}{legal_path}", timeout=2) as accepted:
                    self.assertEqual(accepted.status, 200)
                    self.assertEqual(accepted.headers["Cache-Control"], "no-store")

            index = (DIST / "index.html").read_text(encoding="utf-8")
            hashed_asset = re.search(
                r"""(?:src|href)=["'](/assets/[^"']+\.(?:js|css))["']""",
                index,
            ).group(1)
            with urllib.request.urlopen(
                f"{base_url}{hashed_asset}",
                timeout=2,
            ) as asset_response:
                self.assertIn("immutable", asset_response.headers["Cache-Control"])

            with urllib.request.urlopen(
                f"{base_url}/assets/house-cutout.png",
                timeout=2,
            ) as stable_response:
                self.assertEqual(stable_response.headers["Cache-Control"], "no-cache")

            with urllib.request.urlopen(
                f"{base_url}/favicon.svg",
                timeout=2,
            ) as favicon_response:
                self.assertEqual(favicon_response.status, 200)
                self.assertEqual(favicon_response.headers["Cache-Control"], "no-cache")
                self.assertEqual(
                    favicon_response.headers["Content-Type"],
                    "image/svg+xml",
                )
        finally:
            subprocess.run(
                [DOCKER, "rm", "-f", container],
                capture_output=True,
                text=True,
                check=False,
            )

    @unittest.skipUnless(
        os.name == "posix" and BASH,
        "atomic symlink execution requires a POSIX host",
    )
    def test_deploy_switches_an_immutable_release_and_keeps_previous_release(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            dist = self._make_dist(sandbox)
            deploy_root = sandbox / "deploy-root"
            fake_bin, log = self._make_fake_commands(sandbox)

            first = self._run_deploy(
                fake_bin, log, deploy_root, dist, "release-one"
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_target = (deploy_root / "current").resolve()
            self.assertEqual(first_target, deploy_root / "releases" / "release-one")
            self.assertEqual(
                (first_target / "index.html").read_text(encoding="utf-8"),
                "<!doctype html><script src='/assets/index-12345678.js'></script>",
            )

            second = self._run_deploy(
                fake_bin, log, deploy_root, dist, "release-two"
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                (deploy_root / "current").resolve(),
                deploy_root / "releases" / "release-two",
            )
            self.assertTrue((deploy_root / "releases" / "release-one").is_dir())

            events = log.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                events,
                [
                    "nginx -t",
                    "nginx -t",
                    "systemctl reload nginx",
                    "nginx -t",
                    "nginx -t",
                    "systemctl reload nginx",
                ],
            )

    @unittest.skipUnless(
        os.name == "posix" and BASH,
        "atomic symlink execution requires a POSIX host",
    )
    def test_failed_reload_restores_the_previous_current_symlink(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            dist = self._make_dist(sandbox)
            deploy_root = sandbox / "deploy-root"
            fake_bin, log = self._make_fake_commands(sandbox)

            first = self._run_deploy(
                fake_bin, log, deploy_root, dist, "release-one"
            )
            self.assertEqual(first.returncode, 0, first.stderr)

            failure_marker = sandbox / "fail-reload-once"
            failure_marker.write_text("1", encoding="utf-8")
            failed = self._run_deploy(
                fake_bin,
                log,
                deploy_root,
                dist,
                "release-two",
                {"KAIGO_FAIL_RELOAD_ONCE": str(failure_marker)},
            )

            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(
                (deploy_root / "current").resolve(),
                deploy_root / "releases" / "release-one",
            )
            self.assertTrue((deploy_root / "releases" / "release-two").is_dir())
            self.assertFalse(any(deploy_root.glob(".current.*")))
            self.assertIn("restored previous release", failed.stderr)

    @unittest.skipUnless(
        os.name == "posix" and BASH,
        "first deploy rollback execution requires a POSIX host",
    )
    def test_failed_first_reload_keeps_the_only_complete_release_active(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            dist = self._make_dist(sandbox)
            deploy_root = sandbox / "deploy-root"
            fake_bin, log = self._make_fake_commands(sandbox)
            failure_marker = sandbox / "fail-reload-once"
            failure_marker.write_text("1", encoding="utf-8")

            failed = self._run_deploy(
                fake_bin,
                log,
                deploy_root,
                dist,
                "first-release",
                {"KAIGO_FAIL_RELOAD_ONCE": str(failure_marker)},
            )

            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(
                (deploy_root / "current").resolve(),
                deploy_root / "releases" / "first-release",
            )
            self.assertIn("no previous release; kept new current", failed.stderr)

    @unittest.skipUnless(
        os.name == "posix" and BASH,
        "concurrent deploy execution requires a POSIX host",
    )
    def test_concurrent_same_id_deploy_is_rejected_without_mutating_release(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            sandbox = Path(temporary_directory)
            dist = self._make_dist(sandbox)
            deploy_root = sandbox / "deploy-root"
            fake_bin, log = self._make_fake_commands(sandbox)
            env = self._deploy_env(fake_bin, log, deploy_root, dist)
            env["KAIGO_NGINX_DELAY_SECONDS"] = "0.5"
            command = [BASH, str(DEPLOY_SCRIPT), "same-release"]

            first = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            time.sleep(0.15)
            second = subprocess.run(
                command,
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            first_stdout, first_stderr = first.communicate(timeout=10)

            self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("another marketing deploy is already running", second.stderr)
            release = deploy_root / "releases" / "same-release"
            self.assertEqual(
                sorted(path.name for path in release.iterdir()),
                ["assets", "index.html"],
            )

    @staticmethod
    def _open_with_retry(url: str):
        last_error = None
        for _attempt in range(20):
            try:
                return urllib.request.urlopen(url, timeout=2)
            except urllib.error.URLError as error:
                last_error = error
                time.sleep(0.1)
        raise last_error

    @staticmethod
    def _make_dist(sandbox: Path) -> Path:
        dist = sandbox / "dist"
        assets = dist / "assets"
        assets.mkdir(parents=True)
        (dist / "index.html").write_text(
            "<!doctype html><script src='/assets/index-12345678.js'></script>",
            encoding="utf-8",
        )
        (assets / "index-12345678.js").write_text("console.log('ok')", encoding="utf-8")
        return dist

    @staticmethod
    def _make_fake_commands(sandbox: Path) -> tuple[Path, Path]:
        fake_bin = sandbox / "fake-bin"
        fake_bin.mkdir()
        log = sandbox / "commands.log"
        nginx = fake_bin / "nginx"
        systemctl = fake_bin / "systemctl"
        nginx.write_text(
            "#!/usr/bin/env bash\n"
            'printf "nginx %s\\n" "$*" >> "$KAIGO_COMMAND_LOG"\n'
            'if [[ -n "${KAIGO_NGINX_DELAY_SECONDS:-}" ]]; then\n'
            '  sleep "$KAIGO_NGINX_DELAY_SECONDS"\n'
            "fi\n",
            encoding="utf-8",
        )
        systemctl.write_text(
            textwrap.dedent(
                """\
                #!/usr/bin/env bash
                printf "systemctl %s\\n" "$*" >> "$KAIGO_COMMAND_LOG"
                if [[ -n "${KAIGO_FAIL_RELOAD_ONCE:-}" && -f "$KAIGO_FAIL_RELOAD_ONCE" ]]; then
                  rm -f -- "$KAIGO_FAIL_RELOAD_ONCE"
                  exit 1
                fi
                """
            ),
            encoding="utf-8",
        )
        os.chmod(nginx, 0o755)
        os.chmod(systemctl, 0o755)
        return fake_bin, log

    @staticmethod
    def _run_deploy(
        fake_bin: Path,
        log: Path,
        deploy_root: Path,
        dist: Path,
        release: str,
        extra_env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = MarketingSitePackageTests._deploy_env(
            fake_bin,
            log,
            deploy_root,
            dist,
        )
        if extra_env:
            env.update(extra_env)
        return subprocess.run(
            [BASH, DEPLOY_SCRIPT.as_posix(), release],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    @staticmethod
    def _deploy_env(
        fake_bin: Path,
        log: Path,
        deploy_root: Path,
        dist: Path,
    ) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
                "KAIGO_COMMAND_LOG": log.as_posix(),
                "KAIGO_MARKETING_DEPLOY_ROOT": deploy_root.as_posix(),
                "KAIGO_MARKETING_DIST_DIR": dist.as_posix(),
                "KAIGO_MARKETING_SKIP_BUILD": "1",
            }
        )
        return env


if __name__ == "__main__":
    unittest.main()
