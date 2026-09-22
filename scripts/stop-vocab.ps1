# Stop the Shici server, and prove that it is actually gone.
#
# Why this script is defensive
# ---------------------------
# On 2026-09-22 the live server was still running while `data/server.pid` named a
# process that no longer existed. The old version of this script read only that
# file, found no process, deleted the file and printed "Shici server stopped." --
# so the operator believed the database was quiesced before a migration while an
# application process was still holding it open. A migration run in that state
# would rebuild tables underneath a live writer.
#
# The rule this script now enforces:
#
#   * the recorded pid, the listener on the port, and every project python
#     process are all inspected -- never just one of them;
#   * a listener on the port is authoritative about "is the service up", because
#     the server may have been started from an interpreter outside the virtualenv
#     (that is exactly what happened: the listener was E:\python\python.exe);
#   * "Shici server stopped." is printed only after the port has no listener AND
#     no service process remains. If either check fails the script prints FAILED
#     and exits non-zero;
#   * a pid file naming a live process that is not ours is reported and left
#     alone. A recycled pid must never cost an unrelated process its life.
#
# No service manager is involved: processes are stopped with Stop-Process.

[CmdletBinding()]
param(
    # The port the server listens on. Overridable so the stop path can be tested
    # against a throwaway listener without touching a real server.
    [int]$Port = 8000,

    # Where server.pid lives. Defaults to $env:VOCAB_DATA_DIR, then <project>/data.
    [string]$DataRoot,

    # Which checkout's python processes belong to this service. Defaults to this
    # script's own checkout. It exists so the stop path can be exercised against a
    # throwaway tree without a test ever reaching the real checkout's processes.
    [string]$CheckoutRoot,

    # Report what is running and exit without stopping anything.
    [switch]$Status,

    # How long to wait for a stopped process to release the port.
    [int]$TimeoutSeconds = 15
)

$ErrorActionPreference = 'Stop'

$projectRoot = if ($CheckoutRoot) { $CheckoutRoot } else { Split-Path -Parent $PSScriptRoot }
if (-not $DataRoot) {
    $DataRoot = if ($env:VOCAB_DATA_DIR) { $env:VOCAB_DATA_DIR } else { Join-Path $projectRoot 'data' }
}
$pidPath = Join-Path $DataRoot 'server.pid'
$venvPython = Join-Path $projectRoot 'backend\.venv\Scripts\python.exe'

# Fail closed when the port cannot be inspected: a script that cannot verify the
# port state must never be able to report success.
if (-not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue)) {
    Write-Host 'REFUSING: Get-NetTCPConnection is unavailable, so the port state cannot be verified.' -ForegroundColor Red
    Write-Host 'Without that check this script cannot prove the server stopped.' -ForegroundColor Red
    exit 2
}

function Get-ProcessFacts {
    param([int]$ProcessId)

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }

    $path = ''
    try { $path = $process.Path } catch { $path = '' }
    $started = $null
    try { $started = $process.StartTime } catch { $started = $null }

    [pscustomobject]@{
        Id      = $process.Id
        Name    = $process.ProcessName
        Path    = $path
        Started = $started
    }
}

function Get-ListeningProcessId {
    param([int]$Port)

    return @(
        Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess -Unique |
            Where-Object { $_ -and $_ -ne 0 }
    )
}

