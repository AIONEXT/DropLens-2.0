# DropLens 2.0 — Architecture Review

Review of the full source tree (`dropLens/`, `build/`, `installer/`, `tests/`) as shipped on the
`freebuff/7e494c6ba750d8e7447c29ba` branch.

> **Status (2.1.0).** The ceilings in §3 and the defects catalogued in §9 have since been
> addressed: `sqlite3.Row.get()` crashes, orphaned derived rows, dead settings, chunk-level
> hybrid retrieval with real citations, and a durable enrichment queue. See
> [`IMPLEMENTATION_NOTES.md`](IMPLEMENTATION_NOTES.md) for the file-by-file record. Everything
> below remains an accurate snapshot of the **2.0.0** tree and is kept as the baseline.

**Method.** Every Python module was read end-to-end (5,577 LOC across 26 files under
`dropLens/`; 5,961 including the tests and build scripts), plus the Inno Setup script and the
build docs. Claims about defects below were reproduced or verified
mechanically (details in §9). The test suite was **not executed**: it needs the Windows-only
runtime stack (`tkinterdnd2`, Tk, a windowing system), which is not installed in this
environment. Nothing here is based on running the GUI.

---

## 1. System at a glance

DropLens is a **single-process, offline-first Windows desktop application** written in Python
3.11–3.13. It is a Tkinter/Tk application (not a web app and not a service) that ships as a
PyInstaller one-file executable plus an Inno Setup installer.

| Dimension | Value |
|---|---|
| Language / runtime | Python 3.11–3.13 |
| UI toolkit | Tkinter + ttk, `tkinterdnd2` for drag & drop |
| Persistence | One SQLite database (`WAL`) with FTS5; JSON settings file |
| Concurrency | Threads + a single `queue.Queue` drained by the Tk main loop |
| External I/O | Optional HTTP: AI providers (OpenAI-compatible / Anthropic / Gemini API), Google Translate |
| Distribution | `dist\DropLens.exe` → `dist\Setup-DropLens-<v>.exe` |
| Total source | 5,577 LOC in `dropLens/`, 26 files (largest: `ui/mainwindow.py`, 1,552) |

The product thesis is: **read-only indexing**. Every file is copied into an index (`files` +
FTS5 `docs`); originals are never modified, moved, or deleted. Every feature is a read or a
derived artifact (summary, tag, embedding, translation) keyed by `doc_id`.

---

## 2. Module map and dependency graph

### 2.1 Layers

```
┌──────────────────────────────────────────────────────────────────────┐
│  Presentation — dropLens/ui/                                         │
│  mainwindow.py (1552)  dialogs.py (669)  bootstrap.py (153)          │
│  theme.py (104)  tray.py (89)  icon.py (66)                          │
├──────────────────────────────────────────────────────────────────────┤
│  Domain services — dropLens/ai/            dropLens/engine/          │
│  providers.py (208) presets+config        scan.py (241)  orchestration│
│  client.py (205)   HTTP transport         extract.py (519) 25+ formats│
│  svc.py (334)      summaries/tags/RAG     db.py (545)    persistence │
│                                           discover.py (148) walk+hash│
│                                           translate.py (120) cache   │
│                                           export.py (89)  CSV/TXT    │
│                                           watch.py (65)   polling   │
│                                           search.py (58)  FTS query  │
│                                           categorize.py (74) taxonomy│
├──────────────────────────────────────────────────────────────────────┤
│  Foundation — dropLens/config.py (137), resources.py (85), __init__   │
│  bootstrap: launcher.py → main.py (100)                              │
└──────────────────────────────────────────────────────────────────────┘
```

Layering discipline is **good and mechanically verifiable**: no module under `engine/` or `ai/`
imports anything from `ui/` (checked with a directed grep). Dependencies point one way:

```
main.py ──► ui/* ──► ai/svc ──► ai/client ──► ai/providers
                │         │
                └────► engine/scan ──► engine/extract, discover, categorize
                          │
                          └──► engine/db ◄── engine/search, translate, export, watch
                                │
                config.py ◄────┴──► resources.py (paths, logging)
```

`config.py` uses a **deferred import** of `ai.providers.AIConfig` inside `Config.ai_config()`
to break the only would-be cycle (`config ↔ providers`). That is the right call.

### 2.2 Responsibilities per module

