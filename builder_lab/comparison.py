from __future__ import annotations

import hashlib
import html
import json
import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


MANIFEST_NAME = "manifest.json"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _source_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("bundle source must be a real directory")
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("bundle source must not contain symlinks")
        if path.is_file() and path.name != MANIFEST_NAME:
            files.append(path)
    if not files:
        raise ValueError("bundle source is empty")
    return tuple(files)


def _file_manifest(root: Path, files: Sequence[Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest, byte_count = _file_digest(path)
        result[relative] = {"sha256": digest, "byte_count": byte_count}
    return result


def _bundle_digest(
    files: Mapping[str, Mapping[str, Any]],
    metadata: Mapping[str, Any],
) -> str:
    return hashlib.sha256(
        _json_bytes({"files": files, "metadata": metadata})
    ).hexdigest()


@dataclass(frozen=True)
class FrozenBundle:
    root: Path
    manifest: Mapping[str, Any]

    @property
    def digest(self) -> str:
        return str(self.manifest["bundle_digest"])


def verify_bundle(root: str | Path) -> bool:
    directory = Path(root)
    manifest_path = directory / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1:
            return False
        expected = manifest["files"]
        if not isinstance(expected, dict) or not expected:
            return False
        actual_paths = {
            path.relative_to(directory).as_posix()
            for path in directory.rglob("*")
            if path.is_file() and path.name != MANIFEST_NAME
        }
        if actual_paths != set(expected):
            return False
        for relative, record in expected.items():
            path = directory / Path(relative)
            if path.is_symlink() or not path.is_file():
                return False
            digest, byte_count = _file_digest(path)
            if record != {"sha256": digest, "byte_count": byte_count}:
                return False
        metadata = manifest.get("metadata", {})
        return manifest.get("bundle_digest") == _bundle_digest(expected, metadata)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False


def freeze_bundle(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> FrozenBundle:
    source = Path(source_dir).resolve()
    output = Path(output_dir).resolve()
    safe_metadata = json.loads(_json_bytes(dict(metadata or {})).decode("utf-8"))
    files = _source_files(source)
    file_manifest = _file_manifest(source, files)
    digest = _bundle_digest(file_manifest, safe_metadata)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "bundle_digest": digest,
        "metadata": safe_metadata,
        "files": file_manifest,
    }
    if output.exists():
        existing_path = output / MANIFEST_NAME
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FileExistsError(f"existing bundle is not verifiable: {output}") from exc
        if existing.get("bundle_digest") != digest or not verify_bundle(output):
            raise FileExistsError(f"refusing to replace a different bundle: {output}")
        return FrozenBundle(root=output, manifest=existing)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output.name}-", dir=str(output.parent))
    )
    try:
        for source_file in files:
            relative = source_file.relative_to(source)
            destination = temporary / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination)
        (temporary / MANIFEST_NAME).write_bytes(_json_bytes(manifest))
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    if not verify_bundle(output):
        raise RuntimeError("frozen bundle failed post-write verification")
    return FrozenBundle(root=output, manifest=manifest)


