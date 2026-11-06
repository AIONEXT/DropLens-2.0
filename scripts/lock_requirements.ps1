# Regenerate requirements-lock.txt from requirements.txt in a clean venv (Windows).
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\lock_requirements.ps1
#
# Run this on Windows before a release build: a few runtime packages
# (tkinterdnd2, pystray backends, backports.zstd) resolve differently per platform.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Join-Path $PSScriptRoot "..")

$tmp = Join-Path $env:TEMP ("droplens-lock-" + [guid]::NewGuid().ToString("N"))
$py  = Join-Path $tmp "Scripts\python.exe"
$out = "requirements-lock.txt"

function Finally-Cleanup {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Recurse -Force }
}

try {
    python -m venv $tmp
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }

    & $py -m pip install --quiet --upgrade pip
    & $py -m pip install --quiet -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw "dependency install failed" }

    $header = @"
# DropLens — pinned runtime dependency lock
#
# Purpose: reproducible installs. Every package the runtime actually imports is
# pinned to an exact version so two machines (and two builds) resolve the same
# tree. ``requirements.txt`` stays the human-authored *range* file; this file is
# what CI, the release build and enterprise rollouts install.
#
#   pip install -r requirements-lock.txt
#
# Regenerate after changing requirements.txt:
#   Linux/macOS: scripts/lock_requirements.sh
#   Windows:     scripts/lock_requirements.ps1
#
# NOTE: the set below is resolved for Windows/x64. Regenerate on the platform you
# build for; a handful of packages are platform-specific.
"@
    $pins = & $py -m pip freeze | Sort-Object
    if ($LASTEXITCODE -ne 0) { throw "pip freeze failed" }

    Set-Content -LiteralPath $out -Value $header -Encoding utf8
    Add-Content -LiteralPath $out -Value "" -Encoding utf8
    Add-Content -LiteralPath $out -Value $pins -Encoding utf8

    $count = ($pins | Where-Object { $_ -match "==" }).Count
    Write-Host "wrote $out ($count pinned packages)"
}
catch {
    Write-Error "lock generation failed: $_"
    Finally-Cleanup
    exit 1
}
Finally-Cleanup