| Module | LOC | Responsibility | Notable coupling |
|---|---|---|---|
| `main.py` | 100 | DPI awareness, logging, splash → window → onboarding, crash screen | imports UI, config |
| `config.py` | 137 | `DEFAULTS` dict, typed `Config` view, thread-safe `ConfigStore` (JSON) | knows `ai.providers` |
| `resources.py` | 85 | data dir, db/settings/log paths, Tesseract discovery, rotating logger | none |
| `engine/db.py` | 545 | **All** SQL: schema, roots, files, FTS, docmeta, prefs, stats, dup groups | raw `sqlite3` |
| `engine/discover.py` | 148 | cycle-safe walker, `FileInfo`, MD5 hashing, size formatting | none |
| `engine/extract.py` | 519 | dispatch table of extractors; optional-import guards | `charset_normalizer` |
| `engine/scan.py` | 241 | `ScanManager`: one thread, batched transactions, incremental diffing | db + extract + discover |
| `engine/search.py` | 58 | FTS5 `MATCH` builder, tokenizer, `<mark>` helpers | none |
| `engine/categorize.py` | 74 | extension → category taxonomy | none |
| `engine/translate.py` | 120 | chunked translation, DB cache, 70+ languages | `requests` |
| `engine/export.py` | 89 | CSV catalogue/search/duplicates, TXT document export | db |
| `engine/watch.py` | 65 | interval polling thread, fingerprint compare | discover |
| `ai/providers.py` | 208 | 11 presets, `AIProvider`, `AIConfig`, 3 protocol kinds | none |
| `ai/client.py` | 205 | `chat`, `chat_messages`, `embed`, `list_models`, `ping` | `requests` |
| `ai/svc.py` | 334 | summary, tags, embeddings, semantic search, related, RAG `ask`, folder report, bulk enrich | db + search |
| `ui/theme.py` | 104 | palette + ttk styling + widget helpers | none |
| `ui/icon.py` | 66 | runtime Pillow icon generation (no shipped asset) | Pillow |
| `ui/bootstrap.py` | 153 | splash + 2-step onboarding wizard | icon, theme |
| `ui/mainwindow.py` | 1552 | **Everything else**: 5 pages, drop handling, search, AI, tray, watch, settings wiring | all layers |
| `ui/dialogs.py` | 669 | Settings, AI Models, Translate, Duplicates, About, shell helpers | engine + ai |
| `ui/tray.py` | 89 | `pystray` icon, notifications, 3 menu actions | icon |

### 2.3 The architectural centre of gravity

`DropLensApp` is the system's hub: it owns the `Catalog`, `ScanManager`, `Translator`,
`FolderWatcher`, `Tray`, `AIService`, the `queue.Queue`, and five page trees. It also issues
**raw SQL** that bypasses the `Catalog` API (`_ai_enriched_count()` executes
`SELECT COUNT(*) … FROM docmeta` directly at `mainwindow.py:937`). Every other layer is a
library; this class is the application.

---

## 3. Runtime topology

### 3.1 Threads

| Thread | Created by | Lifetime | Touches UI? |
|---|---|---|---|
| Tk main | `TkinterDnD.Tk()` | process | yes (only) |
| `droplens-scan` | `ScanManager.scan_roots` | per scan | **no** — enqueues dicts |
| `droplens-watch` | `FolderWatcher.__init__` | until stop | **no** — enqueues |
| `droplens-tray` | `Tray.start` | until stop | no (pystray callbacks marshal via `after`/enqueue) |
| ad-hoc `Thread(...)` | `_ask`, `_ai_summary`, `_show_related`, `_folder_report_*`, `_ai_enrich_all`, `_run_semantic_search`, `_auto_ai_enrich`, translation, `_test`/`_list_models` | per action | **no** — enqueue or `after(0, …)` |

There is **no thread pool**. Each user action spawns a new daemon thread. The `threads`
setting in `config.DEFAULTS` is presented in Settings (`dialogs.py:57`) but is **never read by
the scan engine** — scanning is strictly single-threaded (verified: no consumer). This is the
single largest mismatch between documented configuration and actual behaviour.

### 3.2 The cross-thread contract

The whole design rests on one invariant: **workers only put plain dicts on `self.q`; the main
thread drains it**. Enforced in `_poll()` (`mainwindow.py:695`), which runs every 80 ms via
`master.after`, drains until empty, and re-arms itself.

Message kinds: `state`, `file`, `done`, `log`, `root_stats`, `watch_changed`, `tr_done`,
`tr_fail`, `ai_answer`, `ai_fail`, `ai_progress`, `ai_enrich_done`, `ai_sum_done`,
`sem_result`, `related_done`. Consistent, but a **stringly-typed protocol with no schema** —
dispatching happens in one 90-line `if/elif` chain.

Two inconsistent escape hatches break the purity of the contract: `AISettingsDialog._test`
and `_list_models` call `self.after(0, lambda: …)` directly from a worker instead of using the
queue, and `TranslateDialog` passes the **widget itself** through the queue (`"dlg": self`),
so the main thread holds a reference to a possibly-destroyed Toplevel and must call
`winfo_exists()` before use.

### 3.3 Concurrency hazards

1. **One SQLite connection, two threads.** `Catalog` opens a single connection with
   `check_same_thread=False`, guards writes with an `RLock`, but `execute()` **returns the
   cursor after releasing the lock** (`db.py:169`), so `.fetchall()` runs unguarded while a
   scan thread may be writing. WAL mode makes this mostly benign, but it is not a guarantee.
2. **Long write transactions vs. GUI reads.** `_scan_one` opens a transaction and commits per
   800-record batch, on the *same* connection the GUI reads through. Readers can observe
   uncommitted rows.
3. **Config by reference.** `Config` objects are handed to `ScanManager`, `FolderWatcher` and
   dialogs; `SettingsDialog._save` mutates its private copy then calls back. `on_saved`
   re-points `scanner.config`, but an in-flight scan keeps the old snapshot — acceptable, but
   only by accident.
4. **Queue flooding.** `on_progress({"kind": "file", …})` fires **per file**. A 200k-file scan
   pushes 200k dicts through an 80 ms drain loop; the UI counter lags arbitrarily behind the
   scan and memory grows. No coalescing.

---

## 4. Data architecture

### 4.1 Schema (`engine/db.py::_SCHEMA`)

