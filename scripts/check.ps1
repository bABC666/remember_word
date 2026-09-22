# Full local check for 拾词.
#
# Runs the required verification chain in order:
#   1. lint (ruff)
#   2. backend tests (temporary databases only)
#   3. frontend tests, typecheck, lint and production build
#   4. proof that pytest did not touch the real data directory
#
# Step 4 is a REQUIRED gate: any change involving a migration, a downgrade, a
# schema reset, a fixture schema setup or destructive SQL must pass it. Run this
# script instead of individual commands so the check never depends on memory.

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot 'backend'
$frontendRoot = Join-Path $projectRoot 'frontend'
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'

function Invoke-Step {
    param([string]$Name, [scriptblock]$Action)
    Write-Host ""
    Write-Host "== $Name ==" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "FAILED: $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "Backend virtualenv not found at $python" -ForegroundColor Red
    exit 1
}

Invoke-Step 'ruff (backend + tools)' {
    Push-Location $backendRoot
    try { & $python -m ruff check app tests } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Push-Location $projectRoot
    try { & $python -m ruff check tools } finally { Pop-Location }
}

Invoke-Step 'backend tests' {
    Push-Location $backendRoot
    try { & $python -m pytest tests -q } finally { Pop-Location }
}

if (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules')) {
    Invoke-Step 'frontend tests' {
        Push-Location $frontendRoot
        try { & npm.cmd test } finally { Pop-Location }
    }
    Invoke-Step 'frontend typecheck' {
        Push-Location $frontendRoot
        try { & npm.cmd run typecheck } finally { Pop-Location }
    }
    Invoke-Step 'frontend lint' {
        Push-Location $frontendRoot
        try { & npm.cmd run lint } finally { Pop-Location }
    }
    Invoke-Step 'frontend build' {
        Push-Location $frontendRoot
        try { & npm.cmd run build } finally { Pop-Location }
    }
} else {
    Write-Host ""
    Write-Host "== frontend checks skipped (node_modules missing) ==" -ForegroundColor Yellow
}

Invoke-Step 'test isolation proof (real data must be untouched)' {
    Push-Location $projectRoot
    try { & $python (Join-Path $projectRoot 'tools\prove_test_isolation.py') } finally { Pop-Location }
}

Invoke-Step 'verified backup check (live database vs baseline)' {
    Push-Location $projectRoot
    try {
        & $python (Join-Path $projectRoot 'tools\verify_backup.py') `
            (Join-Path $projectRoot 'data\vocab.db') `
            --baseline (Join-Path $projectRoot 'data\recovery\baseline.json')
    } finally { Pop-Location }
}

Write-Host ""
Write-Host "All checks passed." -ForegroundColor Green
