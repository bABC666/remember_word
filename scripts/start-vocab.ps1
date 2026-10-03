$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot 'backend'
$frontendRoot = Join-Path $projectRoot 'frontend'
$python = Join-Path $backendRoot '.venv\Scripts\python.exe'
$dataRoot = if ($env:VOCAB_DATA_DIR) { $env:VOCAB_DATA_DIR } else { Join-Path $projectRoot 'data' }
$logRoot = Join-Path $dataRoot 'logs'
$pidPath = Join-Path $dataRoot 'server.pid'
$healthUrl = 'http://127.0.0.1:8000/api/health'
$appUrl = 'http://127.0.0.1:8000'
$shortcutInstaller = Join-Path $PSScriptRoot 'install-shortcut.ps1'

function Open-AppBrowser {
    try {
        Start-Process $appUrl
    } catch {
        Write-Warning "Shici is ready, but the browser could not be opened: $($_.Exception.Message)"
    }
}

# Keep the desktop shortcut aligned with the versioned launcher and icon.
try {
    & $shortcutInstaller -Quiet
} catch {
    Write-Warning "Desktop shortcut could not be refreshed: $($_.Exception.Message)"
}

New-Item -ItemType Directory -Force -Path $dataRoot, $logRoot | Out-Null

$health = $null
try {
    $health = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
} catch {
    # Expected when the local server is not running yet.
}
if ($health -and $health.StatusCode -eq 200) {
    Write-Host "Shici is already running at $appUrl" -ForegroundColor Green
    Open-AppBrowser
    exit 0
}

if (-not (Test-Path -LiteralPath $python)) {
    Write-Host 'First run: creating the Python environment...' -ForegroundColor Cyan
    python -m venv (Join-Path $backendRoot '.venv')
    & $python -m pip install --upgrade pip
    & $python -m pip install -e "$backendRoot[dev]"
}

& $python -m pip show paddlepaddle paddleocr *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'First run: installing PaddleOCR CPU dependencies...' -ForegroundColor Cyan
    & $python -m pip install paddlepaddle==3.3.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
    & $python -m pip install -e "$backendRoot[ocr]"
}

if (-not (Test-Path -LiteralPath (Join-Path $frontendRoot 'node_modules'))) {
    Write-Host 'First run: installing frontend dependencies...' -ForegroundColor Cyan
    Push-Location $frontendRoot
    try { npm.cmd install } finally { Pop-Location }
}

Write-Host 'Building the frontend...' -ForegroundColor Cyan
Push-Location $frontendRoot
try {
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

Write-Host 'Checking the database schema...' -ForegroundColor Cyan
# Never `alembic upgrade head` from here. This launcher is what the desktop shortcut
# runs, so an unconditional migration would apply any new revision to the user's real
# database with no rehearsal, no backup, no explicit revision and no verification --
# and the server would then start cleanly, so nothing would report that it happened.
# scripts/prepare-database.ps1 decides instead: it starts without touching the schema
# when the database already matches the code, initialises only a database with
# nothing in it, and otherwise refuses and prints the explicit migration procedure.
#
# No -DatabasePath: the script asks python to resolve it from the process environment
# plus the same .env file uvicorn will load. Once resolved, it is passed back as an
# absolute db_url, so initialisation targets exactly the database that was inspected.
$envFile = Join-Path $projectRoot '.env'
$prepareArgs = @{ PythonPath = $python; RepositoryRoot = $projectRoot }
if (Test-Path -LiteralPath $envFile) {
    $prepareArgs.EnvFile = $envFile
}
& (Join-Path $PSScriptRoot 'prepare-database.ps1') @prepareArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host ''
    Write-Host 'The server was not started: the database schema needs an operator.' -ForegroundColor Red
    exit $LASTEXITCODE
}

$arguments = @('-m', 'uvicorn', 'app.main:app', '--app-dir', $backendRoot, '--host', '127.0.0.1', '--port', '8000')
if (Test-Path -LiteralPath $envFile) {
    $arguments += @('--env-file', $envFile)
}

$stdout = Join-Path $logRoot 'server-out.log'
$stderr = Join-Path $logRoot 'server-error.log'
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii

for ($attempt = 0; $attempt -lt 30; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($process.HasExited) {
        $detail = if (Test-Path -LiteralPath $stderr) { Get-Content -LiteralPath $stderr -Tail 20 | Out-String } else { '' }
        throw "FastAPI failed to start.`n$detail"
    }
    $health = $null
    try {
        $health = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    } catch {
        # Keep waiting until the bounded loop expires.
    }
    if ($health -and $health.StatusCode -eq 200) {
        Write-Host "Shici is running at $appUrl" -ForegroundColor Green
        Write-Host "Data directory: $dataRoot"
        Open-AppBrowser
        exit 0
    }
}

throw "FastAPI start timed out. See $stderr"