```
roots(id, path UNIQUE, added_at, last_scan, status)
files(id, path UNIQUE, name, ext, size, mtime, ctime, root, hash, category,
      kind['file'|'folder'], status, dup_id, scanned_at)
  idx_files_root(root) idx_files_hashes(hash) idx_files_size(size) idx_files_cat(category)
docs  ── FTS5 virtual table (doc_id UNINDEXED, name, path, content)
      tokenize = 'unicode61'; ranking via bm25(); snippets via snippet(docs, 3, …)
translations(doc_id, lang, text, ts)  PRIMARY KEY(doc_id, lang)
docmeta(doc_id PK, summary, summary_lang, tags, notes, favorite, ai_status,
        embedding BLOB, meta_ts)
prefs(key PK, value)
```

Key design decisions:

- **`files.id` is the join key for everything.** `docs.doc_id` is a plain integer (FTS5 cannot
  hold foreign keys), `docmeta.doc_id`, `translations.doc_id` all key off it. There is no
  `ON DELETE CASCADE`; `mark_deleted()` deletes `files` and `docs` rows but **leaves `docmeta`
  and `translations` orphans**. Those orphans are invisible but never reclaimed, and they
  inflate `_ai_enriched_count()`, `list_tags()` and embedding scans.
- **Embeddings are `struct.pack('<Nf')` float32 blobs** stored in `docmeta`, with no dimension
  metadata apart from `len(blob)//4`. Mixing providers silently invalidates vectors (guarded
  by a length equality check that silently skips mismatches).
- **Tags are a comma-joined string**, so `files_with_tag()` must do
  `(',' || REPLACE(tags,' ','') || ',') LIKE '%,tag,%'` — no index, full `docmeta` scan.
- **FTS5 has no UPSERT**, so `scan.flush()` issues `DELETE` per doc then a batched `INSERT`.
- `translations` and `docmeta` are keyed by `doc_id`, which is **reused** after
  delete/re-add of a path? No — `files.id` is `INTEGER PRIMARY KEY` without `AUTOINCREMENT`,
  so SQLite **can** reuse a rowid after the max row is deleted. A stale `docmeta` row for a
  reused id would attach an old summary/embedding to a new document. Low probability, real
  consequence.

### 4.2 On-disk layout

| Artifact | Path | Notes |
|---|---|---|
| Catalogue | `%LOCALAPPDATA%\DropLens\catalog.db` (+ `-wal`, `-shm`) | overridable via `DROPLENS_DIR`, or Settings → data dir |
| Settings (incl. **API keys in clear text**) | `…\settings.json` | written with `indent=2` |
| Log | `…\droplens.log` | `RotatingFileHandler` 5 MB × 2 |
| Icon | generated at runtime by `ui/icon.py`; `build/appicon.ico` for packaging | no binary asset in the repo for the app itself |

`resources.data_dir()` and `config._default_data_dir()` compute the same path **twice with
slightly different logic** (one creates the directory and falls back to `~/.droplens`, the
other does not). The data-dir override is therefore resolved in two places that can disagree
if `DROPLENS_DIR` is unset and creation fails.

### 4.3 Growth characteristics

Per indexed file: one `files` row (~250 B), one `docs` row containing **up to 5 MB of extracted
text** (`extract._MAX_CONTENT`, from `DROPLENS_MAX_CONTENT`), optional `docmeta` summary/tags/
notes + float32 embedding (1,536 floats ≈ 6 KB for `text-embedding-3-small`). A 100k-file
library with average 20 KB extracted text is ≈ 2 GB of index. Nothing prunes `docs` content,
offers retention limits, or VACUUMs; `max_content_chars` in `config.DEFAULTS` is **dead**
(only `extract._MAX_CONTENT` is honoured, and only via environment variable).

---

## 5. Primary data flows

### 5.1 Cold start

```
launcher.py → dropLens.main.main()
  ├─ SetProcessDpiAwareness(1)                    (Windows-only, guarded)
  ├─ resources.setup_logging()                    (rotating + stream, APP_NAME logger)
  ├─ TkinterDnD.Tk() ; root.withdraw()
  ├─ show_splash(root)                            (static 470×320 card; progressbar never advances)
  ├─ DropLensApp(root)                            → Catalog, ScanManager, Translator, AIService,
  │                                                 5 pages, ttk theme, DnD binding
  ├─ app.boot_refresh()                           → refresh_all(), after(80,_poll), tray, watcher
  ├─ dismiss_splash() ; root.deiconify()
  ├─ if not cfg.onboarded and not DROPLENS_SKIP_ONBOARD: Onboarding(on_finish)
  │     on_finish: adopt local provider if requested, mark onboarded, add_paths(presets)
  └─ root.mainloop()
```

The splash is created *before* the window exists and is never animated (`on_frame` is
`lambda v: None`), so its determinate progress bar stays at 0 % while the app boots. Cosmetic,
but it is the first thing a user sees.

### 5.2 Scan (the hot path)

```
add_paths(paths) / onboarding / tray "quick scan" / watcher
  └─ ScanManager.scan_roots(roots, incremental=True)
       └─ thread "droplens-scan" → for each root: _scan_one(root)
            ├─ existing_in_root(root) → {path: (id, size, mtime)}      (incremental diff)
            ├─ for fi in walk_root(root, ignore_names, on_error):
            │    ├─ skip if size+mtime unchanged                  → res.skipped
            │    ├─ categorize(ext)
            │    ├─ duplicate prefilter:
            │    │    size seen twice this scan → hash both
            │    │    size already in files table → hash this one  (has_same_size)
            │    │    else remember path by size
            │    ├─ extract_content(path, ocr, tesseract_path) → (text, status)
            │    └─ buffer record; every 800 → flush(): upsert_file + DELETE/INSERT docs + COMMIT
            ├─ prune rows whose path vanished (mark_deleted, chunked at 900 params)
            └─ touch_root_scan(); emit root_stats + done
```

