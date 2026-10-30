# Run the backend in dev mode with autoreload.
# Usage: .\scripts\dev.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root 'venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Error "venv not found at $venvPython. Create one with: python -m venv venv"
    exit 1
}

Push-Location (Join-Path $root 'backend')
try {
    & $venvPython -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
} finally {
    Pop-Location
}
