# DropLens — Project Architecture

**Version:** 2.2.0 · **Runtime:** Python 3.10+ · **UI:** Tk/tkinterdnd2 · **Store:** one SQLite file (FTS5)

This file is the end-to-end map of the framework: what each layer owns, how a file
becomes an answer, and where the boundaries are. It reflects the code as it stands
today, including the 2.1.0 retrieval/queue work and the 2.2.0 extension, tool and
agent layers. When this document and the code disagree, the code wins — then fix
this document.

---

## 1. What DropLens is

A local-first desktop application that indexes folders of documents into a single
SQLite catalogue and lets you search, deduplicate, translate, enrich and *ask
questions about* them — with citations — through a GUI, a headless CLI, or an
AI that calls read-only tools over your library.

Four constraints shape every architectural decision:

| Constraint | What it means in code |
|---|---|
| **Read-only by construction** | Nothing in the app writes to indexed source files. Extraction produces an index copy; deletions cascade only inside `catalog.db`. |
| **One file is the whole state** | `catalog.db` + `settings.json`. No server, no daemon, no cloud dependency. Migrations are additive and versioned by `PRAGMA user_version`. |
| **Graceful degradation** | Every heavy dependency (PyMuPDF, python-docx, Tesseract, an embedding model) is optional and fails soft. The app boots and works without any of them. |
| **No import cycles** | A strict downward-only dependency ladder (§3). Upper layers import lower layers, never the reverse. |

---

## 2. High-level view

```
                       ┌──────────────────────────────────────────────┐
   entry points        │  main.py (GUI)      cli.py (headless)        │
                       └───────────┬──────────────────┬───────────────┘
                                   │                  │
        ┌──────────────────────────▼──────────────────▼───────────────┐
        │  presentation     ui/ (Tk pages, dialogs, tray, splash)     │
        ├─────────────────────────────────────────────────────────────┤
        │  services         ai/svc.py  ·  ai/jobs.py  ·  ai/agent.py  │
        │                   tools.py  ·  extensions/  ·  net.py       │
        ├─────────────────────────────────────────────────────────────┤
        │  provider edge    ai/client.py  ·  ai/providers.py          │
        ├─────────────────────────────────────────────────────────────┤
        │  engine           engine/scan · extract · chunk · search    │
        │                   db · migrations · discover · export …     │
        ├─────────────────────────────────────────────────────────────┤
        │  platform         config.py · resources.py · secure.py      │
        └─────────────────────────────────────────────────────────────┘
                                     │
                          SQLite + FTS5 (catalog.db)
```

Data always flows **down** into storage and **up** out as rows, `Hit`s and
`str` results. Worker threads never touch the Tk main loop directly (§11).

---

## 3. Layering and dependency rules

The import ladder, from the bottom up — each row may import only rows below it:

```
1.  config.py · resources.py · net.py · secure.py          (platform)
2.  engine/*                                               (indexing + storage)
3.  ai/providers.py · ai/client.py                         (wire formats)
4.  tools.py                                               (imports engine only)
5.  extensions/                                            (imports tools + engine)
6.  ai/agent.py · ai/svc.py · ai/jobs.py                   (imports client, tools, extensions)
7.  cli.py · ui/* · main.py                                (imports everything above)
```

Rules that keep it acyclic:

- **`tools.py` knows nothing about plugins.** It takes a `Catalog` and exposes
  built-in read-only tools. The agent assembles the final toolbox.
- **`extensions/` may import `tools.py`, `ai/*` must not import `extensions/api`.**
  The service reaches plugins through `extensions.default()` (the singleton registry),
  never through plugin code directly.
- **Engine never imports AI.** Enrichment is pulled in by `ai/svc.py` and `ai/jobs.py`,
  which sit above the engine. `extract.py` only asks the *registry* (layer 5) for an
  extractor — and that call is mediated so the engine still works when plugins are off.
- **`Config.data_dir` delegates to `resources.data_dir()`.** One source of truth for
  the data folder (`DROPLENS_DIR` → `%LOCALAPPDATA%` → `~/DropLens`); the previous
  duplicated implementation was removed because it ignored `DROPLENS_DIR`.

---

## 4. Package layout