Properties worth noting:

- **Incremental diffing is size+mtime only** (1e-6 s tolerance). Correct and cheap, but a file
  rewritten with identical size and mtime is missed — rare but not impossible (rsync with
  `--times`, some build tools).
- **Duplicate detection deliberately hashes only size collisions.** Correct optimisation; the
  `path_rec`/`size_first` buffers exist solely to backfill a hash onto an earlier record in the
  same batch. Subtle but sound.
- **Extraction is serial and unconditional** for every non-skipped file — no worker pool, no
  size ceiling, no per-format timeout. A single pathological PDF blocks the whole scan.
- **`res.indexed = len(seen) - res.skipped - res.folders`** undercounts when a folder is
  skipped as unchanged (folders are counted in `seen` and subtracted once), and is unused
  downstream except for logging.
- `status_override = None` (`scan.py`) is dead code; a bare `except Exception as exc:` with an
  unused `exc` returns `"failed"` via an f-string with no placeholder.

### 5.3 Keyword search

```
term/mode/category → search.build_match(term, mode)
  tokens:  re.findall([\w@.\-\u0080-\uffff]+) → '"word*"'  (AND, prefix wildcard, quoted)
  columns: all→'' | name→'name:' | path→'path:' | content→'content:'
→ mainwindow._do_fts_search
   ├─ db.name_search(term)  (LIKE on name/path, ORDER BY size DESC)   [only in All/Name]
   ├─ db.search(expr)       (FTS5 MATCH + bm25 + snippet)
   ├─ _merge_results        (dedupe by files.id, name hits first)
   └─ _fill → Treeview rows, 5,000-row cap
```

The query builder is **injection-safe** by construction: user text is tokenised, quoted with
doubled `"`, and only prefixed with a column filter from a fixed map. `_file_clause()` builds
`AND root=?` / `AND category=?` with bound parameters. No f-string interpolation of user input
anywhere in `db.py`.

Two behavioural defects in this path:

- **Pagination is wrong.** After the first page, `_results_meta["offset"] = len(merged) - 1`,
  and `_load_more` queries FTS5 at `offset = that + 1`. Because page 1 was a *merge* of LIKE +
  FTS results, the FTS offset does not correspond to the merged list — "Load more" therefore
  skips rows and can re-emit others.
- **`_fill` is N+1.** For every row it calls `db.get_meta(row_id)` (one query per result, up to
  5,000 per search) just to render a `★`. With a cold cache on a large library this dominates
  search latency. The fix is a single LEFT JOIN.

Semantic mode is an entirely separate path (§5.4) that **silently ignores the category filter**
and bypasses `build_match`.

### 5.4 AI: embeddings and RAG

```
Settings → AI Models (dialogs.AISettingsDialog)
  presets → AIProvider(name, kind, base_url, api_key, model, embedding_model)
  kinds: openai (Ollama/LM Studio/llama.cpp/OpenAI/Azure/OpenRouter/Groq/Mistral/Custom),
         anthropic (Messages API), gemini (generateContent)
  persisted: settings.json → cfg["ai"] = {"providers":[…], "active":"…"}
       │
       ├─ summarize_doc  → chat(SYSTEM_SUMMARY) → docmeta.summary   (cached)
       ├─ auto_tag       → chat(SYSTEM_TAG)     → docmeta.tags      (cached, ≤7 tags)
       ├─ embed_doc      → embed([name+content]) → docmeta.embedding (packed float32)
       ├─ semantic_search→ embed(query) → cosine over ALL embeddings (Python loop)
       ├─ related        → cosine(base, all others)
       ├─ ask            → _retrieve: semantic top-8, fall back to FTS if <3 →
       │                   prompt with "[file: name]" blocks → SYSTEM_ANSWER →
       │                   parse citations by string-matching those markers
       ├─ folder_report  → files_under(root) → SYSTEM_REPORT
       └─ index_ai       → loop files_for_ai() (smallest first), busy-guarded per doc
```

Security-relevant: `client._chat_gemini` puts the API key in the **query string**
(`?key=…`), which is the documented Google pattern but ends up in proxy logs.
`_headers()` correctly uses `x-api-key` for Anthropic and `Bearer` elsewhere. Errors are
normalised to `AIConnectionError` with the provider's message truncated to 300 chars — good
for debuggability, but the message is surfaced verbatim in the UI log.

`_busy_guard(key, fn)` serialises per-key work (e.g. `sum:42`), returning a placeholder string
instead of raising. `ask` keys on `hash(question)` — process-randomised, so it is not a stable
identity, only a collision guard.

Semantic retrieval is **O(number of embeddings) per query with a Python loop and `struct.unpack`
per row**; there is no ANN index, no dimension normalisation at write time, and the whole
vector set is loaded into memory for each query. Fine to ~10k docs, quadratic-feeling beyond.

### 5.5 Watch, translate, export

