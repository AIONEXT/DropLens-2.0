# DropLens build & packaging

Two artifacts are produced on Windows:

1. `dist\DropLens.exe` — portable single-file executable (PyInstaller).
2. `dist\Setup-DropLens-<V>.exe` — professional **installer** (Inno Setup):
   branded welcome screen, Start-menu + desktop shortcuts, uninstaller,
   AppUserModelId, and silent-install support.

End users need nothing installed besides the artifact itself.

## Quick start

```powershell
# 1. (fresh machine) install Python 3.11–3.13 from python.org, then:
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt

# 2. build the single-file .exe + installer
powershell -ExecutionPolicy Bypass -File .\build.ps1
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1   # needs Inno Setup 6 (winget install JRSoftware.InnoSetup)
```

## What `build.ps1` does

1. Verifies Python / pip are available.
2. Installs all runtime + build requirements.
3. Generates `build\appicon.ico` from source artwork (pure Python/Pillow).
4. Compiles every source module (catches syntax errors early).
5. Runs the engine smoke test (`tests\test_engine.py`).
6. Invokes PyInstaller on `launcher.py` (root-level launcher that imports the
   `dropLens` package — avoids relative-import issues when frozen):

```text
--onefile --windowed --icon build\appicon.ico
--name DropLens
--collect-all tkinterdnd2        (drag & drop binaries)
--hidden-import pymupdf
```

The result is a fully self-contained `dist\DropLens.exe` (~100 MB with Python,
all extraction libraries, and the AI + tray clients) that stores its catalogue,
settings and AI configuration under `%LOCALAPPDATA%\DropLens` the first time it runs.

## What `build_installer.ps1` does

1. Locates Inno Setup 6 (`ISCC.exe`).
2. Generates branded wizard bitmaps via `installer\make_bitmaps.py`
   (dark gradient + the DropLens lens icon).
3. Compiles `installer\DropLens.iss` → `dist\Setup-DropLens-<V>.exe`.

The installer installs to `%LOCALAPPDATA%\Programs\DropLens` (no admin needed,
`PrivilegesRequired=lowest`), creates Start-menu + optional desktop shortcut and
launches the app. Uninstalling removes the app but **keeps your
`%LOCALAPPDATA%\DropLens` catalogue** untouched.

## IT / commerce checklist

- [ ] Code-sign both exes (Microsoft/OV or EV cert) before public distribution.
- [ ] Pin dependency versions before release and rebuild from a clean venv for a
      reproducible binary.
- [ ] Toggle `ocr_enabled` in Settings; install Tesseract-OCR on user machines to
      unlock image text extraction (optional).
- [ ] Use `--onefile` for shippable single-file; run `--onedir` builds for faster
      startup in enterprise rollouts.
- [ ] Read `README.md` for the full feature list and `docs/COMMERCIAL.md` for
      licensing & deployment notes.

## Release build (recommended, reproducible)

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Version 2.0.0 -Clean
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1 -Version 2.0.0
```

`-Clean` removes the previous `dist/` and PyInstaller intermediates only; the
tracked build metadata under `build/` (icon, version info, docs) is kept.