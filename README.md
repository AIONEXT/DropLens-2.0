<div align="center">

# DropLens 🧲

**Drop anything. Scan everything. Find it instantly.**

DropLens is a free, desktop, offline-first **universal file indexer, organizer
and AI-powered search console for Windows**. Drag any file, any folder, any
format, any size into the window — or let it silently index your whole
Documents / Downloads / Desktop tree in the background — and DropLens extracts,
organizes, translates, understands and makes it all — hundreds and thousands of
files — searchable **by keyword and by meaning**.

![platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue)
![python](https://img.shields.io/badge/python-3.11%20·%203.12%20·%203.13-green)
![build](https://img.shields.io/badge/build-PyInstaller%20onefile-orange)
![ai](https://img.shields.io/badge/AI-any%20local%20%7C%20cloud%20model-22d3ee)
![license](https://img.shields.io/badge/license-MIT-green)

</div>

---

## What it does

| | |
|---|---|
| 🗂 **Drop anything** | Drag files/folders straight into the window — or use *Add Folders / Add Files*. Nothing to configure. |
| 🤫 **Silent background scanning** | First-run wizard drops in your Documents, Downloads, Desktop, Pictures, Music and Videos — and indexes everything quietly in the background while you keep working. |
| ✨ **Branded installer & splash** | Professional Inno Setup installer with big welcome art, Start-menu/desktop shortcuts and uninstaller; branded splash + onboarding wizard on first launch. |
| 🧠 **Any AI model, local or cloud** | Connect **Ollama, LM Studio, llama.cpp, OpenAI, Azure, OpenRouter, Groq, Mistral, Anthropic Claude, Google Gemini or any OpenAI-compatible API** — offline models for zero-cost privacy, cloud models for maximum power. |
| 🔎 **Search by keyword AND meaning** | Fuzzy full-text search (FTS5 + BM25) over names, paths and content **plus** semantic search via embeddings that finds files about the same *topic* even with different words. |
| 🤖 **AI understanding of your data** | Auto **summaries**, **smart tags**, **embeddings**, an **AI assistant that answers questions about your library with file citations**, and **folder reports**. |
| 📄 **25+ formats extracted** | PDF · Word · Excel · PowerPoint · LibreOffice · text · code · JSON/XML/CSV · e-books · e-mail · archives · images (OCR) and more — your originals are **never modified**. |
| 🌍 **Translation** | Translate any item’s content into 70+ languages (cached locally, originals untouched). |
| 🧹 **Organization** | Auto-categorization, duplicates by content-hash, **favourites, notes, related files**, stats dashboard. |
| ♻️ **Auto-watch + tray** | Monitor folders and re-index silently; live in the system tray with notifications and “quick scan” — the library stays fresh even when the window is hidden. |
| 💾 **Portable, private data** | Everything lives in SQLite+FTS5 under `%LOCALAPPDATA%\DropLens`. Offline, private, no accounts unless you add a cloud key. |
| 📤 **Export** | Catalogue, search results, duplicates and translations to CSV / TXT. |

---

## Quick start

**Download the installer** — `Setup-DropLens-2.x.exe` (see Releases) — run it,
click through the branded wizard, then drop or scan.

**Or build / run from source:**

```powershell
git clone https://github.com/<you>/DropLens.git
cd DropLens

python -m pip install -r requirements.txt -r requirements-build.txt
powershell -ExecutionPolicy Bypass -File .\build.ps1          # -> dist\DropLens.exe
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1 # -> dist\Setup-DropLens.exe
```

**First use — 3 steps**

1. The **onboarding wizard** asks what to organize silently (Documents, Downloads, Desktop…) and whether to use a **local AI model** (Ollama/LM Studio — 100% offline). Pick, and DropLens starts scanning in the background.
2. **Drop** anything else straight into the main window anytime.
3. Ask the **AI Assistant** “what’s in my Downloads?” — or search normally and click **AI summary** on any result.

---

## Features

### AI (works with any local or cloud model)
- **Providers:** Ollama, LM Studio, llama.cpp, OpenAI, Azure OpenAI, OpenRouter, Groq, Mistral, Anthropic Claude, Google Gemini, or any OpenAI-compatible endpoint. Presets included; add your own URL/key/model.
- Add presets under **Settings → AI Models**, hit **Test connection**, and pick an embedding model for semantic search (e.g. `nomic-embed-text`, `text-embedding-3-small`).
- **AI summary** per document (cached), **auto tags** (clickable chips), **favourites & notes**, **related files** (embedding similarity).
- **AI Assistant** answers questions using only your indexed files and **cites the exact sources**.
- **AI-enrich library** generates summaries/tags/embeddings for everything (smallest files first, cancellable) — optionally automatic after each scan.
- No data leaves your machine unless you configure an online provider; local providers run fully offline.

### Search
- **Keyword modes:** *All* (name+path+content), *Name*, *Path*, *Content* — AND-prefix wildcards, BM25 ranking, highlighted snippets.
- **Semantic mode:** toggle “Semantic (AI meaning)” to find files by topic, not just by words.
- Category filters, recent-files default view, preview pane, **Load more**.
- Actions: **Open**, **Folder**, **★ Favorite**, **AI summary**, **Translate…**, **Copy Path**, **Export…**.

### Organization
- Stat dashboard (files / folders / size / duplicates / tags / AI-enriched), monitored-folder table, duplicate groups with wasted space, tag chips with counts, favourites.

### Extraction matrix (auto-detected)

| Family | Formats |
|---|---|
| Text & code | txt, md, log, py, js, ts, java, c/cpp, go, rust, ruby, php, sql, html, css, json/xml/yaml/csv/tsv/toml/ini and ~50 more (auto charset) |
| Documents | PDF · DOCX · DOC (legacy) · RTF · ODT · TXT · TeX |
| Spreadsheets | XLSX · XLS (legacy) · ODS · CSV/TSV |
| Presentations | PPTX · PPT (legacy) · ODP |
| E-book & e-mail | EPUB · EML · MSG (when `extract-msg` installed) |
| Archives | ZIP · TAR · GZ · BZ2 · XZ · 7Z · RAR (content listing) |
| Images | OCR when **Tesseract** is installed (auto-detected) |

### Duplicates, Translation, Auto-watch
- Duplicates by **content hash** with a size-collision short-circuit (hashes only colliding sizes — fast on thousands of files).
- Translation of any result into 70+ languages, cached in the DB, exported as `.txt`. Source never altered.
- **Auto-watch** re-indexes changed folders incrementally by size+mtime fingerprint.

### Privacy & data quality
- Indexes are **read-only copies**; originals are never modified.
- Search/scanning/translation caches are local. AI content is only sent to the model you choose — local by default.
- Incremental rescans skip unchanged files; large trees re-scan in seconds.

---

## Where data lives

| Item | Location |
|---|---|
| Catalogue DB (SQLite + FTS5) | `%LOCALAPPDATA%\DropLens\catalog.db` |
| Settings (incl. AI providers) | `%LOCALAPPDATA%\DropLens\settings.json` |
| Log (rotating) | `%LOCALAPPDATA%\DropLens\droplens.log` |

Override at runtime with the `DROPLENS_DIR` environment variable, or change
*Settings → Data location* (applies immediately).

---

## Building

See [`build/BUILD.md`](build/BUILD.md). Short version:

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Clean -Version 2.0.0      # DropLens.exe
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1 -Version 2.0.0   # Setup-DropLens.exe
```

> **For public/commercial distribution:** code-sign both exes (OV/EV cert) and
> pin dependency versions. Details in [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).

---

## Architecture

```
dropLens/
├─ main.py              # entry point (splash → onboarding → window; crash-safe logging)
├─ config.py            # typed settings + persisted AI provider config
├─ resources.py         # paths, logging, Tesseract discovery
├─ ai/
│  ├─ providers.py      # local/cloud provider presets (Ollama, OpenAI, Gemini, …)
│  ├─ client.py         # OpenAI-compatible / Anthropic / Gemini HTTP clients
│  └─ svc.py            # summaries, tags, embeddings, semantic search, RAG assistant
├─ engine/
│  ├─ db.py             # SQLite + FTS5 catalogue + docmeta (AI insights) + prefs
│  ├─ discover.py       # safe recursive walker + hashing
│  ├─ extract.py        # 25+ format extractors
│  ├─ categorize.py     # extension → category taxonomy
│  ├─ scan.py           # incremental scanning, worker thread, progress
│  ├─ search.py         # FTS5 query builder
│  ├─ translate.py      # cached translation
│  ├─ export.py         # CSV / TXT export
│  └─ watch.py          # fingerprint-polling folder watcher
└─ ui/
   ├─ theme.py          # dark “deep-space” theme + widget kit
   ├─ icon.py           # runtime-generated brand icon (splash, tray, installer)
   ├─ bootstrap.py      # branded splash + first-run onboarding wizard
   ├─ mainwindow.py     # sidebar navigation, library, search, AI, dupes, activity
   ├─ dialogs.py        # Settings / AI Models / Translate / Duplicates / About
   └─ tray.py           # system tray icon + silent background notifications
```

- **Thread safety:** workers only enqueue to a `queue.Queue`; the GUI main loop
  drains it. No Tk calls from worker threads (deadlock-safe).
- **SQLite FTS5 `unicode61`** over name/path/content with BM25 + `snippet()`.
- **AI store:** `docmeta` table holds summaries, tags, notes, favourites and
  packed float embedding vectors (cosine search in Python, batch-stored).
- **Incremental diffing** keyed on size+mtime; deletions pruned per root.

---

## Tests

```powershell
python -X utf8 tests\test_engine.py   # scan, search, dedup, extraction
python -X utf8 tests\test_ai.py       # AI service: summary/tags/embeddings/RAG (stubbed client)
python -X utf8 tests\test_gui.py      # GUI: window boots, indexes, searches, closes
```

---

## Roadmap

- [x] AI summaries, smart tags, embeddings, semantic search, assistant with citations
- [x] Branded splash + onboarding + system tray + professional installer
- [ ] OCR installer helper + lazy model downloads
- [ ] Local (offline) translation via lightweight models
- [ ] Headless CLI (`droplens scan/search/export`) for automation & CI
- [ ] “Pack” folders into a single searchable archive
- [ ] macOS/Linux builds

---

## License & commercial use

MIT License — use it, fork it, sell it, ship it. See
[`LICENSE`](LICENSE) and [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).

Icons: project-generated (Pillow) — no external assets to license.