- **Watch:** `FolderWatcher` sleeps `watch_interval` (≥2 s, default 15), then calls
  `quick_signature(root)` per root — which is a **full recursive `walk_root` with a `stat` per
  entry**. On a large monitored tree this is a constant-cost background walk every 15 seconds
  in order to decide whether to do another walk. Correct, but the "cheap signature" is not cheap.
- **Translate:** chunked at 3,800 chars, `requests.Session`, 0.15 s between chunks, cached by
  `(doc_id, lang)`. Uses the **undocumented Google Translate web endpoint**
  (`translate.googleapis.com/translate_a/single`, `client=gtx`) with no key, no retry, no
  backoff and no per-run cap. It is a de-facto third-party dependency with no SLA or terms
  coverage, and it is the app's only outbound call that is not user-configured. The README's
  "offline translation" is listed under roadmap, so this is understood to be temporary.
- **Export:** `export_catalogue`, `export_search_results`, `export_text`, plus duplicates CSV in
  the dialog. All UTF-8-SIG for Excel. Chunked `IN (...)` at 900 params. **All CSV paths crash**
  — see §9.

---

## 6. Extension points

| To add… | Touch exactly | Effort |
|---|---|---|
| A file format | `extract.py`: extractor fn + `_BINARY_EXTRACTORS` or `_TEXT_EXTENSIONS`; optionally `categorize.CATEGORY_MAP` | S |
| A new AI provider that speaks OpenAI | `providers.PRESETS` entry only | XS |
| A new AI *protocol* | `client.py`: `_chat_*`/`_embed_*` + branch in `chat_messages`/`embed`; `providers.KIND_*`; `dialogs` kind combobox | M |
| A UI page | `mainwindow`: entry in `MODES`, `_build_*_page`, branch in `show_page` | M |
| A new persisted setting | `config.DEFAULTS` **and** `dialogs.SettingsDialog._build/_save` **and** the consumer | S |
| A new DB column | `_SCHEMA` (no migrations exist) + `upsert_file` key list + readers | M |
| A headless CLI | New `cli.py` composing `Catalog` + `ScanManager`; no engine change needed | S |

There is **no schema migration mechanism**. `_SCHEMA` uses `CREATE TABLE IF NOT EXISTS`, so an
existing `catalog.db` from v1.0 will silently keep its old shape; a new column added to
`files` would not appear, and `upsert_file` would fail at runtime. For a 2.0 release that
changes `docmeta`/`prefs` this is the highest-severity **structural** (not yet triggered) gap.

---

## 7. Build and packaging pipeline

```
build.ps1 [-Clean] [-Version x.y.z]
  ├─ verify python on PATH, print version
  ├─ pip install -r requirements.txt -r requirements-build.txt
  ├─ (Clean) remove dist/, build/DropLens*/ , *.spec
  ├─ python build\make_icon.py build\appicon.ico
  ├─ python -m py_compile <every module>            (syntax gate)
  ├─ python -X utf8 tests\test_engine.py            (engine smoke gate)
  └─ PyInstaller --onefile --windowed --name DropLens
        --icon build\appicon.ico --version-file build\version_info.txt
        --collect-all tkinterdnd2 --hidden-import pymupdf --exclude-module test
        launcher.py                                  (root shim → dropLens.main.main)

build_installer.ps1 [-Version x]
  ├─ locate ISCC.exe (Program Files / x86 / LocalAppData)
  ├─ python installer\make_bitmaps.py               (branded wizard art)
  └─ iscc installer\DropLens.iss /DMyAppVersion=… /DMyAppOutputDir=dist
        → dist\Setup-DropLens-<v>.exe
```

The `launcher.py` shim exists precisely so PyInstaller never has to resolve relative imports in
a frozen package — a sensible, documented workaround.

Packaging observations:

- `requirements.txt` uses `>=` for **all nine** runtime dependencies. Anything can drift between
  builds; `docs/COMMERCIAL.md` acknowledges pinning as a release task, but nothing enforces it
  and there is no lockfile. Given PyMuPDF/Pillow/pdf text parsing touch untrusted files, this is
  a supply-chain-risk item, not just reproducibility.
- No CI: `.github/` does not exist, so the build gates (compile + engine smoke) only run on a
  developer's Windows machine.
- `installer/DropLens.iss` defines `MyAppId` including `{#MyAppVersion}` but **never uses it**
  (the `[Setup]` block hardcodes a literal GUID). Dead define; if it were wired up, every
  version would become a distinct product and upgrades would not replace the old install.
- Installer runs `DropLens.exe --warmup` post-install, but `main()` never inspects
  `sys.argv`, so the flag is ignored and a full app window opens silently-hidden
  (`SW_HIDE`) for the duration. Harmless but misleading.
- `.exe` is unsigned; both docs flag this. `AppUserModelID` is claimed in the build doc but the
  `.iss` does not set one.

---

## 8. What the architecture gets right

1. **Strict one-way layering, verified.** Engine and AI are pure libraries with no UI
   awareness. This is what makes a headless CLI (`Roadmap`) a small job rather than a rewrite.
2. **One authoritative persistence module.** All SQL lives in `engine/db.py` (with two leaks in
   the UI). Schema is declared in one `_SCHEMA` string, easy to audit.
3. **A single, explicit cross-thread contract** — workers enqueue dicts, the Tk loop drains.
   The invariant is stated in three module docstrings and actually held (except the two noted
   `after(0, …)` escapes).
