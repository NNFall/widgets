[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Read-RequiredText {
    param([Parameter(Mandatory = $true)][string]$Prompt)
    do {
        $value = (Read-Host $Prompt).Trim()
    } while ([string]::IsNullOrWhiteSpace($value))
    return $value
}

Write-Host "Local Telegram to Codex bridge setup" -ForegroundColor Cyan
Write-Host "The token is stored in the current Windows user's environment, never in config.json or Git."

$secureToken = Read-Host "Telegram bot token from BotFather" -AsSecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
try {
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
}
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
}
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "Telegram bot token cannot be empty."
}

$userText = Read-RequiredText "Your Telegram user ID"
$userId = 0L
if (-not [long]::TryParse($userText, [ref]$userId) -or $userId -le 0) {
    throw "Telegram user ID must be a positive integer."
}

$chatText = (Read-Host "Telegram chat ID (Enter = use $userId)").Trim()
if ([string]::IsNullOrWhiteSpace($chatText)) {
    $chatId = $userId
}
else {
    $chatId = 0L
    if (-not [long]::TryParse($chatText, [ref]$chatId) -or $chatId -eq 0) {
        throw "Telegram chat ID must be a non-zero integer."
    }
}

$threadText = Read-RequiredText "Codex task ID"
$threadId = [guid]::Empty
if (-not [guid]::TryParse($threadText, [ref]$threadId)) {
    throw "Codex task ID must be a UUID, for example 019f9e1b-fb04-7482-b62d-cee4c051131b."
}

$dataDir = Join-Path $env:LOCALAPPDATA "KaigoCodexTelegramBridge"
$configPath = Join-Path $dataDir "config.json"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

$bindings = [ordered]@{}
$bindings[([string]$chatId)] = $threadId.ToString()
$config = [ordered]@{
    allowed_user_ids = @($userId)
    chat_bindings = $bindings
    data_dir = "%LOCALAPPDATA%\KaigoCodexTelegramBridge"
    codex_home = "%USERPROFILE%\.codex"
    codex_command = "codex.cmd"
    poll_timeout_seconds = 30
    turn_timeout_seconds = 3600
    retry_delay_seconds = 5
    retention_days = 30
}
$json = $config | ConvertTo-Json -Depth 5
[IO.File]::WriteAllText($configPath, $json, [Text.UTF8Encoding]::new($false))
[Environment]::SetEnvironmentVariable("TELEGRAM_BOT_TOKEN", $token, "User")

$env:TELEGRAM_BOT_TOKEN = $token
$env:CODEX_HOME = Join-Path $env:USERPROFILE ".codex"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = (Get-Command python.exe -ErrorAction Stop).Source
Push-Location $repoRoot
try {
    & $python -m tools.codex_telegram_bridge --config $configPath --check-config
    if ($LASTEXITCODE -ne 0) {
        throw "Local configuration validation failed."
    }
}
finally {
    Pop-Location
    $token = $null
    $env:TELEGRAM_BOT_TOKEN = $null
}

Write-Host "Ready. Configuration: $configPath" -ForegroundColor Green
Write-Host "Manual start:"
Write-Host "  & `"$PSScriptRoot\run.ps1`""
Write-Host "Install startup task:"
Write-Host "  & `"$PSScriptRoot\install-startup-task.ps1`""
