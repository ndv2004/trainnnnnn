param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path ".venv\Scripts\python.exe")) { throw "Run scripts\install.ps1 first." }
& .\.venv\Scripts\python.exe scripts\run.py --port $Port
exit $LASTEXITCODE