4. **Graceful optional dependencies.** `extract.py` wraps PyMuPDF, python-docx, openpyxl,
   python-pptx, Pillow, pytesseract, extract-msg, py7zr in try/except imports and degrades to
   `None`/`no_content` per format without crashing the scan. The tray does the same. This is
   why the one-file `.exe` can ship without OCR or MSG support and still work.
5. **FTS5 done idiomatically** — external-content-free FTS table, `bm25()` ordering,
   `snippet()` with private marker chars (`\x01`/`\x02`) converted to `<mark>` in the UI, prefix
   wildcards via quoted tokens. The query builder is injection-proof.
6. **Read-only guarantee is architectural**, not aspirational: no `open(…, 'w')` on a user
   path exists anywhere in `engine/`; the only writes are to the data dir and user-chosen
   export destinations.
7. **Honest, useful instrumentation**: rotating log, activity page, per-root scan summaries
   with indexed/unchanged/removed/errors/duration, crash screen that names the log path.
8. **Zero-asset branding**: the icon is drawn with Pillow at runtime, so the frozen binary
   cannot drift from the installer art.

---

## 9. Verified defects

Each of the following was checked against the code, and the `AttributeError` class was
reproduced in this environment with a stdlib-only script (`sqlite3.Row` has `.keys()` but **no
`.get()`**; calling it raises `AttributeError`).

### 9.1 Blocking (feature is broken as shipped)

| # | Where | Defect | Verification |
|---|---|---|---|
| B1 | `engine/export.py:32,86` | `_row_to_dict` calls `row.get("content")` and `row.get("file_hash")` on a `sqlite3.Row` → **CSV export always raises `AttributeError`** | **Reproduced**: `export_catalogue` and `export_search_results` both crash on a one-row catalogue |
| B2 | `ai/svc.py:291,296` | `index_ai` calls `(self.catalog.get_meta(id) or {}).get("summary")` / `.get("tags")` → **"AI-enrich library" crashes on the first already-enriched document** | Same `AttributeError` class; Row without `.get` |
| B3 | `ui/mainwindow.py:1210,1213,1235` | Preview panel `_show_preview` calls `meta.get("tags"|"favorite"|"summary")` on a Row → **selecting a result that has any `docmeta` row raises inside the Tk callback** | Same class; triggered by any AI-enriched or favourited file |
| B4 | `ui/mainwindow.py:1286` | `self.db.get_meta(ids[0]).get("favorite")` → **★ Favorite toggle raises** when the row already exists | Same class |
| B5 | `ui/mainwindow.py:1384` | Notes dialog `meta.get("notes")` → **Notes… raises** for any file with metadata | Same class |

These are one shared root cause: the code mixes a **dict-style accessor (`Row | {}`)** with
`sqlite3.Row`. Any of the five can be fixed by converting rows (`dict(row)`) at the boundary or
adding a `safe_get(row, key)` helper. There is no test that reaches any of them — the engine
test never exports, and the AI test never calls `index_ai`.

### 9.2 High (silent wrong behaviour)

| # | Where | Defect |
|---|---|---|
| H1 | `ui/mainwindow.py:1096-1105` | **"Load more" pagination is inconsistent.** Page 1 merges LIKE + FTS hits; subsequent pages offset into the FTS result set only, so rows are skipped and duplicated |
| H2 | `ui/mainwindow.py:1060-1080` | Semantic search **ignores the category filter** entirely |
| H3 | `engine/db.py:245` | `mark_deleted` deletes `files` + `docs` but **not `docmeta`/`translations`** → orphan rows inflate tag counts, AI-enriched counts, and leak embeddings; no VACUUM path |
| H4 | `engine/db.py` `files(id INTEGER PRIMARY KEY)` | Rowid **reuse** after deletion can attach a stale `docmeta` summary/embedding to a newly added file |
| H5 | `engine/db.py::_SCHEMA` | **No migration mechanism.** `CREATE TABLE IF NOT EXISTS` means a v1 database keeps its old shape silently; new columns break `upsert_file` at runtime |
| H6 | `config.py:22` + `extract.py:_MAX_CONTENT` | `max_content_chars` is a **dead setting** (verified: defined once, never read); the real cap is an environment variable, so the documented knob does nothing |
| H7 | `config.py:24` `threads` | Dead setting (verified: only shown in the Settings spinbox); scanning is single-threaded despite the "Performance → worker threads" control |
| H8 | `resources.find_tesseract()` | **Never called** (verified). README's "Tesseract auto-detected" relies on the user typing a path; `tesseract_path` default is `""` |
| H9 | `engine/extract.py` dispatch | `ico` and `svgz` are in `_TEXT_EXTENSIONS` **and** `ico` in `_IMAGE_EXTS`; text check wins, so `.ico` files are decoded as text rather than OCR'd. `zip`/`epub` overlap is handled, but `.svgz` is treated as text although it is gzip-compressed binary |
| H10 | `engine/extract.py` `_extract_archive` | `.rar` is routed to `py7zr`, which does **not** read RAR. `requirements.txt` does not include `py7zr` at all, so 7z/RAR listings are always `None` despite README claims |
| H11 | `engine/watch.py` | "Cheap signature" is a **full recursive walk with a stat per entry** every interval — on a 500k-file tree this is a constant background I/O load |
| H12 | `ui/mainwindow.py:715` `_auto_ai_enrich` | `stop=lambda: False` — automatic enrichment **cannot be cancelled** even though `stop_scan` is presented as the universal stop |

