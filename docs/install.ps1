# Install Lumen from the latest GitHub release.
#
#   irm https://brxerq.github.io/lumen/install.ps1 | iex
#
# Downloads lumen.exe, checks it against the release's published SHA-256, and
# puts it in %LOCALAPPDATA%\Programs\Lumen (override with $env:LUMEN_BIN_DIR).
$ErrorActionPreference = "Stop"
# Windows PowerShell draws a progress bar per chunk, which dominates the download.
$ProgressPreference = "SilentlyContinue"

$repo = "Brxerq/lumen"
$custom = [bool]$env:LUMEN_BIN_DIR
$dir = if ($custom) { $env:LUMEN_BIN_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\Lumen" }
$exe = Join-Path $dir "lumen.exe"

$tag = (Invoke-RestMethod "https://api.github.com/repos/$repo/releases/latest").tag_name
Write-Host "Downloading Lumen $tag..."
$base = "https://github.com/$repo/releases/download/$tag"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("lumen-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    Invoke-WebRequest "$base/lumen.exe" -OutFile "$tmp\lumen.exe"
    $expected = ((Invoke-RestMethod "$base/lumen.exe.sha256") -split '\s+')[0]

    # The in-app updater refuses an unverified binary; so does this.
    $actual = (Get-FileHash "$tmp\lumen.exe" -Algorithm SHA256).Hash
    if ($actual -ne $expected.ToUpper()) { throw "Checksum mismatch - not installing." }

    # A running daemon holds its own file open. Only the copy being replaced:
    # a Lumen started from somewhere else is not this script's business.
    Get-Process lumen -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Stop-Process -Force
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    Move-Item "$tmp\lumen.exe" $exe -Force
} finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}

# Two different PATHs matter here, and confusing them is why `lumen` used to be
# "not recognized" in the very shell that had just installed it. The User
# environment variable is what *future* shells inherit; $env:Path is this
# process's own copy, and writing the first does not touch the second. Set both.
# A directory the caller chose is theirs to persist, but this shell still gets
# it, so the last line of this script is true either way.
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $custom -and ($userPath -split ';') -notcontains $dir) {
    # An empty or trailing-semicolon user PATH would otherwise grow an empty entry.
    $prefix = if ([string]::IsNullOrWhiteSpace($userPath)) { "" } else { $userPath.TrimEnd(';') + ";" }
    [Environment]::SetEnvironmentVariable("Path", "$prefix$dir", "User")
    Write-Host "Added $dir to your PATH."
}
if (($env:Path -split ';') -notcontains $dir) { $env:Path = "$($env:Path.TrimEnd(';'));$dir" }

Write-Host "Installed $exe"
if ($custom) {
    Write-Host "Run it with: lumen  (this shell only - add $dir to your PATH to keep it)"
} else {
    Write-Host "Run it with: lumen"
}