function Get-ProjectProcess {
    # Every python/uvicorn process that belongs to this checkout. The executable
    # path is the only durable signal available without a service manager: the
    # launcher runs backend\.venv\Scripts\python.exe, and anything else under the
    # project root is ours by location.
    $found = @()
    foreach ($name in @('python', 'pythonw', 'uvicorn')) {
        foreach ($process in @(Get-Process -Name $name -ErrorAction SilentlyContinue)) {
            $facts = Get-ProcessFacts -ProcessId $process.Id
            if ($null -eq $facts -or -not $facts.Path) { continue }
            if ($facts.Path -ieq $venvPython -or
                $facts.Path.StartsWith($projectRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
                $found += $facts
            }
        }
    }
    return @($found | Sort-Object -Property Id -Unique)
}

function Test-IsOurProcess {
    # A process is ours when it runs from this checkout. A recycled pid pointing at
    # something else must not be treated as the server.
    param($Facts)

    if ($null -eq $Facts -or -not $Facts.Path) { return $false }
    return (
        $Facts.Path -ieq $venvPython -or
        $Facts.Path.StartsWith($projectRoot, [System.StringComparison]::OrdinalIgnoreCase)
    )
}

function Get-ServiceState {
    param([int]$Port)

    $portIds = Get-ListeningProcessId -Port $Port
    $projectProcesses = Get-ProjectProcess

    $ourProcesses = @($projectProcesses)
    foreach ($id in $portIds) {
        $facts = Get-ProcessFacts -ProcessId $id
        if ($null -ne $facts -and -not ($ourProcesses | Where-Object { $_.Id -eq $facts.Id })) {
            $ourProcesses += $facts
        }
    }

    [pscustomobject]@{
        PortOwners       = @($portIds)
        ProjectProcesses = @($projectProcesses)
        ServiceProcesses = @($ourProcesses | Sort-Object -Property Id -Unique)
    }
}

function Format-Process {
    param($Facts)
    if ($null -eq $Facts) { return '<none>' }
    $path = if ($Facts.Path) { $Facts.Path } else { '<path unavailable>' }
    return "pid $($Facts.Id) ($($Facts.Name)) $path"
}

# --- what is actually running ---------------------------------------------

$pidFileValue = $null
$pidFilePid = $null
$pidFileRaw = ''
$pidFileExists = Test-Path -LiteralPath $pidPath
if ($pidFileExists) {
    $pidFileRaw = (Get-Content -LiteralPath $pidPath -Raw -ErrorAction SilentlyContinue)
    if ($null -ne $pidFileRaw) { $pidFileRaw = $pidFileRaw.Trim() }
    $parsed = 0
    if ([int]::TryParse($pidFileRaw, [ref]$parsed)) { $pidFileValue = $parsed }
}

$pidFileProcess = $null
if ($pidFileValue) { $pidFileProcess = Get-ProcessFacts -ProcessId $pidFileValue }
$pidFileIsOurs = Test-IsOurProcess -Facts $pidFileProcess

$state = Get-ServiceState -Port $Port
$portRegistered = @($state.PortOwners)

Write-Host ''
Write-Host '== Shici server stop ==' -ForegroundColor Cyan
Write-Host "  data root : $DataRoot"
Write-Host "  port      : $Port"
if (-not $pidFileExists) {
    Write-Host '  pid file  : missing' -ForegroundColor Yellow
} elseif ($null -eq $pidFileValue) {
    Write-Host "  pid file  : unreadable ('$pidFileRaw')" -ForegroundColor Yellow
} else {
    Write-Host "  pid file  : $pidPath = $pidFileValue"
}

if ($portRegistered.Count -gt 0) {
    Write-Host "  listening : $Port is held by $(($portRegistered | ForEach-Object { "pid $_" }) -join ', ')"
    foreach ($id in $portRegistered) {
        Write-Host "              $(Format-Process (Get-ProcessFacts -ProcessId $id))"
    }
} else {
    Write-Host "  listening : $Port has no listener"
}

if ($state.ProjectProcesses.Count -gt 0) {
    Write-Host "  checkout  : $($state.ProjectProcesses.Count) project process(es)"
    foreach ($process in $state.ProjectProcesses) {
        Write-Host "              $(Format-Process $process)"
    }
} else {
    Write-Host '  checkout  : no project python process'
}

# The exact failure that motivated this rewrite: nothing usable in the pid file,
# but the port says otherwise. Name the real process instead of staying silent.
if ($portRegistered.Count -gt 0) {
    if (-not $pidFileExists) {
        Write-Host '  WARNING   : server.pid is missing but the port has a listener. The real process is named above.' -ForegroundColor Yellow
    } elseif ($null -eq $pidFileProcess) {
        Write-Host "  WARNING   : server.pid names pid $pidFileValue, which does not exist, but the port has a listener. The real process is named above." -ForegroundColor Yellow
    } elseif ($portRegistered -notcontains $pidFileProcess.Id) {
        Write-Host "  WARNING   : server.pid names pid $($pidFileProcess.Id) but the port is held by $(($portRegistered | ForEach-Object { "pid $_" }) -join ', ')." -ForegroundColor Yellow
    }
}

if ($pidFileExists -and $null -ne $pidFileProcess -and -not $pidFileIsOurs) {
    Write-Host "  WARNING   : server.pid names pid $($pidFileProcess.Id) ($($pidFileProcess.Name)), which is not a process from this checkout. It will not be stopped." -ForegroundColor Yellow
}

$alreadyStopped = ($portRegistered.Count -eq 0) -and ($state.ServiceProcesses.Count -eq 0)

if ($Status) {
    Write-Host ''
    if ($alreadyStopped) {
        Write-Host 'STATUS: no running Shici server (port has no listener, no service process).' -ForegroundColor Green
        exit 0
    }
    Write-Host 'STATUS: a Shici server appears to be running.' -ForegroundColor Yellow
    exit 1
}

if ($alreadyStopped) {
    # Nothing to stop. A stale pid file is cleaned up, but this is not reported as
    # having stopped anything.
    if ($pidFileExists) { Remove-Item -LiteralPath $pidPath -Force }
    Write-Host ''
    Write-Host 'No running Shici server found.' -ForegroundColor Green
    Write-Host "Verified: port $Port has no listener and no service process exists."
    exit 0
}

# --- stop every process that belongs to the service ------------------------

$targets = @($state.ServiceProcesses)
Write-Host ''
Write-Host "Stopping $($targets.Count) process(es)..."

foreach ($target in $targets) {
    try {
        Stop-Process -Id $target.Id -ErrorAction Stop
        Write-Host "  signalled $(Format-Process $target)"
    } catch {
        Write-Host "  could not signal pid $($target.Id): $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $state = Get-ServiceState -Port $Port
    if ($state.PortOwners.Count -eq 0 -and $state.ServiceProcesses.Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
}

# Anything still alive gets a second, forcible attempt before we judge.
$state = Get-ServiceState -Port $Port
if ($state.ServiceProcesses.Count -gt 0) {
    foreach ($lingering in $state.ServiceProcesses) {
        Write-Host "  forcing $(Format-Process $lingering)" -ForegroundColor Yellow
        Stop-Process -Id $lingering.Id -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $state = Get-ServiceState -Port $Port
        if ($state.PortOwners.Count -eq 0 -and $state.ServiceProcesses.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    }
}

# --- the only path that may claim success ---------------------------------

$state = Get-ServiceState -Port $Port
$portClear = ($state.PortOwners.Count -eq 0)
$processesGone = ($state.ServiceProcesses.Count -eq 0)

Write-Host ''
if ($portClear -and $processesGone) {
    if (Test-Path -LiteralPath $pidPath) { Remove-Item -LiteralPath $pidPath -Force }
    Write-Host 'Shici server stopped.' -ForegroundColor Green
    Write-Host "Verified: port $Port has no listener and no service process remains."
    exit 0
}

Write-Host 'FAILED: the Shici server is still running.' -ForegroundColor Red
if (-not $portClear) {
    Write-Host "  port $Port is still held by: $(($state.PortOwners | ForEach-Object { "pid $_" }) -join ', ')"
    foreach ($id in $state.PortOwners) {
        Write-Host "    $(Format-Process (Get-ProcessFacts -ProcessId $id))"
    }
}
if (-not $processesGone) {
    Write-Host '  service process(es) still alive:'
    foreach ($process in $state.ServiceProcesses) {
        Write-Host "    $(Format-Process $process)"
    }
}
Write-Host 'Do not start a migration while any of the above is alive.'
exit 1
