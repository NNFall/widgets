[CmdletBinding()]
param(
    [string]$ConfigPath = (Join-Path $env:LOCALAPPDATA "KaigoCodexTelegramBridge\config.json")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$token = [Environment]::GetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "User")
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "TELEGRAM_BOT_TOKEN is not configured. Run setup.ps1 first."
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Configuration was not found: $ConfigPath. Run setup.ps1 first."
}

$env:TELEGRAM_BOT_TOKEN = $token
$env:CODEX_HOME = Join-Path $env:USERPROFILE ".codex"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = (Get-Command python.exe -ErrorAction Stop).Source

Push-Location $repoRoot
try {
    & $python -m tools.codex_telegram_bridge --config $ConfigPath
    exit $LASTEXITCODE
}
finally {
    Pop-Location
    $token = $null
    $env:TELEGRAM_BOT_TOKEN = $null
}
