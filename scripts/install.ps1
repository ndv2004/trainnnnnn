param([switch]$SkipModels)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Install Python 3.12 x64 with the py launcher first." }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed; no success is assumed." }
& .\.venv\Scripts\python.exe -m pip check
if ($LASTEXITCODE -ne 0) { throw "Dependency compatibility check failed." }
if (-not $SkipModels) {
    & .\.venv\Scripts\python.exe scripts\download_models.py
    if ($LASTEXITCODE -ne 0) { throw "Model download/checksum failed." }
}
Write-Host "Installed. Start: .\.venv\Scripts\python.exe scripts\run.py"