### 9.3 Medium (fragility, correctness drift, cost)

| # | Where | Defect |
|---|---|---|
| M1 | `ui/mainwindow.py:1122` (also `:1158`) | `_fill` is **N+1**: one `get_meta` query per result row (up to 5,000 per search) just for the ★ marker |
| M2 | `ai/svc.py` `semantic_search` | Loads every embedding and runs a pure-Python cosine loop per query; no ANN, no cache, dimension mismatch silently skipped |
| M3 | `engine/scan.py` `_scan_one` | One extraction at a time with **no timeout or size ceiling**; a pathological file stalls the whole scan |
| M4 | `engine/scan.py` `flush()` | `upsert_file` resolves the row id with a **second SELECT per file**; FTS delete+insert is per-800 batch, so the same doc is churned |
| M5 | `config.py:_DEFAULTS_COPY = dict(DEFAULTS)` | **Shallow copy**: every `Config` shares the *same* `ignore_names` list; `patch(ignore_names=[…])` rebinds (safe), but any in-place edit would leak across instances |
| M6 | `config.ConfigStore.save` | `except OSError: pass` — a failed settings write is **completely silent**; the user's AI key/config appears saved and is lost on restart |
| M7 | 53 × `except Exception` (of which many `: pass`) | Pervasive swallowing, e.g. tray start, bookmark writes, DnD registration, translation cache. Failures are invisible instead of logged |
| M8 | `engine/translate.py` | Undocumented Google `gtx` endpoint: no key, no retries/backoff, no rate limiting, no size/run cap; a single point of external failure with unclear terms |
| M9 | `config.ConfigStore` | **API keys stored in clear text** in `settings.json`; no Windows Credential Manager / DPAPI. The AI Models dialog has a "show key" toggle, confirming they are meant to be secret |
| M10 | `ui/mainwindow.py` `_show_preview` | Duplicate-marker constants `_SNIP_B/_SNIP_E` are declared in **both** `db.py` and `search.py`; `search.cleanse_snippet` is unused by the UI (the UI highlights with `Text.search` instead) |
| M11 | `ui/dialogs.py` `AISettingsDialog._save` | Dead `else: pass` branch; `var_active` rendered as a `Checkbutton` with `style="Accent.TButton"` (style abuse); `self.tree = tree` assigned twice |
| M12 | `ui/tray.py` `Tray.hide()` | Empty method body — declared, never implemented, never called |
| M13 | `ui/mainwindow.py` | `_refresh_favorites()` and `_ai_enrich_all_gone()` are empty; `_refresh_favorites` **is called** from `refresh_all`, so the favourites feature is declared but has no surface |
| M14 | `main.py` splash | Progress bar never advances (`on_frame=lambda v: None`); splash is a static card |
| M15 | `installer/DropLens.iss` | `MyAppId` (version-bearing GUID) is defined and unused; `--warmup` passed post-install but `main()` never parses `argv`; no `AppUserModelID` despite the build doc's claim |
| M16 | `tests/*` | Scripts, not pytest: `assert`-based, `sys.path.insert(0, os.getcwd())`, module-global monkeypatching, GUI test needs a display. No runner, no CI, no coverage of export/preview/enrich paths (which is exactly where §9.1 lives) |

### 9.4 Dead surface inventory (verified by grep — no callers)

`db._common_prefix`, `db.search_count`, `db.extensions_top`, `db.by_size_recent`,
`db.upsert_doc`, `db.pending_inserts_commit`, `resources.find_tesseract`,
`Tray.hide`, `mainwindow._refresh_favorites` (stub), `mainwindow._ai_enrich_all_gone`,
`search.cleanse_snippet`, `config.DEFAULTS["max_content_chars"]`, `config.DEFAULTS["threads"]`,
`theme.card()` (superseded by `card_outer`), `scan` local `status_override`.

A small but real share of the codebase carrying no behaviour — it inflates the apparent API of
`Catalog` and makes the class look more capable than it is.

---

## 10. Structural assessment

**Strengths outweigh weaknesses for the stated product.** The layering is genuinely clean, the
SQLite/FTS5 core is idiomatic and injection-safe, optional dependencies degrade properly, and
the read-only promise is enforced by the absence of write paths rather than by policy. For a
local, single-user, Windows-only indexer this is a reasonable and honest architecture.

The four things that will actually bite:

1. **`DropLensApp` is a 1,552-line god object** that owns five pages, all services, the queue,
   raw SQL, and business logic (favourites toggling, export, RAG orchestration). Every new
   feature enlarges it; every bug in §9.1 lives in it or in the dict/Row boundary it pollutes.
2. **No migration story.** A 2.0 schema with no `user_version` / migration step means existing
   users' databases are a landmine on the next release.
3. **No automated safety net** on the paths that are most fragile (export, preview, bulk AI),
   which is precisely why five `AttributeError`s shipped.
4. **Documented configuration that does nothing** (`threads`, `max_content_chars`, Tesseract
   auto-detect) erodes trust in the Settings surface and hides a real performance limitation
   (single-threaded extraction).

---

## 11. Recommended target architecture and migration path

The existing layering is the right skeleton; the work is **extraction and hardening**, not a
rewrite. Recommended end state:

