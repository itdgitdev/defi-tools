param(
    [string]$ConfigFile = "",
    [string]$CredentialsEnv = "",
    [switch]$Once,
    [switch]$SkipMigration
)
$ErrorActionPreference = "Stop"
$toolRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $toolRoot "..\..")).Path
$pythonExe = Join-Path $toolRoot ".venv\Scripts\python.exe"
if (-not $ConfigFile) { $ConfigFile = Join-Path $toolRoot "config\local.json" }
if (-not $CredentialsEnv) { $CredentialsEnv = Join-Path $repoRoot ".env" }
if (-not [IO.Path]::IsPathRooted($ConfigFile)) { $ConfigFile = Join-Path $toolRoot $ConfigFile }
if (-not [IO.Path]::IsPathRooted($CredentialsEnv)) { $CredentialsEnv = Join-Path $repoRoot $CredentialsEnv }
foreach ($file in @($ConfigFile, $CredentialsEnv)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required file not found: $file" }
}
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    & (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Monitor setup failed" }
}
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$cliArgs = @("-m", "binance_futures_monitor.cli", "--config", $ConfigFile, "--credentials-env", $CredentialsEnv)
if (-not $SkipMigration) { $cliArgs += "--migrate" }
if (-not $Once) { $cliArgs += "--loop" }
Push-Location $toolRoot
try {
    & $pythonExe @cliArgs
    $exitCode = $LASTEXITCODE
} finally { Pop-Location }
exit $exitCode
