# Run Alembic migrations.
# Usage:
#   .\scripts\migrate.ps1                        # upgrade head
#   .\scripts\migrate.ps1 revision "msg" --autogenerate
#   .\scripts\migrate.ps1 downgrade -1

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root 'venv\Scripts\python.exe'

if (-not (Test-Path $venvPython)) {
    Write-Error "venv not found at $venvPython"
    exit 1
}

Push-Location (Join-Path $root 'backend')
try {
    if ($args.Count -eq 0) {
        & $venvPython -m alembic upgrade head
    } else {
        & $venvPython -m alembic @args
    }
} finally {
    Pop-Location
}
