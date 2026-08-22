from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Any, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://kaigo.space"
REQUEST_TIMEOUT_SECONDS = 20.0


class CliError(RuntimeError):
    pass


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def _add_base_url(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url",
        default=os.getenv("KAIGO_OPERATOR_BASE_URL", DEFAULT_BASE_URL),
        help="Kaigo origin (default: https://kaigo.space)",
    )


def _add_funnel_filters(parser: argparse.ArgumentParser) -> None:
    _add_base_url(parser)
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--source")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only aggregate Kaigo operator reports"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    funnel = commands.add_parser("funnel", help="fetch the aggregate funnel")
    _add_funnel_filters(funnel)

    runs = commands.add_parser("runs", help="fetch sanitized recent runs")
    _add_base_url(runs)
    runs.add_argument("--limit", type=_positive_int, default=50)

    snapshot = commands.add_parser(
        "snapshot", help="fetch funnel and recent runs together"
    )
    _add_funnel_filters(snapshot)
    snapshot.add_argument("--limit", type=_positive_int, default=50)
    return parser


def _read_token() -> str:
    direct = os.getenv("KAIGO_OPERATOR_READ_TOKEN", "").strip()
    if direct:
        return direct
    secret_file = os.getenv("KAIGO_OPERATOR_READ_TOKEN_FILE", "").strip()
    if secret_file:
        try:
            value = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            raise CliError("cannot read KAIGO_OPERATOR_READ_TOKEN_FILE") from error
        if value and "\n" not in value and "\r" not in value:
            return value
    raise CliError(
        "set KAIGO_OPERATOR_READ_TOKEN or KAIGO_OPERATOR_READ_TOKEN_FILE"
    )


def _request_json(
    *,
    base_url: str,
    path: str,
    params: dict[str, object | None],
    token: str,
) -> dict[str, Any]:
    normalized_base = base_url.strip().rstrip("/")
    if not normalized_base:
        raise CliError("base URL must not be empty")
    query = urlencode({key: value for key, value in params.items() if value is not None})
    url = f"{normalized_base}{path}"
    if query:
        url = f"{url}?{query}"
    request = Request(
        url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise CliError(f"request failed with HTTP {error.code}") from None
    except (URLError, TimeoutError, OSError):
        raise CliError("request failed with a network error") from None
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CliError("request returned invalid JSON") from None
    if not isinstance(payload, dict):
        raise CliError("request returned an invalid JSON object")
    return payload


def _funnel(args: argparse.Namespace, token: str) -> dict[str, Any]:
    return _request_json(
        base_url=args.base_url,
        path="/api/operator/funnel",
        params={
            "from": args.from_date,
            "to": args.to_date,
            "source": args.source,
        },
        token=token,
    )


def _runs(args: argparse.Namespace, token: str) -> dict[str, Any]:
    return _request_json(
        base_url=args.base_url,
        path="/api/operator/generation-runs",
        params={"limit": args.limit},
        token=token,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        token = _read_token()
        if args.command == "funnel":
            payload: object = _funnel(args, token)
        elif args.command == "runs":
            payload = _runs(args, token)
        else:
            payload = {
                "funnel": _funnel(args, token),
                "runs": _runs(args, token),
            }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0
    except CliError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