```
dropLens/
├─ main.py              entry point: crash-safe logging, splash → onboarding → window
├─ __main__.py          `python -m dropLens` → CLI when args, GUI when none
├─ cli.py               headless commands (search, scan, ask, tools, extensions, …)
├─ config.py            typed settings over settings.json (+ AI provider registry)
├─ resources.py         data-dir resolution, logging, DB/log paths, Tesseract discovery
├─ net.py               egress policy (offline-only mode) + outbound request hooks
├─ secure.py            OS-level sealing (DPAPI on Windows) for stored API keys
├─ tools.py             Tool / Toolbox + the four built-in catalogue tools
├─ extensions/
│  ├─ registry.py       Extension records, extractor/tool/hook registry, plugin loader
│  └─ api.py            ExtensionAPI — the only surface a plugin is given
├─ ai/
│  ├─ providers.py      provider presets (Ollama, OpenAI-compatible, Anthropic, Gemini)
│  ├─ client.py         HTTP clients + provider converters (chat, chat_tools, embed)
│  ├─ agent.py          bounded, provider-neutral tool-calling loop
│  ├─ jobs.py           durable enrichment queue runner (backoff, retry limit)
│  └─ svc.py            AIService: summaries, tags, embeddings, retrieval, ask, ask_agent
├─ engine/
│  ├─ db.py             Catalog: schema, CRUD, FTS search, chunks, jobs, egress log
│  ├─ migrations.py     versioned additive migrations (PRAGMA user_version, now v2)
│  ├─ discover.py       safe recursive walker (symlink/junction-safe) + file hashing
│  ├─ extract.py        25+ format extractors; plugin-first dispatch with fallback
│  ├─ chunk.py          page-aware paragraph chunking with char offsets
│  ├─ categorize.py     extension → category taxonomy
│  ├─ scan.py           ScanManager: incremental diff, parallel extraction, hooks
│  ├─ search.py         safe FTS5 query builder (quoted tokens, prefix, modes)
│  ├─ translate.py      translation with per-document cache
│  ├─ export.py         CSV / TXT catalogue export
│  └─ watch.py          debounced background folder watcher
└─ ui/
   ├─ theme.py          dark theme + widget kit (ttk styles)
   ├─ icon.py           brand icon generated at runtime (splash, tray, installer)
   ├─ bootstrap.py      branded splash + first-run onboarding wizard
   ├─ mainwindow.py     DropLensApp: sidebar navigation, library, search, AI, dupes, activity
   ├─ dialogs.py        Settings, AI Models, Translate, Duplicates, About
   └─ tray.py           system tray + silent background notifications
```

Top-level (non-package) files: `launcher.py` (dev launcher), `build.ps1` /
`build_installer.ps1` / `build/` (PyInstaller + Inno Setup), `installer/`,
`examples/plugins/`, `tests/`, `docs/`.

---

## 5. Entry points

| Entry | Trigger | Behaviour |
|---|---|---|
| `dropLens/main.py` | `droplens.exe` / `python dropLens/main.py` | configure logging → build `ConfigStore` → splash → onboarding on first run → `DropLensApp` window |
| `dropLens/__main__.py` | `python -m dropLens …` | routes to `cli.main(argv)`; with no arguments it starts the GUI |
| `dropLens/cli.py` | any subcommand | opens config + catalogue exactly as the GUI does (`_open_catalog`), runs, prints, closes |
| `launcher.py` | dev convenience | thin wrapper that starts the GUI |

**CLI surface** (every command also accepts `--json`):

```
droplens search  "term" [--mode all|name|path|content] [--category] [--limit]
droplens stats                                   library, chunk and queue statistics
droplens scan     PATH... [--full] [--quiet]     incremental indexing
droplens ask      "question" [--tools] [--steps N]   RAG answer, or tool-calling agent
droplens tools                                  list built-in + plugin tools
droplens extensions [--reload]                  loaded plugins, extractors, hooks, errors
droplens export   --out catalogue.csv           whole-catalogue CSV
droplens egress   [--limit N] [--clear]         what left this machine
droplens jobs     [--queue|--run|--status] [--retry-failed]   AI enrichment queue
```

---

## 6. Storage model

### 6.1 Data directory

`resources.data_dir()` resolves `<base>/DropLens`, where `<base>` is
`$DROPLENS_DIR` → `%LOCALAPPDATA%` (Windows) → `~`. It contains:

| File | Purpose |
|---|---|
| `catalog.db` | the entire index (SQLite + FTS5) |
| `settings.json` | `Config` dict (AI providers, retrieval knobs, UX prefs) |
| `droplens.log` | rotating log |
| `plugins/` | user plugins — Python files or packages (§9) |

`Config.data_dir` delegates to this same function, so GUI, CLI and plugin lookup
always agree on the folder.

### 6.2 Schema

`PRAGMA user_version = 2`; migrations in `engine/migrations.py` are **additive only**
and run inside the caller's transaction before the first commit.

**v1 (base, `engine/db.py::_SCHEMA`)**

