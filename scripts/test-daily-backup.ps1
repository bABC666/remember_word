# Synthetic-only acceptance; deliberately does not call scripts/check.ps1.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$runRoot = Join-Path $projectRoot ('test-artifacts/daily-backup/run-' + [guid]::NewGuid().ToString('N'))
$tempRoot = Join-Path $runRoot 'temp'
New-Item -ItemType Directory -Force $tempRoot | Out-Null
$names = @('TEMP', 'TMP', 'VOCAB_TEST_MODE', 'VOCAB_DATABASE_PATH', 'PYTHONUTF8')
$saved = @{}
foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
Push-Location $projectRoot
try {
    $env:TEMP = $tempRoot
    $env:TMP = $tempRoot
    $env:VOCAB_TEST_MODE = '1'
    $env:PYTHONUTF8 = '1'
    Remove-Item Env:VOCAB_DATABASE_PATH -ErrorAction SilentlyContinue
    & backend/.venv/Scripts/python.exe -m pytest backend/tests/test_backup.py backend/tests/test_scheduled_backup.py -q --basetemp (Join-Path $runRoot 'pytest')
    $testExit = $LASTEXITCODE
    if ($testExit -ne 0) { exit $testExit }
} finally {
    Pop-Location
    foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
}
