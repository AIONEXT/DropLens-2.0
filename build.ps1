# Build & publish DropLens as a single file .exe on Windows.
# Usage:  powershell -ExecutionPolicy Bypass -File .\build.ps1 [-Clean] [-Version x.y.z]

param(
    [switch]$Clean,
    [string]$Version = "1.0.0"
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Step([string]$msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "Checking prerequisites"
$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "Python not found on PATH. Install Python 3.11-3.13 from python.org and check 'Add to PATH'." }

python --version
if (-not $?) { throw "python --version failed." }

Step "Installing requirements"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt

if ($Clean) {
    Step "Cleaning previous build artifacts (source metadata under build/ is kept)"
    if (Test-Path dist) { Remove-Item -Recurse -Force dist }
    Get-ChildItem -Path build -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match 'DropLens' } | Remove-Item -Recurse -Force
    Get-ChildItem -Filter "*.spec" | Remove-Item -Force -ErrorAction SilentlyContinue
}

Step "Generating application icon"
python -X utf8 build\make_icon.py build\appicon.ico

Step "Compiling source (syntax check)"
$files = Get-ChildItem dropLens -Recurse -Filter *.py | ForEach-Object { $_.FullName }
python -m py_compile @files
if (-not $?) { throw "Compilation failed." }

Step "Running engine smoke test"
python -X utf8 tests\test_engine.py

Step "Building DropLens.exe with PyInstaller"
$verAry = $Version.Split(".")
$nv = "0.$($verAry[0]).$($verAry[1]).$($verAry[2])"
python -m PyInstaller --noconfirm `
    --onefile --windowed `
    --name "DropLens" `
    --icon "build\appicon.ico" `
    --version-file "build\version_info.txt" `
    --collect-all "tkinterdnd2" `
    --hidden-import "pymupdf" `
    --exclude-module "test" `
    launcher.py

if (-not (Test-Path dist\DropLens.exe)) { throw "PyInstaller did not produce dist\DropLens.exe" }

Step "Done"
Get-Item dist\DropLens.exe | Select-Object FullName, @{n='SizeMB';e={[math]::Round($_.Length/1MB,1)}}
Write-Host "Artifact: $(Resolve-Path dist\DropLens.exe)" -ForegroundColor Green