| Table | Role |
|---|---|
| `roots` | indexed folders/files, `last_scan`, scan status |
| `files` | one row per discovered path: size, mtime, hash, category, kind, `status`, `dup_id`, `scanned_at` |
| `docs` | **FTS5** external-content-less index over `doc_id, name, path, content` (`unicode61`) — this is what `search` queries |
| `docmeta` | derived AI state: summary, tags, notes, favourite, document embedding + the `content_hash` they were computed from (stale derived data is dropped when the file changes) |
| `translations` | per-`(doc_id, lang)` cached translation text |
| `prefs` | small key/value catalogue state |

**v2 (`engine/migrations.py`)**

| Object | Role |
|---|---|
| `chunks` | page-aware retrieval windows: `doc_id, ord, page, start_char, end_char, text, token_est` |
| `chunks_fts` | FTS5 over `chunks.text`, kept in sync by **triggers** (`ai/ad/au`) |
| `chunk_vectors` | embeddings for those chunks, `chunk_id` PK — deliberately separate from FTS rows so re-embedding never rewrites index rows |
| `ai_jobs` | durable enrichment queue: `UNIQUE(doc_id, kind)`, `state`, `attempts`, `next_at`, `last_error`, `content_hash` |
| `egress` | transparency log: every outbound request (kind, target, chars, doc) |
| `docmeta` + | `content_hash`, `embed_model`, `embed_dim`, `chunk_hash` columns |

Indexes: `files(root|hash|size|category)`, `chunks(doc_id)`, `chunks(doc_id, ord)`,
`chunk_vectors(doc_id)`, `ai_jobs(state, next_at)`, `egress(ts)`.

### 6.3 Invalidation rules

- **Scan skip:** unchanged `size + mtime` (±1e-6) → not re-extracted.
- **Deletions:** a root's missing paths are pruned and cascade to `docs`, `chunks`,
  `chunk_vectors`, `translations` and `ai_jobs`.
- **Derived AI data:** `docmeta.content_hash` / `chunk_hash` must match the current
  file's hash, or the summary/tag/embedding is considered stale and recomputed.
- **Duplicates:** `dup_id` = content hash; hashing is only attempted for files whose
  byte size collides with another file in the same scan (cheap prefilter).

---

## 7. Indexing pipeline (scan)

```
ScanManager.scan_roots(roots, incremental)
  └─ background thread, one root at a time, progress callbacks → queue
     ├─ extensions.load_from_config(cfg)          plugins register extractors/tools/hooks
     ├─ extensions.fire("scan_start", {root, incremental})
     ├─ discover: safe recursive walk → FileInfo batches (folders too)
     ├─ incremental diff vs existing rows         skip unchanged (§6.3)
     ├─ ThreadPoolExecutor("droplens-extract")    parallel extract_content per file
     │    └─ extract.py dispatch (§8)
     ├─ duplicate prefilter (size collision → hash)
     ├─ flush() every ≤400 files:
     │    upsert_file → save_doc(content, content_hash, status)
     │    extensions.fire("document_indexed", {doc_id, name, path, chars, status})
     │    commit
     └─ extensions.fire("scan_complete", …)
```

- **Folders are documents too:** a folder path is indexed with a generated
  `[folder listing: …]` body so directory structure is searchable.
- **Cancellable:** `cancel()` sets an event checked per file; the scan reports
  `cancelled` instead of pretending completion.
- **Status is honest:** `indexed`, or an error status when extraction failed — a
  failed file never silently looks indexed.
- **Watcher (`engine/watch.py`)** optionally re-runs the same pipeline on a debounce
  interval when `watch_enabled` is on.

---

## 8. Content extraction

`extract_content(path, ocr_enabled, tesseract_path)` returns text or `None`
(nothing extractable), or raises to signal failure — the caller records status.

Dispatch order:

1. **Plugin extractors first.** The extension registry is asked for every extension
   registered for the file's extension (lower-case, no dot). If a plugin returns a
   string, that is the document's text. If it returns `None` (declines) or raises,
   control falls through.
2. **Built-in extractor.** 25+ formats: text/code with charset detection
   (`charset_normalizer`), PDF (PyMuPDF), DOCX/PPTX/XLSX, ODT, RTF, EPUB, DJVU,
   archives (contents listing), e-mail, legacy binary Office, images via OCR
   (Tesseract, optional), plus folder listings.

Every heavy import is wrapped in `try/except ImportError`; a missing library
degrades that one format, not the app. Text is capped by `max_content_chars`
(default 5,000,000 chars per file) to keep the index bounded.

`chunk.py` then splits extracted text into **page-aware paragraph windows**
(`chunk_chars` ≈ 3200 chars, `chunk_overlap` ≈ 0.15, page boundaries preserved via
`PAGE_MARK`), each carrying exact `start_char`/`end_char` offsets — that is what makes
citations land on a real page and offset instead of a guessed line number.

