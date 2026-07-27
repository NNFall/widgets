from pathlib import Path


def test_builder_never_sets_a_gemini_output_token_limit():
    root = Path(__file__).parents[2]
    production_files = [
        *sorted((root / "builder_lab").rglob("*.py")),
        *sorted((root / "app" / "models").rglob("*.py")),
        root / "scripts" / "analyze_reference_site.py",
    ]

    forbidden = ("max_output_tokens", "max_tokens")
    offenders = [
        str(path.relative_to(root))
        for path in production_files
        if any(
            field in path.read_text(encoding="utf-8")
            for field in forbidden
        )
    ]

    assert offenders == []
