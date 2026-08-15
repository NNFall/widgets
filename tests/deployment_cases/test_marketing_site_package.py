import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from html.parser import HTMLParser
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
NGINX_CONFIG = ROOT / "deploy" / "nginx" / "kaigo-marketing-site.conf"
VITE_CONFIG = ROOT / "frontend" / "vite.config.ts"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_marketing_site.sh"
LEGACY_LANDING_SCRIPT = ROOT / "scripts" / "prepare_legacy_landing.sh"
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


class _LinkTagParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "link":
            return
        self.links.append(
            {name.lower(): (value or "") for name, value in attrs}
        )


def _parse_link_tags(document: str) -> list[dict[str, str]]:
    parser = _LinkTagParser()
    parser.feed(document)
    parser.close()
    return parser.links


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
    def test_legacy_landing_script_scopes_archived_assets_without_touching_source(self):
        script = LEGACY_LANDING_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("source_dir=", script)
        self.assertIn("target_dir=", script)
        self.assertIn("cp -a", script)
        self.assertIn("/landing-old/assets/", script)
        self.assertIn("/landing-old/favicon.svg", script)
        self.assertIn("target already exists", script)

    def test_frontend_metadata_uses_truthful_timing(self):
        index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

        self.assertIn("10–20 минут", index)
        self.assertIn(
            "<title>Kaigo — AI-консультант для вашего сайта</title>",
            index,
        )

    def test_frontend_build_is_a_self_contained_hashed_static_package(self):
        index = (DIST / "index.html").read_text(encoding="utf-8")
        versioned_favicons = sorted((DIST / "assets").glob("favicon-living-fold-*.png"))
        favicon_png = DIST / "favicon.png"
        favicon_ico = DIST / "favicon.ico"
        apple_touch_icon = DIST / "apple-touch-icon.png"
        built_css = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (DIST / "assets").glob("*.css")
        )
        references = re.findall(
            r"""(?:src|href)=["'](/assets/[^"']+\.(?:js|css))["']""",
            index,
        )

        self.assertEqual(
            len(versioned_favicons),
            1,
            "built package must include one versioned Living Fold favicon PNG",
        )
        versioned_favicon = versioned_favicons[0]
        self.assertRegex(
            versioned_favicon.name,
            r"^favicon-living-fold-[a-f0-9]{8}\.png$",
        )
        self.assertTrue(favicon_png.is_file(), "built package must include favicon.png")
        self.assertTrue(favicon_ico.is_file(), "built package must include favicon.ico")
        self.assertTrue(
            apple_touch_icon.is_file(),
            "built package must include apple-touch-icon.png",
        )
        with Image.open(versioned_favicon) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (128, 128))
        with Image.open(favicon_png) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (128, 128))
        self.assertEqual(
            favicon_png.read_bytes(),
            versioned_favicon.read_bytes(),
            "stable favicon.png must be byte-identical to the versioned favicon",
        )
        with Image.open(apple_touch_icon) as image:
            image.load()
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (180, 180))
        with Image.open(favicon_ico) as image:
            self.assertEqual(image.format, "ICO")
            self.assertEqual(
                set(image.ico.sizes()),
                {(16, 16), (32, 32), (48, 48)},
            )
        links_by_rel: dict[str, list[dict[str, str]]] = {}
        for link in _parse_link_tags(index):
            rel = link.get("rel", "")
            if rel in {"icon", "shortcut icon", "apple-touch-icon"}:
                links_by_rel.setdefault(rel, []).append(link)

        self.assertEqual(len(links_by_rel.get("icon", [])), 1)
        self.assertEqual(len(links_by_rel.get("shortcut icon", [])), 1)
        self.assertEqual(len(links_by_rel.get("apple-touch-icon", [])), 1)
        favicon_link = links_by_rel["icon"][0]
        shortcut_link = links_by_rel["shortcut icon"][0]
        apple_link = links_by_rel["apple-touch-icon"][0]
        self.assertEqual(
            favicon_link.get("href"),
            f"/assets/{versioned_favicon.name}",
        )
        self.assertEqual(favicon_link.get("type"), "image/png")
        self.assertEqual(favicon_link.get("sizes"), "128x128")
        self.assertEqual(shortcut_link.get("href"), "/favicon.ico")
        self.assertEqual(apple_link.get("href"), "/apple-touch-icon.png")
        self.assertEqual(apple_link.get("sizes"), "180x180")
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
        self.assertNotIn(
            "data:font/",
            built_css,
            "fonts must remain same-origin files allowed by the production CSP",
        )

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
        self.assertEqual(config.count("location = /tour {"), 1)
        self.assertEqual(config.count("location = /tour/ {"), 1)
        self.assertEqual(config.count("location = /landing-old {"), 1)
        self.assertEqual(config.count("location = /landing-old/ {"), 1)
        self.assertNotRegex(config, r"location\s+(?:\^~\s+)?/studio/")
        self.assertIn("root /var/www/kaigo-marketing/current;", config)
        self.assertGreaterEqual(config.count("try_files /index.html =404;"), 5)

        old_landing = config.split("location = /landing-old/ {", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn(
            "root /var/www/kaigo-marketing-archives/landing-old;",
            old_landing,
        )
        self.assertIn("try_files /index.html =404;", old_landing)
        self.assertIn('Cache-Control "no-store"', old_landing)
        self.assertIn("font-src 'self' data:", old_landing)

        old_assets = config.split("location ^~ /landing-old/assets/ {", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn(
            "alias /var/www/kaigo-marketing-archives/landing-old/assets/;",
            old_assets,
        )
        self.assertNotIn("proxy_pass", old_assets)

        old_redirect = config.split("location = /landing-old {", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn("return 308 /landing-old/;", old_redirect)

        self.assertIn(
            'location ~* "^/assets/[^/]+-[A-Za-z0-9_-]{8}\\.',
            config,
        )
        self.assertIn('Cache-Control "public, max-age=31536000, immutable"', config)
        stable_assets = config.split("location /assets/ {", 1)[1].split("}", 1)[0]
        self.assertIn('Cache-Control "no-cache"', stable_assets)
        self.assertIn("try_files $uri =404;", config)

        stable_location = (
            "location ~ ^/(favicon\\.png|favicon\\.ico|apple-touch-icon\\.png)$ {"
        )
        self.assertIn(stable_location, config)
        stable_icons = config.split(stable_location, 1)[1].split("}", 1)[0]
        self.assertIn("root /var/www/kaigo-marketing/current;", stable_icons)
        self.assertIn("try_files $uri =404;", stable_icons)
        self.assertIn('Cache-Control "no-cache"', stable_icons)
        self.assertIn('X-Content-Type-Options "nosniff"', stable_icons)

        favicon_redirect = config.split("location = /favicon.svg {", 1)[1].split(
            "}", 1
        )[0]
        self.assertIn(
            "return 308 /assets/favicon-living-fold-a96d189f.png;",
            favicon_redirect,
        )

        fallback = config.split("location / {", 1)[1]
        self.assertIn("proxy_pass http://127.0.0.1:8080;", fallback)
        self.assertIn("proxy_set_header Host $host;", fallback)

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

        self.assertEqual(len(csp_lines), 9)
        html_csp = [line for line in csp_lines if "frame-src 'self'" in line]
        asset_csp = [line for line in csp_lines if "default-src 'none'" in line]
        vk_csp = [line for line in html_csp if "https://id.vk.ru" in line]
        self.assertEqual(len(html_csp), 6)
        self.assertEqual(len(asset_csp), 3)
        self.assertEqual(len(vk_csp), 2)
        for line in html_csp:
            self.assertIn("default-src 'self'", line)
            self.assertIn("object-src 'none'", line)
            self.assertIn("frame-src 'self'", line)
            self.assertIn("frame-ancestors 'self'", line)
            self.assertNotIn("script-src 'self' 'unsafe-inline'", line)
            self.assertNotIn("*", line)
            self.assertNotIn("unpkg.com", line)
            self.assertNotIn("mytopf.com", line)
        for line in vk_csp:
            self.assertIn("connect-src 'self' https://id.vk.ru", line)
            self.assertIn("frame-src 'self' https://id.vk.ru", line)
            self.assertIn("script-src 'self';", line)
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

            index_links = _parse_link_tags(index)
            favicon_links = [
                link for link in index_links if link.get("rel") == "icon"
            ]
            self.assertEqual(len(favicon_links), 1)
            primary_favicon_href = favicon_links[0].get("href", "")
            self.assertRegex(
                primary_favicon_href,
                r"^/assets/favicon-living-fold-[a-f0-9]{8}\.png$",
            )
            with urllib.request.urlopen(
                f"{base_url}{primary_favicon_href}",
                timeout=2,
            ) as versioned_response:
                self.assertEqual(versioned_response.status, 200)
                self.assertIn("immutable", versioned_response.headers["Cache-Control"])
                self.assertEqual(versioned_response.headers["Content-Type"], "image/png")

            for stable_path, content_type in (
                ("/favicon.png", "image/png"),
                ("/favicon.ico", "image/x-icon"),
                ("/apple-touch-icon.png", "image/png"),
            ):
                with urllib.request.urlopen(
                    f"{base_url}{stable_path}",
                    timeout=2,
                ) as stable_icon_response:
                    self.assertEqual(stable_icon_response.status, 200)
                    self.assertEqual(
                        stable_icon_response.headers["Cache-Control"],
                        "no-cache",
                    )
                    self.assertEqual(
                        stable_icon_response.headers["Content-Type"],
                        content_type,
                    )

            class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, request, file, code, message, headers, newurl):
                    return None

            no_redirect_opener = urllib.request.build_opener(NoRedirectHandler)
            with self.assertRaises(urllib.error.HTTPError) as redirect_error:
                no_redirect_opener.open(f"{base_url}/favicon.svg", timeout=2)
            self.assertEqual(redirect_error.exception.code, 308)
            self.assertEqual(
                urllib.parse.urlsplit(
                    redirect_error.exception.headers["Location"]
                ).path,
                "/assets/favicon-living-fold-a96d189f.png",
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