---

## 9. Extension framework

**Where:** `<data_dir>/plugins/*.py` or a package directory containing `__init__.py`.
**Switch:** `plugins_enabled` (default `True`). Loading is idempotent per directory;
failures are recorded, never raised.

A plugin exposes module constants plus one entry point:

```python
NAME, VERSION, DESCRIPTION = "CSV tables", "1.0", "table files as rows"
def register(api): ...
```

`ExtensionAPI` is deliberately tiny — three verbs:

| API | Purpose |
|---|---|
| `api.register_extractor(exts, fn, replace=False)` | `fn(path) -> str | None`; collision with another *plugin* needs `replace=True`, built-in collisions never do |
| `@api.tool(name, description, parameters)` | register a tool the assistant may call |
| `@api.on("scan_start" \| "document_indexed" \| "scan_complete")` | observe the scan lifecycle (payload dict, optional) |

Registry records every plugin as an `Extension` with its extractors, tools, hooks
and any load/registration error, so `droplens extensions` reports *why* something
did not load. Hook exceptions are logged and swallowed — a broken plugin can slow a
scan but cannot break it. The API exposes **no** catalogue handle and no network
helper; plugins see the path they were given and nothing else.

Worked example: `examples/plugins/csv_tables.py` (`.csv`/`.tsv` extractor,
`csv_columns` tool, `document_indexed` hook). Full guide: [`docs/EXTENSIONS.md`](docs/EXTENSIONS.md).

---

## 10. Retrieval and the AI layer

### 10.1 Hybrid retrieval (`AIService.retrieve`)

```
question
 ├─ engine/search.build_match → quoted, prefix-expanded FTS5 expression
 │    → catalog.search_chunks(...)              rank A  (BM25 over chunks_fts)
 ├─ embed_query(question) → cosine over chunk_vectors   rank B (skipped if the
 │    provider has no embeddings or is unreachable — the log says so, no error)
 └─ RRF fusion (k) → top `retrieve_top` (8) of `retrieve_candidates` (60) per channel
      → list[Hit]  {chunk_id, doc_id, name, path, page, start_char, end_char, text, score}
```

Missing rows for fused ids are back-filled from `chunks_by_ids`, so a chunk found by
only one channel still arrives with its provenance. Everything downstream —
`ask`, `ask_agent`, the `read_document`/`search_library` tools — consumes the same
`Hit` shape, so citations are uniform.

### 10.2 Two answer paths

| | `ask(question)` (default) | `ask_agent(question, …)` (`ask --tools`) |
|---|---|---|
| Mechanism | single RAG prompt: top chunks as labelled blocks, per-chunk budget from `prompt_char_budget` | `ai/agent.py` loop: model may call tools, results return as tool messages, **final turn is issued with `tools=[]`** so prose always comes back |
| Round bound | 1 call | `max_steps` (default 4) |
| Citations | parsed from the answer against the retrieved hits (`_cites_from_answer`) | chunk ids the tools actually returned (`_cites_for_ids`) — a model cannot invent a page |
| Output | `(answer, cites)` | `(answer, cites, steps)` |

Both are wrapped in `_busy_guard`, keyed per question, so double-submits collapse
instead of racing.

### 10.3 Provider edge (`ai/providers.py`, `ai/client.py`)

- Presets for Ollama (local) and cloud OpenAI-compatible, Anthropic and Gemini
  endpoints, including base URL, API-key slot, and whether embeddings are supported.
- **One normalized message shape** in `client.py`; per-provider converters emit
  OpenAI `tools`/`tool_calls`, Anthropic `tool_use`/`tool_result`, or Gemini
  `functionCall`/`functionResponse`. Same agent loop for all three.
- `chat`, `chat_tools`, `embed` are the only outbound AI primitives; `MAX_CHAT_CHARS`
  (400k) clips oversized context before the request.
- Every request passes through `net.py`: in `offline_only` mode only loopback
  destinations are allowed, and (when a sink is configured) the request is recorded in
  the `egress` table — `droplens egress` shows what left the machine.

### 10.4 Enrichment queue (`ai/jobs.py`)

`ai_jobs` is the durable queue for summaries, tags, embeddings and translations:
state machine `queued → running → done|failed`, `attempts` capped by `ai_retry_limit`
(3), `next_at` backoff, `content_hash` recorded so a job for an unchanged file is
skipped. `AIService.index_ai` / `run_jobs` drain it with progress callbacks; the GUI
runs it in the background and the CLI exposes it via `droplens jobs`.

### 10.5 Built-in tools (`tools.py`)

