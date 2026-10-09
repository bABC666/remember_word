# Full local check for 拾词.
#
# Runs the required verification chain in order:
#   1. lint (ruff)
#   2. backend tests (temporary databases only)
#   3. frontend tests, typecheck, lint and production build
#   4. proof that pytest did not touch the real data directory
#   5. exact production/code revision gate, then historical row verification
#
# Steps 4 and 5 are REQUIRED gates: any change involving a migration, a downgrade, a
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

$productionDatabase = Join-Path $projectRoot 'data\vocab.db'
$expectedReleaseRevision = '0015_session_autoincrement'

Invoke-Step 'production database and code revision' {
    Push-Location $backendRoot
    try {
        $statusJson = & $python -m app.cli migration-status --database $productionDatabase
    } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    $status = $statusJson | ConvertFrom-Json
    Write-Host "Code head   : $($status.code_head)"
    Write-Host "DB revision : $($status.database_revision)"
    Write-Host "Database    : $productionDatabase"
    if (-not $status.exists -or $status.problem -or
        $status.code_head -ne $expectedReleaseRevision -or
        $status.database_revision -ne $expectedReleaseRevision -or
        $status.action -ne 'current') {
        Write-Host "FAILED: code and production database must both be $expectedReleaseRevision" -ForegroundColor Red
        exit 1
    }
}

Invoke-Step 'historical baseline rows, integrity and foreign keys' {
    Push-Location $projectRoot
    try {
        # The preceding gate pins both revisions to 0015; the 0007 baseline
        # remains the source of historical row fingerprints.
        & $python (Join-Path $projectRoot 'tools\verify_backup.py') `
            $productionDatabase `
            --baseline (Join-Path $projectRoot 'data\recovery\baseline.json') `
            --allow-revision-change
    } finally { Pop-Location }
}

Write-Host ""
Write-Host "All checks passed." -ForegroundColor Green
