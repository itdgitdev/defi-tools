param(
    [ValidateSet("Check", "DryRun", "Once", "Loop", "Report", "Migrate")]
    [string]$Mode = "Check",
    [string]$ConfigFile = ""
)
$ErrorActionPreference = "Stop"
$toolRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repoRoot = (Resolve-Path (Join-Path $toolRoot "..\..")).Path
$pythonExe = Join-Path $toolRoot ".venv\Scripts\python.exe"
if (-not $ConfigFile) { $ConfigFile = Join-Path $toolRoot "config\local.json" }
if (-not [IO.Path]::IsPathRooted($ConfigFile)) { $ConfigFile = Join-Path $toolRoot $ConfigFile }
foreach ($file in @($pythonExe, $ConfigFile, (Join-Path $repoRoot ".env"))) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Required file not found: $file" }
}
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$logDir = Join-Path $toolRoot "runtime\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir "configured_rebalancer_loop.log"
Push-Location $toolRoot
try {
    if ($Mode -eq "Check" -or $Mode -eq "Once" -or $Mode -eq "Loop") {
        & $pythonExe -m configured_pool_rebalancer.preflight --project-root $repoRoot --config $ConfigFile
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        if ($Mode -eq "Check") { exit 0 }
    }
    if ($Mode -eq "Once" -or $Mode -eq "Loop") {
        Write-Host "LIVE MODE: only one worker may use the same wallet."
        if ((Read-Host "Type LIVE to continue") -cne "LIVE") { exit 1 }
    }
    $cliArgs = @("-m", "configured_pool_rebalancer.cli", "--config", $ConfigFile)
    switch ($Mode) {
        "Once" { $cliArgs += @("--execute", "--migrate") }
        "Loop" { $cliArgs += @("--execute", "--migrate", "--loop") }
        "Report" { $cliArgs += "--pnl-report" }
        "Migrate" { $cliArgs += "--migrate" }
    }
    if ($Mode -eq "Once" -or $Mode -eq "Loop") {
        $savedPreference = $ErrorActionPreference
        try {
            $ErrorActionPreference = "Continue"
            & $pythonExe @cliArgs 2>&1 | Tee-Object -FilePath $logFile -Append
            $exitCode = $LASTEXITCODE
        } finally { $ErrorActionPreference = $savedPreference }
    } else {
        & $pythonExe @cliArgs
        $exitCode = $LASTEXITCODE
    }
} finally { Pop-Location }
exit $exitCode
