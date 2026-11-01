# DropLens 2.1.0 → 2.2.0 — what changed and why

Companion to [`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md) (what the code looked like)
and [`ENHANCEMENT_AND_INTEGRATION_ROADMAP.md`](ENHANCEMENT_AND_INTEGRATION_ROADMAP.md)
(what was worth changing). This file records what actually shipped: files touched, the
defect each change closes, and what was deliberately left out.

---

## 1. Correctness fixes (the crashes and the silent data loss)

| Defect | Fix | Where |
|---|---|---|
| `sqlite3.Row` has no `.get()` → CSV export, text export, preview, favourites, notes, translation and `index_ai` raised `AttributeError` | one boundary helper `row_get(row, key, default)`; every Row/dict read goes through it | `engine/db.py`, `engine/export.py`, `ui/mainwindow.py`, `ai/svc.py` |
| Deleting a file left `docmeta`, `translations` and (new) `chunks`/vectors/jobs orphaned, which could later attach to a recycled `files.id` | `mark_deleted` cascades in chunks of 500 ids; new libraries use `AUTOINCREMENT` | `engine/db.py` |
| No schema version, so a future change could not migrate an existing library | `PRAGMA user_version` + idempotent migrations, run on open | `engine/migrations.py` |
| `upsert_meta` did `SELECT` → `UPDATE` → `commit` per field | single `INSERT … ON CONFLICT DO UPDATE` (no read), optional `commit=False` for batches; `favorite=COALESCE(?, favorite)` so NULL means "leave it" | `engine/db.py` |
| Search results ran one `get_meta` query per row (up to 5 000) | `favorite` and `has_summary` are selected in the same query via `LEFT JOIN docmeta` | `engine/db.py`, `ui/mainwindow.py` |
| `threads` and `max_content_chars` settings were dead | extraction runs on a `ThreadPoolExecutor` sized by `threads`; `extract.set_max_content()` is applied per scan | `engine/scan.py`, `engine/extract.py`, `config.py` |
| `.rar` was handed to `py7zr` (cannot read RAR), `.ico` was treated as text | `.rar` uses `rarfile` when present; `.ico` is an image (OCR path) | `engine/extract.py` |
| Swallowed exceptions hid failures in scan/watch/AI paths | `logger.warning(..., exc_info=True)` outside `extract.py` | `engine/scan.py`, `engine/watch.py`, `ai/svc.py` |
| `stop=lambda: False` made "stop" a no-op | the AI page has a working **Stop** button wired to a real flag | `ui/mainwindow.py` |
| PDF text was flattened, losing page structure | pages are joined with `PAGE_MARK` (`\f`), which the chunker reads and the preview renders as a newline | `engine/extract.py`, `engine/chunk.py` |

**Regression tests:** `tests/test_export.py` (export paths + cascade), `tests/test_engine.py`
(schema version, export count), `tests/test_ai.py` (structured citations).

## 2. Chunked hybrid retrieval (E2)

New tables (migration v2):

```
chunks(id, doc_id, ord, page, start_char, end_char, text, token_est)
chunks_fts          -- external-content FTS5 over chunks.text, triggers keep it in sync
chunk_vectors(chunk_id, doc_id, embedding, model, dim, ts)
```

* **Chunking** — `engine/chunk.py`: paragraph-aligned windows (default 3 200 chars,
  15 % overlap), sentence then hard splits for oversize paragraphs, page numbers from
  form feeds and `== Slide N ==` / `== Sheet ==` markers, exact `start_char`/`end_char`.
* **Embedding** — one vector per chunk, batched (32 per request), 8 000-char cap, plus a
  document vector averaged over its chunks for "related files" and semantic search.
* **Retrieval** — `AIService.retrieve()`: BM25 over `chunks_fts` and cosine over
  `chunk_vectors`, fused with Reciprocal Rank Fusion (`1/(60+rank)`), top-k chunks.
  Neither channel is a fallback for the other any more.
* **Provenance** — the model receives `[#42] report.pdf · p.3` headers and must cite ids;
  `ask()` maps them back to `(doc_id, page, start_char, end_char, snippet)` and never parses
  filenames. Double-clicking a citation opens the file and scrolls the preview to the passage.
* **Scale** — pure-Python cosine with an optional NumPy fast path (`np.frombuffer` +
  matmul). Comfortable to roughly 50k chunks; `sqlite-vec` is the next step, not a rewrite.

## 3. Durable enrichment queue (E4)

`ai_jobs(doc_id, kind, state, attempts, next_at, last_error, content_hash)` with
`UNIQUE(doc_id, kind)`:

* queued because artifacts are **missing or stale**, not because a button was pressed;
* ordered by priority (favourites → small files), so the old `LIMIT 3000` ceiling is gone;
* retry with `2^attempts` backoff (cap 300 s), 3 attempts before `failed`;
* stopping leaves the remainder `queued` — the next run resumes;
* `docmeta.content_hash` is the version a summary/tag set/vector was computed from, so
  editing a file invalidates it (`Catalog.save_doc`) instead of serving the old content forever.

## 4. Platform and trust surface (E7 partial, E8 partial)

* `dropLens/cli.py` — `search`, `stats`, `scan`, `ask`, `export`, `egress`, `jobs`, `--json`.
  `python -m dropLens` still starts the GUI; any sub-command goes to the CLI.
* `dropLens/net.py` — one choke point for outbound calls: `check(url)` enforces
  **offline-only** mode (loopback/private ranges only) and `log_egress()` records kind,
  target and payload size in the `egress` table. Visible in Activity → *Network log…*
  or `dropLens egress`.
* `dropLens/secure.py` — DPAPI sealing of provider API keys on Windows (`dpapi:<base64>`);
  plaintext keys from 2.0 are re-sealed on the next settings save.

## 5. Deliberately not done

| Roadmap item | Why not now |
|---|---|
| E3 media reach (Whisper, CLIP, Docling) | each needs a new optional dependency and a model download path; shimming them would fake capability. The optional-import discipline in `extract.py` is the place they land. |
| E6 near-duplicate intelligence (MinHash, imagehash, Chromaprint) | independent of retrieval; adding it without UI for duplicate *groups* would only grow the DB. |
| E9 portability (encrypted snapshot) | needs a key-management decision (age/libsodium) that belongs with the distribution work. |
| E10 distribution/commerce | signing, winget manifests and licensing are release-engineering tasks, not code in this tree. |
| Cross-encoder reranking | with chunk-level retrieval the marginal quality is small versus shipping an ONNX runtime; `AIService.retrieve` is the single insertion point. |
| `watchdog` native events | the debounced polling watcher already coalesces bursts; native events change the dependency surface and need Windows validation. |

## 6. How it was verified

No Windows GUI is available in the analysis environment, so verification is engine-level
plus import/compile checks:

```
python -m compileall dropLens tests          # all modules import-clean
python -X utf8 tests/test_export.py          # export regression + cascade delete
python -X utf8 tests/test_retrieval.py       # chunking, hybrid RRF, citations, invalidation, queue
python -X utf8 tests/test_ai.py              # summaries, tags, embeddings, ask (stubbed provider)
python -X utf8 tests/test_engine.py          # scan, search, dedup, extraction, CSV export
python -m dropLens stats | search …          # CLI smoke test
```

`tests/test_gui.py` (Tk + tkinterdnd2) cannot run here; it is unchanged and should be run on
Windows before release.

## 7. Tools, extensions and the AI interface (2.2.0)

Three new layers, in dependency order, so no import cycles are introduced:

```
dropLens/tools.py        Tool, Toolbox, library_tools(catalog)      (imports engine only)
dropLens/extensions/     Registry, ExtensionAPI, default()          (imports tools)
dropLens/ai/agent.py     run(question, toolbox, provider)           (imports client + tools)
```

`ai/svc.py` composes them (`toolbox()`, `ask_agent()`), `cli.py` exposes them, and `engine/`
depends on nothing above it except a lazy `from ..extensions import default` inside
`extract_content`.

| Piece | Decision |
|---|---|
| Extractor dispatch | plugin first, built-in fallback; a plugin declining with `None` restores the shipped parser |
| Tool surface | strictly read-only over the catalogue; handlers never receive the catalogue handle from a plugin |
| Tool faults | caught in `Tool.call`, returned to the model as `{"error": ...}`; the loop continues |
| Loop bound | `max_steps` (default 4); the final call is made with `tools=[]` so prose always comes back |
| Citations | `AgentRun.chunk_ids` from tool results → existing `chunks_by_ids` → same cite dicts as `ask` |
| Plugin isolation | import and `register()` are wrapped; errors recorded on the `Extension`, never raised |
| Load trigger | `load_from_config` on scan and before the CLI commands that need it; idempotent per directory |

Deliberately not done: the GUI has no tool/plugin controls yet (the service and CLI do), and
plugins run in-process with no sandbox — they are user-installed code, documented as such.

**Verification added:** `tests/test_extensions.py` (discovery, precedence, hook isolation,
failing plugins, config switch) and `tests/test_tools.py` (tools, faults, agent loop bound,
unknown tools, citation resolution, OpenAI/Anthropic/Gemini message converters). Both use a
stubbed model; neither touches the network.

## 8. Recall-and-integrate pass

`read.md` §18 is the single register of every input this project has received and every
recommendation its review documents produced, with each status re-verified against the
code rather than copied from the earlier tables. Re-verifying them surfaced two items that
were still open and are now closed:

| Item | Change |
|---|---|
| Review §11 phase 6 / M6 | `ConfigStore.save` logged instead of swallowing `OSError` — a failed settings write no longer looks like a successful save. |
| Duplicated data-directory logic (review §219) | `ConfigStore` now uses `resources.settings_path()`; `_data_dir_override_or_default()` / `_default_data_dir()` are deleted, so `settings.json`, `catalog.db`, the log and `plugins/` share one resolved folder under `DROPLENS_DIR` too. |

Also corrected while testing the extension layer: `examples/plugins/csv_tables.py` fed a
`csv.Sniffer` dialect object to `csv.reader(**dialect)`, which raised and made the shipped
example a no-op — it now returns reader keyword arguments.

Everything else the register lists as **open** or **deferred** is genuinely untouched:
watcher cost (H11), the remaining silent `except: pass` sites (M7), the
`DropLensApp` decomposition, delivery/CI, and the deliberately deferred E3/E6/E9/E10 items.

## 9. Phase 1 — search correctness (H1 + H2)

The two remaining user-visible wrong behaviours from the review are now fixed.

| Item | Change |
|---|---|
| H1 — pagination | `dropLens/engine/search.py::ResultCursor` pages over a resolved id list. Page 1 seeds the name and FTS channels; every later page pulls the next window from each channel until enough *new* ids exist, deduplicating by id, so a row is served exactly once and never skipped. `ui/mainwindow.py::_do_fts_search` walks it and the old `offset` arithmetic (and `_merge_results`) are gone. |
| H2 — category filter | `Catalog.file_ids(category, root)` gives the vector channel the same restriction the FTS channel already applies in SQL. It filters `AIService.retrieve`'s vector rank and `semantic_search`, which now takes `category=`; `_run_semantic_search` passes the selected category instead of dropping it. |

Both helpers are plain functions over rows and fetchers precisely so they can be tested
without Tk, which cannot run in this environment.

**Verification added:** `tests/test_engine.py` grew a pagination section (overlapping
name/FTS channels: every id of the union served exactly once across pages, cursor
exhausted at the end, a disabled name channel never queried) and `tests/test_retrieval.py`
grew a §5b category section (`file_ids` contract; a vector-only query returns hits from
the right document only; `semantic_search` restricted per category). Full run after the
change: `compileall` clean and all six runnable scripts exit 0.

**Not verified here:** the Tk wiring itself (`_do_fts_search` / `_run_semantic_search`)
compiles but cannot be executed without a display — `tests/test_gui.py` should be run on
Windows before release.

### Release truth (commercial readiness)

A 2.2.0 build used to mislabel itself: the package said 2.2.0 while
`build/version_info.txt`, the PyInstaller script default and the Inno Setup default all
said 2.0.0, so the exe's file properties and the installer would have disagreed with the
app. `build.ps1` and `build_installer.ps1` now read `APP_VERSION` from
`dropLens/__init__.py` (single source of truth) and `build.ps1` stamps
`build/version_info.txt` before compiling, so the metadata cannot drift again. Separately,
`py7zr` and `rarfile` are now declared and passed as PyInstaller hidden imports — without
them the frozen exe could not list 7Z/RAR although the README advertised both. All three
are pinned by `tests/test_engine.py`.

Still required for a commercial release, and impossible in this Linux environment:
building `dist\DropLens.exe` (PyInstaller does not cross-compile), compiling
`Setup-DropLens-<V>.exe` (Inno Setup is Windows-only), and code-signing both with an
OV/EV certificate. Those steps run on Windows via `build.ps1` and `build_installer.ps1`;
the procedure and checklist are in `build/BUILD.md` and `docs/COMMERCIAL.md`.

**Verification after this pass:** `python -m compileall -q dropLens tests` clean;
`test_engine`, `test_export`, `test_retrieval`, `test_ai`, `test_extensions`, `test_tools`
all exit 0; relative links in `read.md` and `README.md` all resolve. `test_gui.py` still
cannot run without Tk.

```
python -X utf8 tests/test_extensions.py
python -X utf8 tests/test_tools.py
python -m dropLens tools           # 4 built-in tools listed
python -m dropLens extensions      # loaded plugins, extractors, tools, hooks
```
