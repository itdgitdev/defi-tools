$ErrorActionPreference = "Stop"
$toolRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$pythonExe = Join-Path $toolRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    & py -3.13 -m venv (Join-Path $toolRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Python 3.13 environment setup failed" }
}
Push-Location $toolRoot
try {
    & $pythonExe -m pip install -r (Join-Path $toolRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed" }
    & $pythonExe -m configured_pool_rebalancer.cli --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Rebalancer import check failed" }
} finally { Pop-Location }
Write-Host "SETUP COMPLETED"
