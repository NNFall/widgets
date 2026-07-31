from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CANARY_ROOT = ROOT / "deploy" / "publication-canary"
NGINX_CONFIG = ROOT / "deploy" / "nginx" / "kaigo-publication-canary.conf"


def _read(path: Path) -> str:
    assert path.is_file(), f"missing required canary file: {path}"
    return path.read_text(encoding="utf-8")


def test_canary_is_a_credential_free_static_package() -> None:
    index = _read(CANARY_ROOT / "index.html")
    javascript = _read(CANARY_ROOT / "canary.js")
    combined = f"{index}\n{javascript}".lower()

    assert '<script src="/canary.js" defer></script>' in index
    assert "fetch(" not in javascript
    assert "xmlhttprequest" not in combined
    assert "localstorage" not in combined
    assert "sessionstorage" not in combined
    assert "document.cookie" not in combined
    assert "authorization" not in combined
    assert "csrf" not in combined
    assert "api_key" not in combined
    assert "secret" not in combined
    assert "yookassa" not in combined


def test_canary_accepts_only_a_public_stable_key_and_fixed_embed_origin() -> None:
    javascript = _read(CANARY_ROOT / "canary.js")

    assert re.search(
        r"/\^\[A-Za-z0-9_-\]\{20,128\}\$/",
        javascript,
    )
    assert "https://kaigo.space/embed/" in javascript
    assert "new URLSearchParams(window.location.search)" in javascript
    assert "script.src = `https://kaigo.space/embed/${key}.js`" in javascript
    assert "origin" not in javascript.lower()


def test_nginx_serves_two_real_https_origins_with_fail_closed_headers() -> None:
    config = _read(NGINX_CONFIG)

    assert "server_name canary.kaigo.space;" in config
    assert "server_name denied-canary.kaigo.space;" in config
    assert config.count("listen 80;") >= 2
    assert config.count("return 301 https://$host$request_uri;") >= 2
    assert config.count("listen 443 ssl http2;") >= 2
    assert "/etc/letsencrypt/live/canary.kaigo.space/fullchain.pem" in config
    assert "/etc/letsencrypt/live/canary.kaigo.space/privkey.pem" in config
    assert "/etc/letsencrypt/live/denied-canary.kaigo.space/fullchain.pem" in config
    assert "/etc/letsencrypt/live/denied-canary.kaigo.space/privkey.pem" in config
    assert "proxy_pass" not in config
    assert "proxy_set_header" not in config

    required_headers = (
        'add_header Cache-Control "no-store" always;',
        'add_header Referrer-Policy "no-referrer" always;',
        'add_header X-Content-Type-Options "nosniff" always;',
        "add_header Permissions-Policy",
        "Content-Security-Policy",
        "script-src 'self' https://kaigo.space",
        "frame-src https://kaigo.space",
    )
    for declaration in required_headers:
        assert config.count(declaration) >= 2

    lowered = config.lower()
    assert "set-cookie" not in lowered
    assert "authorization" not in lowered
    assert "private_key" not in lowered
