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
| 🤖 **AI understanding of your data** | Auto **summaries**, **smart tags**, **embeddings**, an **AI assistant that answers questions about your library and cites the exact passage**, and **folder reports**. |
| 🎯 **Paragraph-level hybrid retrieval** | BM25 over chunk indexes **fused** with chunk embeddings (Reciprocal Rank Fusion); answers cite `[#42] report.pdf · p.3` and the preview jumps to that passage. |
| 🖥 **CLI + egress log** | `python -m dropLens search|scan|ask|stats|export|jobs` with `--json`; an **offline-only** switch and a “what leaves this machine” log. |
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
- **Respan Gateway (embedded):** the *Respan Gateway (local)* provider sends every call through an in-process, loopback-only LLM gateway — routes, fallback and provider translation live in one `gateway.toml`, and the model field takes a route name (`smart`) or `provider/model`. Keys stay in Settings or in `env:NAME` references.
- Add presets under **Settings → AI Models**, hit **Test connection**, and pick an embedding model for semantic search (e.g. `nomic-embed-text`, `text-embedding-3-small`).
- **AI summary** per document (cached), **auto tags** (clickable chips), **favourites & notes**, **related files** (embedding similarity).
- **AI Assistant** answers questions using only your indexed files and **cites the exact sources**.
- **AI-enrich library** generates summaries/tags/embeddings for everything (smallest files first, cancellable) — optionally automatic after each scan.
- No data leaves your machine unless you configure an online provider; local providers run fully offline.

### Respan Gateway (embedded, loopback only)

