# Decide what to do about the database schema before the server starts.
#
# Why this exists
# ---------------
# `start-vocab.ps1` used to run `alembic upgrade head` unconditionally, and the
# desktop shortcut runs that launcher. So a double-click after deploying a release
# containing a new migration applied it to the user's real database with none of
# the four gates: no rehearsal, no backup, no explicit revision, no verification.
# SQLite runs DDL non-transactionally, so a failure part-way through would leave
# some tables created, the revision unadvanced, and no backup to fall back to --
# and because the application's own startup check compares the code head to the
# database revision, it would have passed silently *after* the migration and
# warned nobody.
#
# What this does
# --------------
# It asks `python -m app.cli migration-status` (read-only, `mode=ro`) what the
# database looks like relative to the code, then:
#
#   current     the database already matches -> exit 0, and no migration runs at all
#   initialise  nothing recorded yet (fresh install, or a file with no tables) ->
#               run the migration, because there is no content to lose and the
#               server cannot start without a schema
#   refuse      anything else -> print how to migrate explicitly and exit 3
#
# The refused cases are the point. A database that is behind the code *and has
# rows* means someone's real data is about to change; that is an operator's
# deliberate act, with a rehearsal and a verified backup, not a side effect of
# starting the app. A database that is unreadable, corrupt, ahead of the code or
# otherwise not understood is refused for the same reason: not knowing is not
# permission.
#
# ASCII only, like the other operator scripts: Windows PowerShell reads a
# BOM-less script as ANSI, and non-ASCII text in here would be mis-decoded into
# parse errors rather than merely looking wrong.
#
# Exit codes
# ----------
#   0  schema ready, start the server
#   3  refused: do NOT start; the printed guidance says what to do
#   2  the state could not be established at all (also a refusal to start)

[CmdletBinding()]
param(
    # The database to inspect. Defaults to whatever the application would use.
    [string]$DatabasePath = '',
    # The python interpreter that has this checkout's dependencies installed.
    [string]$PythonPath = '',
    # The checkout whose alembic.ini and migrations are used.
    [string]$RepositoryRoot = '',
    # Use the same dotenv source as the server process.
    [string]$EnvFile = ''
)

$ErrorActionPreference = 'Stop'

if (-not $RepositoryRoot) {
    $RepositoryRoot = Split-Path -Parent $PSScriptRoot
}
if (-not $PythonPath) {
    $PythonPath = Join-Path $RepositoryRoot 'backend\.venv\Scripts\python.exe'
}

function Write-Guidance {
    param([string]$Database)
    Write-Host ''
    Write-Host 'This launcher will NOT migrate the database for you.' -ForegroundColor Yellow
    Write-Host 'Migrate it explicitly, following the release procedure, then start again:' -ForegroundColor Yellow
    Write-Host ''
    Write-Host '  1) Take a verified backup first (never migrate without one):'
    Write-Host "       python tools\verify_backup.py <backup-file> --baseline data\recovery\baseline.json"
    Write-Host '  2) Rehearse on an isolated clone (Level 2) and compare row counts and'
    Write-Host '     per-row fingerprints before and after. There is one rehearsal tool per'
    Write-Host '     migration, and a tool named after a revision rehearses from the one'
    Write-Host '     before it:'
    # Listed rather than exemplified: a hard-coded tool name goes stale the moment
    # another migration is added, and this message is only ever read by someone who
    # is already being told not to guess.
    $rehearsals = @(Get-ChildItem -LiteralPath (Join-Path $RepositoryRoot 'tools') `
        -Filter 'rehearsal_migration_*.py' -Name -ErrorAction SilentlyContinue)
    if ($rehearsals.Count -eq 0) {
        Write-Host '       (none found in tools\; follow the runbook below)'
    } else {
        foreach ($tool in $rehearsals) {
            Write-Host "       python tools\$tool --source `"$Database`""
        }
    }
    Write-Host '  3) Migrate with an EXPLICIT revision -- never `upgrade head` on real data:'
    Write-Host "       cd `"$RepositoryRoot\backend`""
    Write-Host "       .\.venv\Scripts\python.exe -m alembic -c alembic.ini -x `"db_url=sqlite:///$Database`" upgrade <revision>"
    Write-Host '  4) Verify revision, integrity and row fingerprints, then start again.'
    Write-Host ''
    Write-Host '  Full procedure: docs\0007-production-migration-runbook.md and -checklist.md.'
    Write-Host '  List the target revision with: python -m alembic heads'
}

