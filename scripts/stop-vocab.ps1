$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$dataRoot = if ($env:VOCAB_DATA_DIR) { $env:VOCAB_DATA_DIR } else { Join-Path $projectRoot 'data' }
$pidPath = Join-Path $dataRoot 'server.pid'

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host 'No running Shici server was found.'
    exit 0
}

$serverPid = [int](Get-Content -LiteralPath $pidPath -Raw)
$process = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $serverPid
    $process.WaitForExit(5000)
}
Remove-Item -LiteralPath $pidPath -Force
Write-Host 'Shici server stopped.' -ForegroundColor Green
