param(
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$launcher = Join-Path $projectRoot 'start-vocab.bat'
$icon = Join-Path $projectRoot 'assets\shici-app.ico'
$desktop = [Environment]::GetFolderPath('Desktop')

if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw "Launcher not found: $launcher"
}

if (-not (Test-Path -LiteralPath $icon -PathType Leaf)) {
    throw "Application icon not found: $icon"
}

if (-not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw "Desktop directory not found: $desktop"
}

$shell = New-Object -ComObject WScript.Shell
$shortcutPath = Join-Path $desktop '拾词.lnk'

# Do not overwrite an unrelated shortcut that happens to use the same name.
if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.TargetPath -and
        ([IO.Path]::GetFullPath($existing.TargetPath) -ne [IO.Path]::GetFullPath($launcher))) {
        $shortcutPath = Join-Path $desktop '拾词（本地词汇学习）.lnk'
    }
}

$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $launcher
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = "$icon,0"
$shortcut.Description = '启动拾词本地英语词汇学习应用'
$shortcut.WindowStyle = 7
$shortcut.Save()

if (-not $Quiet) {
    Write-Host "Desktop shortcut updated: $shortcutPath" -ForegroundColor Green
}