`Tool` = name + description + JSON Schema + handler returning
`(text, parsed)`. Handler exceptions become an `{"error": …}` payload handed back to
the model; they never propagate into the worker thread. `Toolbox` de-duplicates by
name with first registration winning.

| Tool | Reads | Policy |
|---|---|---|
| `search_library` | chunk FTS (+ file-row fallback when a library has no chunk index) | returns citable `chunk_id`s |
| `read_document` | one document's stored text | windowed with page, offsets, continuation token; `MAX_READ_CHARS` 6000 |
| `library_stats` | counts, categories, queue states | aggregate only |
| `list_documents` | file rows | name/path/size filter, bounded page |

`MAX_RESULT_CHARS` (8000) bounds what any tool can return per call. All built-ins are
strictly read-only: no writes, no deletions, no network, no plugin state.

---

## 11. GUI (`ui/`)

- **Pages:** `Library`, `Search`, `AI Assistant`, `Duplicates`, `Activity`, plus a
  `Settings` button opening the dialog stack (`dialogs.py`: Settings / AI Models /
  Translate / Duplicates / About).
- **Thread contract:** workers (scan, AI, export) never call Tk. They enqueue dicts
  into a `queue.Queue`; the main loop drains it. This is the rule that keeps the app
  deadlock-safe and it is why progress callbacks are plain dicts.
- **Branding:** `theme.py` (dark "deep-space" palette + ttk styles), `icon.py`
  (icon generated at runtime — no binary asset drift), `bootstrap.py` (splash +
  onboarding), `tray.py` (background notifications, close-to-tray).
- **Known gap:** the assistant's tool trace and plugin status are service/CLI only
  today; there is no GUI panel for them yet (open roadmap item).

---

## 12. Security, privacy and trust

- **Read-only toward user files** — see §1; the source is never modified.
- **Injection-safe FTS:** user text is tokenized, quoted and escaped by
  `engine/search.build_match`; no raw user string ever reaches a `MATCH` expression
  or `LIKE` clause (parameterized SQL everywhere else).
- **Egress policy:** `net.configure(offline_only, sink=catalog.log_egress_entry)` —
  offline mode blocks every non-loopback destination; otherwise calls are logged to
  `egress` for inspection.
- **Key storage:** `secure.py` seals API keys with OS-level protection (DPAPI on
  Windows) rather than leaving them in plaintext JSON.
- **Walker safety:** `discover.py` does not follow symlinks/junctions out of a root,
  and `ignore_names` skips VCS/cache/recycle directories.
- **Plugin trust model:** plugins are user-installed Python running in-process — the
  API surface is minimal (no catalogue, no network helper), but they are code the user
  chose to install, not sandboxed code.

---

## 13. Configuration

`config.py` holds one `DEFAULTS` dict (deep-copied) wrapped by a typed `Config`.
Groups:

| Group | Keys (defaults) |
|---|---|
| Location | `data_dir` ("" → resolved at runtime) |
| Indexing | `threads=4`, `hash_duplicates=True`, `max_content_chars=5M`, `ignore_names=[…]` |
| OCR | `ocr_enabled=True`, `tesseract_path=""` |
| Watcher | `watch_enabled=False`, `watch_interval=15`, `watch_debounce=3` |
| Retrieval | `chunk_chars=3200`, `chunk_overlap=0.15`, `retrieve_candidates=60`, `retrieve_top=8`, `embed_batch=32`, `semantic_default=False` |
| AI queue | `ai_retry_limit=3`, `ai_auto_enrich=False` |
| Privacy | `offline_only=False`, `plugins_enabled=True` |
| AI registry | `ai={}` (provider presets + active selection) |
| UX | `tray_enabled`, `close_to_tray`, `notify_scan_done`, `onboarded`, `default_lang`, `preview_chars` |

Every field has a working default; unknown/older `settings.json` files merge forward.

---

## 14. Build and distribution

| Artifact | Command |
|---|---|
| `DropLens.exe` | `powershell -ExecutionPolicy Bypass -File .\build.ps1 -Clean -Version 2.2.0` |
| `Setup-DropLens.exe` | `powershell -ExecutionPolicy Bypass -File .\build_installer.ps1 -Version 2.2.0` |

Runtime deps live in `requirements.txt` (PyMuPDF, python-docx, python-pptx, openpyxl,
charset-normalizer, requests, tkinterdnd2, Pillow, pystray); build-only deps in
`requirements-build.txt` (PyInstaller). Packaging details: `build/BUILD.md`,
`installer/DropLens.iss`, signing guidance in `docs/COMMERCIAL.md`.

---

## 15. Tests

