[CmdletBinding()]
param(
    [string]$TaskName = "Kaigo Codex Telegram Bridge"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Startup task was not found: $TaskName"
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Startup task removed: $TaskName" -ForegroundColor Green
Write-Host "Configuration and local queue remain in %LOCALAPPDATA%\KaigoCodexTelegramBridge."