@dataclass(frozen=True)
class ComparisonVariant:
    slug: str
    title: str
    model: str
    thinking: str
    status: str
    summary: str
    raw_slug: str | None = None
    profile: str | None = None
    critique_summary: str | None = None
    elapsed_seconds: float | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    final_label: str = "Final"

    def __post_init__(self) -> None:
        self._validate_slug(self.slug, "slug")
        for name in ("title", "model", "thinking", "status", "summary"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 2000:
                raise ValueError(f"comparison {name} is invalid")
        if (
            not isinstance(self.final_label, str)
            or not self.final_label.strip()
            or len(self.final_label) > 40
        ):
            raise ValueError("comparison final_label is invalid")
        experiment_fields = (
            self.raw_slug,
            self.profile,
            self.critique_summary,
            self.elapsed_seconds,
            self.total_tokens,
            self.cost_usd,
        )
        if any(value is not None for value in experiment_fields):
            if any(value is None for value in experiment_fields):
                raise ValueError("experiment comparison fields must be supplied together")
            self._validate_slug(self.raw_slug, "raw_slug")
            if self.raw_slug == self.slug:
                raise ValueError("raw and final comparison slugs must differ")
            if (
                not isinstance(self.profile, str)
                or not self.profile.strip()
                or len(self.profile) > 80
            ):
                raise ValueError("comparison profile is invalid")
            if (
                not isinstance(self.critique_summary, str)
                or not self.critique_summary.strip()
                or len(self.critique_summary) > 2000
            ):
                raise ValueError("comparison critique_summary is invalid")
            if (
                isinstance(self.elapsed_seconds, bool)
                or not isinstance(self.elapsed_seconds, (int, float))
                or not math.isfinite(float(self.elapsed_seconds))
                or not 0 <= float(self.elapsed_seconds) <= 7 * 24 * 60 * 60
            ):
                raise ValueError("comparison elapsed_seconds is invalid")
            if (
                type(self.total_tokens) is not int
                or not 0 <= self.total_tokens <= 100_000_000
            ):
                raise ValueError("comparison total_tokens is invalid")
            if (
                isinstance(self.cost_usd, bool)
                or not isinstance(self.cost_usd, (int, float))
                or not math.isfinite(float(self.cost_usd))
                or not 0 <= float(self.cost_usd) <= 100_000
            ):
                raise ValueError("comparison cost_usd is invalid")

    @staticmethod
    def _validate_slug(value: str | None, field_name: str) -> None:
        if not isinstance(value, str):
            raise ValueError(f"comparison {field_name} is invalid")
        path = Path(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in value
        ):
            raise ValueError(f"comparison {field_name} is invalid")

    @property
    def has_raw_final_pair(self) -> bool:
        return self.raw_slug is not None


def render_comparison_page(variants: Sequence[ComparisonVariant]) -> str:
    if not variants or len(variants) > 8:
        raise ValueError("comparison requires between one and eight variants")
    if len({variant.slug for variant in variants}) != len(variants):
        raise ValueError("comparison variant slugs must be unique")
    cards = []
    for variant in variants:
        slug = html.escape(variant.slug, quote=True)
        link = f'<a href="{slug}/">Открыть отдельно ↗</a>'
        metadata = ""
        preview = f"""
              <iframe src="{slug}/" title="{html.escape(variant.title, quote=True)}"
                loading="lazy"
                sandbox="allow-scripts allow-forms allow-same-origin"></iframe>"""
        if variant.has_raw_final_pair:
            raw_slug = html.escape(variant.raw_slug or "", quote=True)
            tokens = f"{variant.total_tokens:,}".replace(",", " ")
            final_label = html.escape(variant.final_label)
            link = (
                f'<nav class="variant-links"><a href="{raw_slug}/">Raw ↗</a>'
                f'<a href="{slug}/">{final_label} ↗</a></nav>'
            )
            metadata = f"""
              <div class="experiment-meta">
                <span>Профиль · {html.escape(variant.profile or "")}</span>
                <span>{float(variant.elapsed_seconds):.1f} с</span>
                <span>{tokens} токенов</span>
                <span>${float(variant.cost_usd):.4f}</span>
              </div>
              <p class="critique">{html.escape(variant.critique_summary or "")}</p>"""
            preview = f"""
              <div class="pair">
                <section><strong>Raw</strong><iframe src="{raw_slug}/"
                  title="{html.escape(variant.title, quote=True)} raw"
                  loading="lazy"
                  sandbox="allow-scripts allow-forms allow-same-origin"></iframe></section>
                <section><strong>{final_label}</strong><iframe src="{slug}/"
                  title="{html.escape(variant.title, quote=True)} final"
                  loading="lazy"
                  sandbox="allow-scripts allow-forms allow-same-origin"></iframe></section>
              </div>"""
        cards.append(
            f"""
            <article class="variant">
              <header>
                <div><span class="status">{html.escape(variant.status)}</span>
                  <h2>{html.escape(variant.title)}</h2></div>
                {link}
              </header>
              <p>{html.escape(variant.summary)}</p>
              <dl><div><dt>Модель</dt><dd>{html.escape(variant.model)}</dd></div>
                <div><dt>Thinking</dt><dd>{html.escape(variant.thinking)}</dd></div></dl>
              {metadata}
              {preview}
            </article>"""
        )
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kaigo · сравнение генераторов RAW BUREAU</title>
<style>
:root{{--bg:#12110f;--surface:#1d1b18;--line:#39352f;--text:#f2eee7;--muted:#aaa39a;--accent:#d8ff52}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 Inter,Arial,sans-serif}}
main{{width:min(1580px,calc(100% - 32px));margin:0 auto;padding:42px 0 72px}}
.intro{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:end;margin-bottom:28px}}
h1{{font-size:clamp(32px,5vw,76px);line-height:.95;letter-spacing:-.055em;margin:0;max-width:900px}}
.intro p{{color:var(--muted);max-width:520px;margin:0}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,430px),1fr));gap:18px}}
.variant{{border:1px solid var(--line);border-radius:24px;background:var(--surface);padding:16px;min-width:0}}
.variant header{{display:flex;justify-content:space-between;gap:16px;align-items:start}}
h2{{margin:6px 0 0;font-size:20px}}a{{color:var(--accent);text-decoration:none;white-space:nowrap}}
.status{{font:700 10px/1 monospace;text-transform:uppercase;letter-spacing:.12em;color:var(--accent)}}
.variant>p{{min-height:48px;color:var(--muted)}}dl{{display:flex;gap:22px;margin:0 0 14px}}
dl div{{min-width:0}}dt{{font-size:10px;color:var(--muted);text-transform:uppercase}}dd{{margin:2px 0 0;overflow-wrap:anywhere}}
iframe{{display:block;width:100%;height:650px;border:1px solid var(--line);border-radius:16px;background:#fff}}
.variant-links{{display:flex;gap:12px}}.experiment-meta{{display:flex;flex-wrap:wrap;gap:7px;margin:0 0 10px}}
.experiment-meta span{{padding:5px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font:11px/1.2 ui-monospace,monospace}}
.critique{{min-height:0!important;margin:0 0 14px!important}}.pair{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}
.pair section{{min-width:0}}.pair strong{{display:block;margin:0 0 7px;color:var(--muted);font:11px/1 ui-monospace,monospace;text-transform:uppercase}}
.pair iframe{{height:650px}}
@media(max-width:700px){{main{{width:min(100% - 16px,1580px);padding-top:20px}}.intro{{grid-template-columns:1fr}}iframe{{height:720px}}}}
@media(max-width:980px){{.pair{{grid-template-columns:1fr}}}}
</style></head><body><main>
<section class="intro"><h1>Один сайт. Два новых генератора. Старый результат.</h1>
<p>Все версии используют один и тот же бриф RAW BUREAU. Можно открыть каждую,
проверить чат, размеры, анимации и сравнить доказательства сборки.</p></section>
<section class="grid">{''.join(cards)}</section>
</main></body></html>"""


__all__ = [
    "ComparisonVariant",
    "FrozenBundle",
    "freeze_bundle",
    "render_comparison_page",
    "verify_bundle",
]