```
python -X utf8 tests/test_engine.py       scan, search, dedup, extraction, CSV export
python -X utf8 tests/test_export.py       CSV/TXT export + cascade delete (regression)
python -X utf8 tests/test_retrieval.py    chunking, hybrid RRF, citations, invalidation, queue
python -X utf8 tests/test_ai.py           summaries/tags/embeddings/RAG (stubbed client)
python -X utf8 tests/test_extensions.py   plugin discovery, extractor precedence, hooks, config switch
python -X utf8 tests/test_tools.py        read-only tools, agent loop, citations, provider converters
python -X utf8 tests/test_gui.py          GUI boot, index, search, close (needs Tk)
```

Style: plain-`assert` scripts runnable with no test runner and no network — the AI
tests stub the HTTP layer, the extension/tool tests inject a stub model. Expected
failure injections intentionally print to stderr (429 retry log, broken-plugin
warnings); the scripts still exit 0.

**Current status:** all scripts except `test_gui.py` pass in a headless Linux VM
(`compileall` clean). `test_gui.py` needs Tk + tkinterdnd2, and the Windows-only
paths (DPAPI, Explorer integration, installer) are not exercised off-Windows.

---

## 16. Documentation map

| Document | Read it for |
|---|---|
| [`README.md`](README.md) | quick start, feature list, tests, roadmap |
| [`read.md`](read.md) | this file — the architecture |
| [`docs/EXTENSIONS.md`](docs/EXTENSIONS.md) | writing plugins and tools |
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | end-user workflows |
| [`docs/IMPLEMENTATION_NOTES.md`](docs/IMPLEMENTATION_NOTES.md) | what changed in 2.1.0/2.2.0 and why |
| [`docs/CUSTOMER_OFFERING.md`](docs/CUSTOMER_OFFERING.md) | the customer promises and what we must not claim |
| [`docs/ENHANCEMENT_AND_INTEGRATION_ROADMAP.md`](docs/ENHANCEMENT_AND_INTEGRATION_ROADMAP.md) | the improvement ladder (E1–E10) and integration horizon |
| [`docs/ARCHITECTURE_REVIEW.md`](docs/ARCHITECTURE_REVIEW.md) | known issues found in review |
| [`docs/COMMERCIAL.md`](docs/COMMERCIAL.md) | licensing and commercial distribution |

---

## 17. Known gaps and deliberate non-goals

- No GUI surface yet for tool traces or plugin status (CLI/service only).
- Plugins run in-process without a sandbox — trusted, user-installed code by design.
- Gemini's `functionResponse` converter is unit-verified against the converter
  contract but not against a live API from this repo.
- No macOS/Linux packaging; no server, sync or multi-user mode — out of scope for
  the local-first product.

The full history of what was asked for and what was recommended — with each item's
status re-verified against the code — is in §18 below.

---

## 18. Inputs and recommendations — integrated register

This section consolidates **every input this project has received** and **every
recommendation its review documents produced**, with a status re-checked against the
code (grep, `compileall`, and the six runnable test scripts) when this section was
written. Source documents: `docs/ARCHITECTURE_REVIEW.md` (§9 defects, §11 plan),
`docs/ENHANCEMENT_AND_INTEGRATION_ROADMAP.md` (E1–E10), `docs/IMPLEMENTATION_NOTES.md`
(§5 deferred, §7 extension decisions), `docs/CUSTOMER_OFFERING.md` (§7 claims, §8 next).

Status vocabulary: **done** (in code and covered by a check) · **partial** · **open**
(not in code) · **deferred** (deliberately, with a recorded reason).

### 18.1 Inputs received

| # | Input | Where it landed |
|---|---|---|
| 1 | *“Implement all findings and actionables, then we will improvise”* | 2.1.0: schema v2 (§6.2), hybrid retrieval (§10.1), durable queue (§10.4), headless CLI (§5), trust surface (§12) — file-level detail in `IMPLEMENTATION_NOTES.md` §1–4 |
| 2 | *“Correct the code, fix the architecture, integrate and enhance tools, extensions, plugins and the AI interface”* | 2.2.0: layering rules (§3), extension framework (§9), tools (§10.5), agent loop (§10.2), provider converters (§10.3), `ask --tools` CLI |
| 3 | *“Create read.md and update the complete project architecture”* | §1–§17 of this file, linked from the README architecture section |
| 4 | *“Recall everything — all inputs and recommendations — and integrate”* | §18 (this section), the two config cleanups closed while verifying it, and the H1/H2 fixes that verification led to |
| 5 | **Standing constraints:** no new hard dependencies · assert-style tests, no pytest · one SQLite file · the Windows/Tk app must keep working · no commit/push unless asked | `requirements.txt` untouched; §15 test style; §6 storage model; §11 thread contract preserved; all work remains uncommitted |