function Invoke-BackendPython {
    # Run python from the backend directory, where `app` is importable and
    # alembic.ini resolves, and capture stdout+stderr without PowerShell turning the
    # first stderr line into a terminating error.
    #
    # Native commands write to stderr as a matter of course -- alembic logs there --
    # and with $ErrorActionPreference = 'Stop' a `2>&1` redirect converts that into a
    # "NativeCommandError" that replaces every diagnostic below with a wrapper. Every
    # call in this script goes through here so that cannot be forgotten at one site.
    param([string[]]$Arguments)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    Push-Location (Join-Path $RepositoryRoot 'backend')
    try {
        $output = & $PythonPath @Arguments 2>&1
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
        $ErrorActionPreference = $previousPreference
    }
    return [pscustomobject]@{ Output = @($output); ExitCode = $code }
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    Write-Host "Python interpreter not found: $PythonPath" -ForegroundColor Red
    Write-Host 'Cannot determine the database state, so the server will not start.' -ForegroundColor Red
    exit 2
}

$arguments = @('-m', 'app.cli', 'migration-status')
if ($DatabasePath) {
    $arguments += @('--database', $DatabasePath)
} elseif ($EnvFile) {
    $arguments += @('--env-file', $EnvFile)
}

$statusCall = Invoke-BackendPython -Arguments $arguments
$raw = $statusCall.Output
$exitCode = $statusCall.ExitCode

if ($exitCode -ne 0) {
    Write-Host 'Could not read the database migration state:' -ForegroundColor Red
    $raw | ForEach-Object { Write-Host "  $_" }
    exit 2
}

try {
    $status = ($raw -join "`n") | ConvertFrom-Json
} catch {
    Write-Host 'The migration status output could not be parsed, so the server will not start.' -ForegroundColor Red
    $raw | ForEach-Object { Write-Host "  $_" }
    exit 2
}

$resolvedDatabase = if ($status.database) { $status.database } else { $DatabasePath }

Write-Host "Database    : $resolvedDatabase"
Write-Host "Code head   : $($status.code_head)"
Write-Host "DB revision : $($status.database_revision)"
Write-Host "Tables/rows : $($status.table_count) / $($status.row_count)"

switch ($status.action) {
    'current' {
        Write-Host 'Schema already matches this code; no migration needed.' -ForegroundColor Green
        exit 0
    }
    'initialise' {
        Write-Host "Initialising the schema: $($status.reason)" -ForegroundColor Cyan
        # A genuinely fresh install has no data directory yet, and SQLite cannot
        # create a database inside a directory that does not exist. Creating it is
        # part of initialising; the inspection above stayed read-only.
        if ($status.database) {
            $dataDirectory = Split-Path -Parent $status.database
            if ($dataDirectory -and -not (Test-Path -LiteralPath $dataDirectory)) {
                New-Item -ItemType Directory -Force -Path $dataDirectory | Out-Null
                Write-Host "Created data directory: $dataDirectory"
            }
        }
        $migrate = @('-m', 'alembic', '-c', 'alembic.ini')
        if ($status.database) {
            $migrate += @('-x', "db_url=sqlite:///$($status.database)")
        }
        $migrate += @('upgrade', 'head')
        $migration = Invoke-BackendPython -Arguments $migrate
        if ($migration.ExitCode -ne 0) {
            Write-Host 'Schema initialisation failed; the server was not started.' -ForegroundColor Red
            $migration.Output | ForEach-Object { Write-Host "  $_" }
            Write-Host 'If this is a brand-new database, remove the file and retry.' -ForegroundColor Red
            Write-Host 'If it already holds data, do NOT retry: migrate explicitly instead.' -ForegroundColor Red
            Write-Guidance -Database $resolvedDatabase
            exit 3
        }
        # Prove it landed, rather than trusting the exit code alone. The path comes
        # from the status report, so this re-checks the database that was migrated
        # even when no -DatabasePath was passed.
        $verifyArguments = @('-m', 'app.cli', 'migration-status')
        if ($status.database) {
            $verifyArguments += @('--database', $status.database)
        }
        $verify = (Invoke-BackendPython -Arguments $verifyArguments).Output
        try {
            $after = ($verify -join "`n") | ConvertFrom-Json
        } catch {
            $after = $null
        }
        if (-not $after -or $after.action -ne 'current') {
            Write-Host 'The database still does not match the code; the server was not started.' -ForegroundColor Red
            $verify | ForEach-Object { Write-Host "  $_" }
            Write-Guidance -Database $resolvedDatabase
            exit 3
        }
        Write-Host "Schema initialised and verified at $($after.database_revision)." -ForegroundColor Green
        exit 0
    }
    default {
        Write-Host "Refusing to migrate automatically: $($status.reason)" -ForegroundColor Red
        Write-Guidance -Database $resolvedDatabase
        exit 3
    }
}
