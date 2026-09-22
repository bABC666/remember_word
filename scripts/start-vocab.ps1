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

# Keep the desktop shortcut aligned with the versioned launcher and icon.
try {
    & $shortcutInstaller -Quiet
} catch {
    Write-Warning "Desktop shortcut could not be refreshed: $($_.Exception.Message)"
}

New-Item -ItemType Directory -Force -Path $dataRoot, $logRoot | Out-Null

try {
    $health = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
    if ($health.StatusCode -eq 200) {
        Start-Process $appUrl
        Write-Host 'Shici is already running. The browser is open.' -ForegroundColor Green
        exit 0
    }
} catch {
    # Expected when the local server is not running yet.
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

Write-Host 'Applying database migrations...' -ForegroundColor Cyan
Push-Location $backendRoot
try {
    & $python -m alembic -c alembic.ini upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }

$arguments = @('-m', 'uvicorn', 'app.main:app', '--app-dir', $backendRoot, '--host', '127.0.0.1', '--port', '8000')
$envFile = Join-Path $projectRoot '.env'
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
    try {
        $health = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 2
        if ($health.StatusCode -eq 200) {
            Start-Process $appUrl
            Write-Host "Shici is running at $appUrl" -ForegroundColor Green
            Write-Host "Data directory: $dataRoot"
            exit 0
        }
    } catch {
        # Keep waiting until the bounded loop expires.
    }
}

throw "FastAPI start timed out. See $stderr"