### 18.2 Architecture review — defect and plan status

From `docs/ARCHITECTURE_REVIEW.md` §9 (defects B/H/M) and §11 (phases 0–10). Rows
marked **done now** were closed during this integration pass.

| Review item | Status | Evidence in this tree |
|---|---|---|
| **Phase 0 — B1–B5** `sqlite3.Row` used with `.get()` (export, `index_ai`, preview, favourite, notes) | **done** | `row_get()` defined in `engine/db.py` and used across `export.py`, `ai/svc.py`, `ui/mainwindow.py`; no `.get()` on rows remains at the five reported sites; export covered by `tests/test_export.py` |
| **Phase 1 — H3** `mark_deleted` orphan rows | **done** | cascade deletes `files → docs → chunks → chunk_vectors → docmeta → translations → ai_jobs` (covered by `tests/test_export.py`); no `VACUUM` path yet |
| **Phase 1 — H4** rowid reuse attaching stale metadata | **done** | `files.id INTEGER PRIMARY KEY AUTOINCREMENT` |
| **Phase 1 — H5** no migration mechanism | **done** | `engine/migrations.py`, additive only, `PRAGMA user_version = 2` (§6.2) |
| **Phase 2 — H1** “Load more” pagination inconsistency | **done now** | `ResultCursor` (`engine/search.py`) pages over a resolved id list fed by both channels — every id served exactly once in a stable order; wiring in `_do_fts_search`; unit-covered in `tests/test_engine.py` |
| **Phase 2 — H2** semantic search ignores the category filter | **done now** | `Catalog.file_ids(category, root)` filters the vector channel of `AIService.retrieve` and `semantic_search(category=…)`; the window passes the selected category through; covered in `tests/test_retrieval.py` §5b |
| **Phase 2 — M1** N+1 `get_meta` per result row | **mostly done** | the ★ marker now reads `row_get(r, "favorite")` from the row itself; 3 `get_meta` call sites remain in the window |
| **Phase 3 — H6/H7/H8** dead settings | **done** | `max_content_chars` → `set_max_content(...)`, `threads` → `ThreadPoolExecutor(max_workers=...)`, `tesseract_path` falls back to `resources.find_tesseract()` — all in `engine/scan.py` |
| **Phase 4 — H9** `.ico` decoded as text | **done** | `ico` is no longer in `_TEXT_EXTENSIONS`; it reaches the image/OCR path |
| **Phase 4 — H9** `.svgz` treated as plain text although gzip-compressed | **done now** | dropped from `_TEXT_EXTENSIONS`; `_extract_gzip_text` gunzips it into searchable SVG text; asserted in `tests/test_engine.py` |
| **Phase 4 — H10** 7Z/RAR claims | **done now** | `py7zr` and `rarfile` are declared in `requirements.txt` and passed to PyInstaller as hidden imports, so the shipped exe can list 7Z/RAR; compressed-RAR *extraction* still needs an `unrar`/`bsdtar` binary, which is documented rather than shipped |
| **Phase 5 — H11** watcher re-walks the tree every interval | **open** | `engine/watch.py` unchanged; native events belong to E5 (see §18.3) |
| **Phase 6 — M5** shared mutable `DEFAULTS` copy | **done** | deep copy via `json.loads(json.dumps(DEFAULTS))` |
| **Phase 6 — M6** silent `settings.json` write failure | **done now** | `ConfigStore.save` logs `could not save settings to …` instead of `except OSError: pass` |
| **Phase 7 — M9** API keys in clear text | **done** | `secure.seal_field` / `open_field` (DPAPI, `dpapi:` prefix) applied in `ai/providers.py` serialization |
| **Phase 8 — M7** blanket swallowed exceptions | **partial** | 17 `except …: pass` sites remain; only the settings-save path was converted to logging in this pass |
| **Phase 3 — M3** no per-file extraction timeout | **open** | no timeout exists in `scan.py`/`extract.py`; only the `max_content_chars` size cap |
| **Review H12** auto-enrichment uncancellable | **done** | `_auto_ai_enrich` now uses `stop=lambda: self._ai_stop_flag` |
| **Phase 9** `DropLensApp` god object | **open** | `ui/mainwindow.py` is 1,638 lines (review measured 1,552) — decomposition plan in §11 of the review still stands |
| **Phase 10** pinned deps, CI, signed binaries | **open** | no `.github/`, `requirements.txt` uses ranges, no signing step |
| **§9.4 dead surface** (`_common_prefix`, `search_count`, `extensions_top`, `upsert_doc`, `pending_inserts_commit`, `cleanse_snippet`, `Tray.hide`) | **done** | all greps return zero remaining references |

