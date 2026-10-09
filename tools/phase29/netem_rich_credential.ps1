# Explicit local input only. No plaintext password is written or printed.
param([Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = Join-Path $PSHOME 'Modules'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$artifactRoot = [IO.Path]::GetFullPath((Join-Path $root 'test-artifacts')).TrimEnd('\') + '\'
$target = [IO.Path]::GetFullPath($Output)
if (-not $target.StartsWith($artifactRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Credential output must stay in test-artifacts.'
}
if (Test-Path -LiteralPath $target) { throw 'Refusing to overwrite a credential.' }
Write-Host 'NETEM protected release: enter your local administrator credential.'
Write-Host 'The password is encrypted with Windows CurrentUser DPAPI and deleted after release.'
$username = Read-Host 'Administrator username (Enter = admin)'
if (-not $username) { $username = 'admin' }
$password = Read-Host 'Current administrator password' -AsSecureString
$encrypted = ConvertFrom-SecureString -SecureString $password
$json = @{ username=$username; encrypted_password=$encrypted } | ConvertTo-Json
[IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target)) | Out-Null
$stream = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
try {
    $writer = [IO.StreamWriter]::new($stream, [Text.UTF8Encoding]::new($false))
    try { $writer.Write($json) } finally { $writer.Dispose() }
} finally { $stream.Dispose() }
$password.Dispose()
Write-Host 'Encrypted local credential is ready. You may close this window.' -ForegroundColor Green
