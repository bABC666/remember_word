param(
    [ValidateSet('setup', 'start', 'stop')][string]$Action = 'start',
    [switch]$Reset
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'
if ($Reset -and $Action -ne 'setup') { throw 'Reset is allowed only with setup.' }
if ($Action -eq 'setup') {
    if (-not (Test-Path -LiteralPath $python)) {
        & py.exe -3.13 -m venv (Join-Path $projectRoot 'backend\.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Install Python 3.13 with the Windows py launcher.' }
    }
    & $python (Join-Path $projectRoot 'tools\classmate_runtime.py') validate
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m pip install -r (Join-Path $PSScriptRoot 'classmate-requirements.txt')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $python -m pip install --no-deps --no-build-isolation -e (Join-Path $projectRoot 'backend')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Push-Location (Join-Path $projectRoot 'frontend')
    try {
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath $python)) { throw 'Run setup-classmate-test.bat first.' }
$arguments = @((Join-Path $projectRoot 'tools\classmate_runtime.py'), $Action)
if ($Reset) { $arguments += '--reset' }
& $python @arguments
exit $LASTEXITCODE
