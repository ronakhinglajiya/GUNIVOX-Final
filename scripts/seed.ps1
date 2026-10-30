# Seed initial data (admin user, sample leads). Phase 4 will fill this in.
# Usage: .\scripts\seed.ps1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root 'venv\Scripts\python.exe'

Push-Location (Join-Path $root 'backend')
try {
    & $venvPython -m scripts.seed
} finally {
    Pop-Location
}
