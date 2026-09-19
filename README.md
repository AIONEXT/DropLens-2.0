<div align="center">

# DropLens 🧲

**Drop anything. Scan everything. Find it instantly.**

DropLens is a free, desktop, offline-first **universal file indexer and search
console for Windows**. Drag any file, any folder, any format, any size into the
window and DropLens scans, extracts, organizes, translates and makes it all —
hundreds and thousands of files and folders — instantly searchable.

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![python](https://img.shields.io/badge/python-3.11%20·%203.12%20·%203.13-green)
![build](https://img.shields.io/badge/build-PyInstaller%20onefile-orange)
![license](https://img.shields.io/badge/license-MIT-green)

</div>

---

## What it does

| | |
|---|---|
| 🗂 **Drop anything** | Drag files/folders straight into the window — or use *Add Folders / Add Files*. Nothing to configure. |
| 🔎 **Full-text search** | Fuzzy-prefix full-text search over **names, paths and extracted content** — instantly, across thousands of files. |
| 📄 **25+ formats extracted** | PDF · Word · Excel · PowerPoint · LibreOffice · plain text · code · JSON/XML/CSV · e-books · e-mail · archives · images (OCR) and more — original files are **never modified**. |
| 🌍 **Translation** | Select any search result and translate its content into 70+ languages (cached locally, originals untouched). |
| 🧹 **Organization** | Auto-categorization (Document / Spreadsheet / Code / Image / …), duplicate detection by content-hash, human-readable stats. |
| ♻️ **Auto-watch** | Monitor folders and re-index changes automatically — your library stays fresh. |
| 💾 **Portable data** | Everything lives in SQLite with FTS5 full-text search under `%LOCALAPPDATA%\DropLens`. Offline, private, no accounts. |
| 📤 **Export** | Catalogue, search results, duplicates and translations to CSV / TXT for Excel and reporting. |

---

## Quick start

**Download / build the .exe** (recommended, end-users need nothing installed):

```powershell
git clone https://github.com/<you>/DropLens.git
cd DropLens

python -m pip install -r requirements.txt -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

→ the ready-to-ship `dist\DropLens.exe` is produced. Double-click it.

**Or run from source** (developers):

```powershell
python -m pip install -r requirements.txt
python -m dropLens
```

### First use — 3 steps

1. **Drop** a file or entire folder anywhere into the main window (or press `Ctrl+O`).
2. **Watch it scan** — progress bar, live file counter, and the activity log report every result.
3. **Search** in the *Search* tab — type `annual report`, switch to *Content* mode, filter by type, and double-click any result to open or preview it.

---

## Features

### Search
- **Modes:** *All* (name+path+content), *Name*, *Path*, *Content* only.
- **Smart default:** treats words as AND with prefix wildcards — `annual repor` finds `annual-report.docx`.
- **Filters:** category (Document / Spreadsheet / Code / …), folder scope.
- **Ranked results** (BM25), highlighted match snippets, relevance ordering.
- **Preview pane** with yellow highlight of every match; empty query lists recently indexed files.
- Quick actions: **Open**, **Open Folder**, **Copy Path**, **Translate…**, **Export…**.

### Extraction matrix (auto-detected)

| Family | Formats |
|---|---|
| Text & code | txt, md, log, py, js, ts, java, c/cpp, go, rust, ruby, php, sql, html, css, json/xml/yaml/csv/tsv/toml/ini and ~50 more (character-encoding auto-detected) |
| Documents | PDF · DOCX · DOC (legacy) · RTF · ODT · TXT · TeX |
| Spreadsheets | XLSX · XLS (legacy) · ODS · CSV/TSV |
| Presentations | PPTX · PPT (legacy) · ODP |
| E-book & e-mail | EPUB · EML · MSG (when `extract-msg` installed) |
| Archives | ZIP · TAR · GZ · BZ2 · XZ · 7Z · RAR (content listing) |
| Images | OCR when **Tesseract** is installed (auto-detected in Settings) |

Everything else is still catalogued by name, size, dates and path — nothing
is silently skipped, and extraction failures are tracked per-file with
`no_content` / `failed` status flags (never losing the file itself).

### Duplicates
Identical files are found by **content hash** using a size-collision short-circuit
(it only hashes files whose byte-size collides — thousands of files stay fast).
The *Duplicates* tab reports groups + wasted space and exports them to CSV.

### Translation
Open any result with extractable text → **Translate…** → pick a target language
(70+). Translation runs on a background thread, results are cached in the local
database, and you can copy or save the translation as `.txt`. Source content is
never altered — the translation is a derived, clearly-labelled copy.

### Auto-watch
Tick **Auto-watch** (or enable in Settings). DropLens polls monitored folders on
an interval, detects changes with a cheap fingerprint, and re-indexes
incrementally — only changed files are re-extracted.

### Data quality & privacy
- Indexes are **read-only copies**; your originals are never modified.
- 100% local & offline search. (Translation uses Google’s free web endpoint only when you click it.)
- Incremental rescans skip unchanged files (size+mtime), so large trees re-scan in seconds.

---

## Where data lives

| Item | Location |
|---|---|
| Catalogue DB (SQLite + FTS5) | `%LOCALAPPDATA%\DropLens\catalog.db` |
| Settings | `%LOCALAPPDATA%\DropLens\settings.json` |
| Log (rotating) | `%LOCALAPPDATA%\DropLens\droplens.log` |

Override at runtime: set the `DROPLENS_DIR` environment variable, or change
*Settings → Data location* (applies immediately; existing catalogue open there).

---

## Building the .exe

See [`build/BUILD.md`](build/BUILD.md). Short version:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Clean -Version 1.0.0
```

Produces `dist\DropLens.exe` — a single portable file (~45 MB) with Python and
all extraction libraries bundled.

> **For public/commercial distribution:** code-sign the exe (OV/EV cert) and pin
> dependency versions before shipping. Details in [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).

---

## Architecture

```
dropLens/
├─ main.py              # entry point (DPI-aware; crash-safe logging)
├─ config.py            # typed settings, JSON store
├─ resources.py         # paths, logging, Tesseract discovery
├─ engine/
│  ├─ db.py             # SQLite + FTS5 catalogue (thread-safe RLock)
│  ├─ discover.py       # safe recursive walker + hashing + human sizes
│  ├─ extract.py        # 25+ format extractors (never touches originals)
│  ├─ categorize.py     # extension → category taxonomy
│  ├─ scan.py           # incremental scanning, worker thread, progress
│  ├─ search.py         # FTS5 query builder (column filters, prefix,*)
│  ├─ translate.py      # cached machine translation (Google free endpoint)
│  ├─ export.py         # CSV / TXT export
│  └─ watch.py          # fingerprint-polling folder watcher
└─ ui/
   ├─ mainwindow.py     # main window: drop zone, library, search, dupes, log
   └─ dialogs.py        # Settings / Translate / Duplicates / About dialogs
```

- **One background scan thread + queue** — worker threads only enqueue; the GUI
  main loop drains a `queue.Queue`. No Tk calls from worker threads (deadlock-safe).
- **SQLite FTS5 `unicode61`** index over name, path and content with BM25 ranking
  and `snippet()` highlighting.
- **Incremental diffing** by path, keyed on size+mtime; deletions pruned per root.

---

## Tests

```powershell
python -X utf8 tests\test_engine.py   # engine: scan, search, dedup, PDF, zip, PDF→search
python -X utf8 tests\test_gui.py      # GUI: window boots, indexes, searches, closes
```

---

## Roadmap

- [ ] “Pack” folders into a single searchable archive (one-click export)
- [ ] Tesseract OCR installer helper + lazy model downloads
- [ ] Local (offline) translation via lightweight models
- [ ] Content summaries with local embeddings (semantic search)
- [ ] Headless CLI (`droplens scan/search/export`) for automation & CI
- [ ] macOS/Linux builds

---

## License & commercial use

MIT License — use it, fork it, sell it, ship it. See
[`LICENSE`](LICENSE) and [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).

Icons: project-generated (Pillow) — no external assets to license.