### 18.3 Roadmap status (E1–E10)

| Item | Status | Note |
|---|---|---|
| **E1** trust foundation | done | row boundaries, migrations, cascade deletes, DPAPI, dead settings wired or removed |
| **E2** chunked hybrid retrieval | done | `engine/chunk.py`, `chunks`/`chunks_fts`/`chunk_vectors`, RRF fusion, page+offset citations; reranker deliberately not wired (§18.4) |
| **E4** durable enrichment queue | done | `ai_jobs`, backoff, resumable, cancellable, `content_hash` invalidation |
| **E7** programmable surface | done | CLI (2.1.0) **plus** tools, plugins and the agent loop (2.2.0) — §5, §9, §10 |
| **E5** always-fresh index | partial | debounced watcher + per-file re-extraction; native `ReadDirectoryChangesW`/`watchdog` still to do |
| **E8** trust layer | partial | offline switch, egress log, DPAPI keys done; redaction-before-send and signed binaries open |
| **E3** content reach (audio/video/media) | open — **deferred** | needs new optional dependencies and a model-download path (§18.4) |
| **E6** near-duplicate intelligence | open — **deferred** | needs MinHash/imagehash; schema has room, nothing added prematurely |
| **E9** portability (encrypted snapshot) | open — **deferred** | key-management decision belongs with distribution work |
| **E10** distribution & commerce | open — **deferred** | signing, winget/Scoop, auto-update, licensing are release-engineering tasks |

Known ceiling recorded with the roadmap: chunk vectors are brute-forced in Python
(≈50k chunks comfortable); `sqlite-vec` is the answer beyond that.

### 18.4 Deliberately deferred, with reasons

| Deferred item | Reason recorded in `IMPLEMENTATION_NOTES.md` §5 / §7 |
|---|---|
| E3 media reach (Whisper, CLIP, Docling) | each needs a new optional dependency plus a model-download path; shimming would fake capability — they land in `extract.py`'s optional-import discipline |
| E6 near-duplicate groups | needs MinHash/imagehash and duplicate *group* UI; adding it without UI only grows the DB |
| E9 encrypted snapshots | needs a key-management decision (age/libsodium) tied to distribution |
| E10 distribution/commerce | release engineering, not code in this tree |
| Cross-encoder reranking | marginal gain versus shipping an ONNX runtime; insertion point is `AIService.retrieve` |
| `watchdog` native filesystem events | the debounced poller already coalesces bursts; native events change the dependency surface and need Windows validation |
| GUI controls for tools and plugins | 2.2.0 shipped the service + CLI first; the GUI panel is the open roadmap item in §17 |
| Plugin sandboxing | plugins are user-installed code; the API is deliberately tiny (no catalogue handle, no network helper) rather than sandboxed |

### 18.5 Claim discipline (carried from `CUSTOMER_OFFERING.md` §7)

**Claims we may make:** paragraph-level citations with page numbers · hybrid keyword +
semantic retrieval · resumable, cancellable enrichment · offline-only mode with an
egress log · API keys sealed with Windows DPAPI · CLI with JSON output · a plugin and
tool surface that loads user extensions.

**Claims we must not make yet:** any millisecond or throughput number · “handles
millions of chunks” · “finds audio and video” · “detects near-duplicates” · “signed
installer” · “enterprise SSO”. Anything in §18.2 still **open** or §18.3 still **open**
is a claim we cannot make either.

### 18.6 Integration order — what to do next, ranked

1. **Search correctness (user-visible):** ~~H1 resolved-id pagination cursor and H2
   category filtering on the semantic path~~ — **done**, see §18.2. Everything below is
   still untouched.
2. **Truth fixes:** ~~drop `svgz` from the text path or gunzip it; declare
   `rarfile`/`py7zr`~~ — **done**, see §18.2 (with the single release version stamp
   so the exe and installer cannot mislabel a build). Remaining here: convert the
   silent `except: pass` sites to logged warnings (M7).
3. **Surface the 2.2.0 layer in the GUI:** tool-call trace and plugin status panel
   (§17 gap).
4. **Performance/robustness:** watcher cost (H11/E5) and a per-file extraction timeout (M3).
5. **Structure:** extract services/pages out of `DropLensApp` (review §11 phase 9), typed
   queue message instead of raw dicts.
6. **Delivery:** pin dependencies, add a Windows CI job running `compileall` + the test
   scripts, sign both executables (phase 10).
7. **Product next, in `CUSTOMER_OFFERING.md` §8 order:** media reach → near-duplicate
   groups → measured retrieval quality → agent surface (MCP/loopback API) → trust and
   commerce.
