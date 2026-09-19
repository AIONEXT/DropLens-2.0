# DropLens build & packaging

The portable .exe is built on Windows with PyInstaller. It bundles Python and
every extraction library, so end users need nothing installed.

## Quick start

```powershell
# 1. (fresh machine) install Python 3.11–3.13 from python.org, then:
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-build.txt

# 2. build the single-file .exe
powershell -ExecutionPolicy Bypass -File .\build.ps1

# 3. the ready-to-ship exe is written to  dist\DropLens.exe
```

## What the script does

1. Verifies Python / pip are available.
2. Installs all runtime + build requirements.
3. Generates `build\appicon.ico` from the bundled source artwork (pure Python,
   no Photoshop needed).
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

The result is a fully self-contained `dist\DropLens.exe` (~45 MB) that stores
its catalogue and settings under `%LOCALAPPDATA%\DropLens` the first time it runs.

## IT / commerce checklist

- [ ] Code-sign the exe (e.g. a Microsoft/OV  or EV cert) before public distribution.
- [ ] Pin dependency versions before release and rebuild from a clean venv for a
      reproducible binary.
- [ ] Toggle `ocr_enabled` in Settings; install Tesseract-OCR on user machines to
      unlock image text extraction.
- [ ] Use `--onefile` for shippable single-file; run `--onedir` builds for faster
      startup in enterprise rollouts.
- [ ] Read `README.md` for the full feature list and `docs/COMMERCIAL.md` for
      licensing & deployment notes.

## Release build (recommended, reproducible)

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Version 1.0.0 -Clean
```

`-Clean` wipes the previous `build/` and `dist/` folders first.