`respan-gateway==0.3.0` runs **inside** DropLens (`dropLens/ai/gateway.py`): it starts
`respan_gateway.Gateway().serve(port=0)` on `127.0.0.1` and the `gateway` provider kind
resolves that URL per request. Summaries, tags, embeddings, tool calling and the
assistant keep their existing client — only routing, fallback and provider translation
move into one configuration file. Guide: [respanai/respan-gateway](https://github.com/respanai/respan-gateway?utm_source=freebuff&utm_medium=paid&utm_campaign=respan-gateway&bfcid=bfc_1.eyJpZCI6IjlmOGQyYzYyLTYwZjAtNDcxNC04YTRlLWY5MmI4NDk4ODY5MSIsImMiOiIyMDM2ODZkZS1mMzg0LTQ4NTQtYmViNC1hYzBiYWRmNmM1NTUiLCJrIjoiM2IxMGE4YmUtNzgyOS00MmJhLWFmNmEtYmI0YWUxZTRhY2Y4IiwiaWF0IjoxNzkzNDM2MDY0fQ.fL2Dz1C-E1VcSgzJUIuc047M-s1R1huNKjoheyPPBno).

1. Install the pinned wheel: `pip install -r requirements.txt` (wheels for Linux/macOS x64+arm64, Windows x64; Python 3.10+).
2. Describe where models go in `<data folder>/gateway.toml` (or point `DROPLENS_GATEWAY_CONFIG` at a file):

   ```toml
   log = "text"
   [providers.ollama]              # vendor keys, or any OpenAI-compatible base_url
   vendor = "ollama"
   base_url = "http://localhost:11434"
   [routes.smart]                  # what the app asks for
   to = ["ollama/llama3.1", "openai/gpt-4o-mini"]
   tries = 2
   ```

3. **Settings → AI Models → Add preset → Respan Gateway (local)**; set **Model** to a route
   (`smart`) or `provider/model`, keep the key empty (or paste the gateway's client key if
   `gateway.toml` declares `client_keys`), and **Test connection**.
4. Run the app as usual: `python launcher.py` (or `python -m dropLens`).

**Observed result:** `python -X utf8 tests/test_gateway.py` drives chat completions, tool
calling, embeddings, `/v1/models` and the connectivity probe through the embedded gateway
to a local OpenAI-compatible fixture — 4 fixture requests, all assertions pass, no account,
key or deployment involved. Live provider calls were **not** verified here (no provider
credentials in this environment); they run in your normal app setup with your own keys.

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
| Archives | ZIP · TAR · GZ · BZ2 · XZ · 7Z · RAR (content listing; 7Z/RAR extractors bundled) |
| Images | OCR when **Tesseract** is installed (auto-detected) |

### Duplicates, Translation, Auto-watch
- Duplicates by **content hash** with a size-collision short-circuit (hashes only colliding sizes — fast on thousands of files).
- Translation of any result into 70+ languages, cached in the DB, exported as `.txt`. Source never altered.
- **Auto-watch** re-indexes changed folders incrementally by size+mtime fingerprint.

### Extensions, tools and the AI interface
- **Extensible by file** — drop a Python file into `<data folder>/plugins/` to add an
  extractor for a new format, a tool the assistant can call, or a hook that runs after a
  document is indexed. A broken plugin is listed with its error and otherwise ignored.
- **Read-only tools** — `search_library`, `read_document`, `library_stats`,
  `list_documents`. The assistant can look things up; it can never change your files.
- **Two ways to ask** — `droplens ask` (one retrieval-grounded call) and
  `droplens ask --tools` (bounded tool-calling loop, at most 4 rounds, citations still
  resolved to real chunk ids).
- `droplens tools` and `droplens extensions` list what is available. Details in
  [`docs/EXTENSIONS.md`](docs/EXTENSIONS.md); worked example in
  [`examples/plugins/csv_tables.py`](examples/plugins/csv_tables.py).

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
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Clean -Version 2.2.0      # DropLens.exe
powershell -ExecutionPolicy Bypass -File .\build_installer.ps1 -Version 2.2.0   # Setup-DropLens.exe
```

> **For public/commercial distribution:** code-sign both exes (OV/EV cert) and
> pin dependency versions. Details in [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).

---

## Architecture

The complete framework architecture — layering rules, storage schema, indexing
pipeline, retrieval, AI/agent layer, extension system, trust model and known gaps —
lives in [`read.md`](read.md). The tree below is the short version.

```
dropLens/
├─ main.py              # entry point (splash → onboarding → window; crash-safe logging)
├─ config.py            # typed settings + persisted AI provider config
├─ resources.py         # paths, logging, Tesseract discovery
├─ ai/
│  ├─ providers.py      # local/cloud provider presets (Ollama, OpenAI, Gemini, …)
│  ├─ client.py         # OpenAI-compatible / Anthropic / Gemini clients (+ tool calling)
│  ├─ agent.py          # bounded, provider-neutral tool-calling loop
│  └─ svc.py            # summaries, tags, embeddings, semantic search, RAG assistant
├─ tools.py             # read-only catalogue tools + Toolbox (JSON schemas)
├─ extensions/          # plugin registry: extractors, tools, hooks
├─ cli.py               # headless search/scan/ask/tools/extensions/export/…
├─ net.py               # egress policy (offline-only) + outbound request log
├─ secure.py            # DPAPI sealing for API keys
├─ engine/
│  ├─ db.py             # SQLite + FTS5 catalogue, chunks, docmeta, jobs, prefs
│  ├─ migrations.py     # PRAGMA user_version schema migrations
│  ├─ chunk.py          # page-aware paragraph chunking with offsets
│  ├─ discover.py       # safe recursive walker + hashing
│  ├─ extract.py        # 25+ format extractors
│  ├─ categorize.py     # extension → category taxonomy
│  ├─ scan.py           # incremental scanning, parallel extraction, progress
│  ├─ search.py         # FTS5 query builder
│  ├─ translate.py      # cached translation
│  ├─ export.py         # CSV / TXT export
│  └─ watch.py          # debounced folder watcher
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
- **Retrieval:** `chunks` (+ `chunks_fts`, kept in sync by triggers) holds
  page-aware paragraph windows; `chunk_vectors` holds their embeddings (kept apart
  so re-embedding never rewrites the FTS rows). Both rankings are fused with RRF.
- **AI store:** `docmeta` holds summaries, tags, notes, favourites and the
  document-level vector plus the `content_hash` it was computed from, so stale
  derived data is dropped when a file changes. `ai_jobs` is the durable queue.
- **Incremental diffing** keyed on size+mtime; deletions pruned per root and
  cascaded to docs, chunks, vectors, translations and jobs.

---

## What customers get

See [`docs/CUSTOMER_OFFERING.md`](docs/CUSTOMER_OFFERING.md): the five customer promises, the
before → after improvement scope of 2.1.0 (retrieval depth, freshness, correctness, surface area)
and 2.2.0 (extensions, tools, agentic answers), and the explicit list of what we must **not**
claim yet.

## Tests

```powershell
python -X utf8 tests\test_engine.py     # scan, search, dedup, extraction, CSV export
python -X utf8 tests\test_ai.py         # AI service: summary/tags/embeddings/RAG (stubbed client)
python -X utf8 tests\test_retrieval.py  # chunking, hybrid RRF, chunk citations, invalidation, queue
python -X utf8 tests\test_export.py     # CSV/TXT export + cascade delete (regression)
python -X utf8 tests\test_extensions.py # plugin discovery, extractor precedence, hooks, config switch
python -X utf8 tests\test_tools.py      # read-only tools, agent loop, citations, provider converters
python -X utf8 tests\test_gateway.py    # embedded Respan Gateway: chat/tools/embeddings via loopback
python -X utf8 tests\test_gui.py        # GUI: window boots, indexes, searches, closes
```

---

## Roadmap

- [x] AI summaries, smart tags, embeddings, semantic search, assistant with citations
- [x] Branded splash + onboarding + system tray + professional installer
- [ ] OCR installer helper + lazy model downloads
- [ ] Local (offline) translation via lightweight models
- [x] Headless CLI (`python -m dropLens search/scan/ask/stats/export/jobs`) for automation & CI
- [x] Read-only assistant tools + bounded tool-calling agent (`ask --tools`)
- [x] Plugin system: extractors, tools and hooks from `<data folder>/plugins`
- [ ] Assistant tools and plugin status surfaced in the GUI (currently CLI/service only)
- [x] Chunk-level hybrid retrieval with page-level citations and a durable AI queue
- [ ] “Pack” folders into a single searchable archive
- [ ] macOS/Linux builds

---

## License & commercial use

MIT License — use it, fork it, sell it, ship it. See
[`LICENSE`](LICENSE) and [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md).

Icons: project-generated (Pillow) — no external assets to license.