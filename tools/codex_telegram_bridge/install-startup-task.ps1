[CmdletBinding()]
param(
    [string]$TaskName = "Kaigo Codex Telegram Bridge"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runScript = (Resolve-Path (Join-Path $PSScriptRoot "run.ps1")).Path
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$configPath = Join-Path $env:LOCALAPPDATA "KaigoCodexTelegramBridge\config.json"
if (-not (Test-Path -LiteralPath $configPath)) {
    throw "Configuration was not found. Run setup.ps1 first."
}
if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable("TELEGRAM_BOT_TOKEN", "User"))) {
    throw "TELEGRAM_BOT_TOKEN is not configured. Run setup.ps1 first."
}

$powerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$arguments = '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}"' -f $runScript
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $arguments -WorkingDirectory $repoRoot
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 3650)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description "Forwards authorized Telegram messages to local Codex tasks." `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force | Out-Null

Write-Host "Startup task installed: $TaskName" -ForegroundColor Green
Write-Host "Start now: Start-ScheduledTask -TaskName `"$TaskName`""
