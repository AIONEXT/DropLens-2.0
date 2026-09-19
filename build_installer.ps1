# Build the professional DropLens installer (.exe with branded welcome art).
# Requires Inno Setup 6 (iscc.exe) — https://jrsoftware.org/isinfo.php

param(
    [string]$Version = "2.0.0"
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$iscc = $null
foreach ($p in @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
    "${env:LOCALAPPDATA}\Programs\Inno Setup 6\ISCC.exe"
)) {
    if (Test-Path $p) { $iscc = $p; break }
}
if (-not $iscc) {
    Write-Warning "Inno Setup 6 not found. Install it from https://jrsoftware.org/isinfo.php"
    Write-Warning "  (winget install JRSoftware.InnoSetup)"
    exit 2
}
if (-not (Test-Path "dist\DropLens.exe")) {
    Write-Warning "dist\DropLens.exe not found — run .\build.ps1 first."
    exit 2
}

# generate the branded welcome/dashboard bitmaps for the wizard
python -X utf8 installer\make_bitmaps.py
if (-not $?) { Write-Warning "Bitmap generation failed — using Inno defaults." }

& $iscc "installer\DropLens.iss" "/DMyAppVersion=$Version" "/DMyAppOutputDir=dist" 2>&1 | Select-Object -Last 12
if (-not $?) { exit 1 }

Get-Item "dist\Setup-DropLens-$Version.exe" |
    Select-Object FullName, @{n='SizeMB';e={[math]::Round($_.Length/1MB,1)}}