```
dropLens/
  app.py                  # composition root: builds services, owns lifecycle (no widgets)
  services/
    index_service.py      # add_paths / rescan / rebuild / cancel  (moves out of mainwindow)
    search_service.py     # keyword + semantic + pagination cursors (fixes H1/H2/M1)
    ai_service.py         # = ai/svc.py, unchanged API
  engine/…                # unchanged; add migrations.py + safe_get()
  ui/
    app.py (~250)         # window shell, nav, status bar, queue drain
    pages/library.py, search.py, assistant.py, duplicates.py, activity.py
    dialogs/…             # split by feature
```

Concrete, ordered plan (each step independently shippable):

| Phase | Work | Fixes |
|---|---|---|
| **0. Correctness hotfix** | Add `dict(row)` conversion or `db.safe_get(row, key)`; apply at the five sites; add assert-based tests for export, preview, favourite, notes, `index_ai` | B1–B5 |
| **1. Data integrity** | `PRAGMA user_version` + `migrate(conn)` runner; `AUTOINCREMENT` on `files.id` (or explicit id-reuse guard); delete `docmeta`/`translations` in `mark_deleted`; add `VACUUM`/retention setting | H3, H4, H5 |
| **2. Search correctness** | Replace offset arithmetic with a **resolved-id cursor** (store the merged id list; page over ids); apply category filter in the semantic path; single LEFT JOIN for favourites instead of N+1 | H1, H2, M1 |
| **3. Wire or delete settings** | `threads` → `ThreadPoolExecutor` inside `ScanManager` for extraction; `max_content_chars` → pass into `extract_content`; call `resources.find_tesseract()` when `tesseract_path` is empty | H6, H7, H8, M3 |
| **4. Format truth** | Add `rarfile` (or drop RAR claims), add `py7zr` to requirements; move `ico` out of `_TEXT_EXTENSIONS`; treat `svgz` as archive | H9, H10 |
| **5. Watcher cost** | Track per-root `(mtime, count)` from the last scan instead of re-walking; or move to `ReadDirectoryChangesW`/`watchdog` | H11 |
| **6. Config hygiene** | `deepcopy` defaults; log failed saves; surface save errors in the Settings dialog | M5, M6 |
| **7. Secrets** | Store provider keys via DPAPI (`win32crypt` / `keyring`), keep only non-secret provider fields in `settings.json`; migrate on load | M9 |
| **8. Observability** | Replace blanket `except: pass` in tray/DnD/translate/config with `logger.warning(..., exc_info=True)`; keep extraction's per-format swallow | M7 |
| **9. UI decomposition** | Extract `services/*` from `DropLensApp`, then split pages into modules; introduce a typed message dataclass for the queue | §10.1, M10–M13 |
| **10. Delivery** | Pin dependencies (`pip-compile`), add GitHub Actions Windows job running `py_compile` + the three test scripts, sign both `.exe`s, wire `AppUserModelID` | §7 |

Phases 0–2 are the ones that change user-visible correctness. Phases 3 and 5 are the ones that
change user-visible performance. The rest reduce future risk.

---

## 12. Appendix — settings key → consumer matrix

| Key | Default | Read by | Status |
|---|---|---|---|
| `data_dir` | `""` → `%LOCALAPPDATA%\DropLens` | `mainwindow._resolve_db_path` | live |
| `threads` | 4 | **nothing** | **dead** |
| `hash_duplicates` | true | `scan._scan_one` | live |
| `ocr_enabled` | true | `scan` → `extract_content` | live |
| `tesseract_path` | `""` | `scan` → `extract._extract_image_ocr` | live (manual only) |
| `watch_enabled` | false | `mainwindow` (tray/watcher), `boot_refresh` | live |
| `watch_interval` | 15 s | `FolderWatcher.run` | live |
| `default_lang` | `en` | `TranslateDialog` | live |
| `max_content_chars` | 5,000,000 | **nothing** | **dead** |
| `preview_chars` | 20,000 | `_show_preview` | live |
| `ignore_names` | 9 entries | `walk_root` | live |
| `ai` | `{}` | `Config.ai_config` → `AIService` | live |
| `tray_enabled` | true | `boot_refresh`, `open_settings` | live |
| `close_to_tray` | false | `_on_close` | live |
| `notify_scan_done` | true | `_handle("done")`, `ai_enrich_done` | live |
| `onboarded` | false | `main.main` | live |
| `semantic_default` | false | search page toggle | live |
| `ai_auto_enrich` | false | `_handle("done")` | live |

## 13. Appendix — test inventory

| Script | Covers | Does not cover |
|---|---|---|
| `tests/test_engine.py` | temp tree → walk, extract (txt/md/py/json/csv/PDF/ZIP), scan, FTS content search, name search, dup groups, incremental rescan, stats | export, translation, OCR, watch, incremental deletion |
| `tests/test_ai.py` | stubbed chat/embed: summary caching, tags, embedding persistence, cosine ranking, related, `ask` + citations, favourites, tag lookups, prefs round-trip | `index_ai` (B2), real providers, `list_models`, `ping`, `folder_report` |
| `tests/test_gui.py` | window boots, indexes the test folder, FTS search via UI state, preview selection, clean close | export, favourites, notes, semantic search, tray, onboarding, settings |

None of the three exercises `export.py`, `_show_preview` with metadata, `_toggle_fav`, or
`index_ai` — the exact surface where §9.1 lives.
