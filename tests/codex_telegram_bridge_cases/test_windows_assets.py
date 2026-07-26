import json
from pathlib import Path


ROOT = Path(__file__).parents[2]
BRIDGE = ROOT / "tools" / "codex_telegram_bridge"


def test_example_config_contains_no_bot_token() -> None:
    payload = json.loads((BRIDGE / "config.example.json").read_text(encoding="utf-8"))

    assert "telegram_token" not in payload
    assert payload["codex_command"] == "codex.cmd"


def test_setup_stores_token_outside_json_and_runs_safe_check() -> None:
    script = (BRIDGE / "setup.ps1").read_text(encoding="utf-8")

    assert 'SetEnvironmentVariable("TELEGRAM_BOT_TOKEN"' in script
    assert '"User"' in script
    assert "--check-config" in script
    assert 'telegram_token"' not in script


def test_runner_restores_user_codex_home_and_starts_module() -> None:
    script = (BRIDGE / "run.ps1").read_text(encoding="utf-8")

    assert 'TELEGRAM_BOT_TOKEN' in script
    assert '.codex' in script
    assert '-m tools.codex_telegram_bridge' in script


def test_startup_scripts_use_current_user_scheduled_task() -> None:
    install = (BRIDGE / "install-startup-task.ps1").read_text(encoding="utf-8")
    uninstall = (BRIDGE / "uninstall-startup-task.ps1").read_text(encoding="utf-8")

    assert "New-ScheduledTaskTrigger" in install
    assert "-AtLogOn" in install
    assert "-RunLevel Limited" in install
    assert "Unregister-ScheduledTask" in uninstall


def test_powershell_scripts_are_ascii_for_windows_powershell_51() -> None:
    for path in BRIDGE.glob("*.ps1"):
        path.read_bytes().decode("ascii")


def test_readme_documents_reuse_and_current_editor_task() -> None:
    readme = (BRIDGE / "README.md").read_text(encoding="utf-8")

    assert "019f9e1b-fb04-7482-b62d-cee4c051131b" in readme
    assert "/use" in readme
    assert "install-startup-task.ps1" in readme
    assert "не публикует" in readme.lower()
