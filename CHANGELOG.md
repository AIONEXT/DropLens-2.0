# Changelog

All notable changes to DropLens are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.2.0] - 2026-10-31

### Added — extensions and tools
- **Plugin system** (`dropLens/extensions/`): Python files in `<data_dir>/plugins`
  register extractors (`register_extractor`), assistant tools (`@api.tool`) and
  hooks (`@api.on("scan_start" | "document_indexed" | "scan_complete")`).
  Loading is idempotent, per-directory and isolated: a plugin that raises is
  recorded with its error, listed by `droplens extensions`, and skipped.
- **Extractor precedence**: a plugin extractor runs before the built-in parser
  and may decline by returning `None`, which falls back to the shipped format
  support — adding a format no longer means replacing one.
- **Read-only tool surface** (`dropLens/tools.py`): `search_library`,
  `read_document`, `library_stats`, `list_documents`, with JSON schemas, bounded
  results and fault-tolerant calls (a tool error becomes a payload for the model,
  never an exception in the scan or the worker thread).
- **Tool-calling assistant** (`dropLens/ai/agent.py`): provider-neutral loop with
  a hard `max_steps` bound. The last turn is taken without tools, so the caller
  always gets prose. Citations resolve from chunk ids the tools actually
  returned, through the same `chunk_id → (doc_id, page, start, end)` map as
  ordinary answers.
- **Provider tool protocol** in `ai/client.py`: OpenAI `tools`/`tool_calls`,
  Anthropic `tool_use`/`tool_result`, Gemini `functionCall`/`functionResponse`.
- **CLI**: `droplens ask --tools [--steps N]`, `droplens tools [--json]`,
  `droplens extensions [--reload] [--json]`.
- `plugins_enabled` setting. Plugins load from the effective data folder,
  including the `DROPLENS_DIR` override.
- Docs and tests: `docs/EXTENSIONS.md`, `examples/plugins/csv_tables.py`,
  `tests/test_extensions.py`, `tests/test_tools.py` (stubbed model, no network).

### Changed
- `engine/extract.py` dispatches through the registry; the built-in extractors
  are unchanged and remain the fallback for every format.
- `engine/scan.py` fires `scan_start`, `document_indexed` and `scan_complete`
  hooks after the catalogue write for each document.
- `ConfigStore` resolves `settings.json` through `resources.settings_path()`,
  so the catalogue, settings, log and plugins all live in the same folder — the
  second, divergent copy of the data-directory logic (which ignored
  `DROPLENS_DIR`) is gone.

### Fixed
- **Search pagination (review H1):** “Load more” offset into the FTS result set
  only, after page 1 had merged name *and* FTS hits — rows were skipped and
  repeated between presses. Results are now served by a resolved-id cursor
  (`dropLens/engine/search.py::ResultCursor`) that walks both channels and
  yields every id exactly once in a stable order.
- **Semantic category filter (review H2):** a category selected in Search was
  applied to the keyword channel in SQL but not to embeddings — vector hits from
  other categories leaked into the filtered view. The vector channel of
  `AIService.retrieve` and `semantic_search` now apply the same filter through
  `Catalog.file_ids(category, root)`, and the window passes the chosen category
  to semantic search.
- **`.svgz` (review H9):** gzip-compressed SVG was in the plain-text family, so it
  was decoded as binary noise; it is now gunzipped and indexed as real XML text.
- **7Z/RAR (review H10):** `py7zr` and `rarfile` are declared in `requirements.txt`
  and passed to PyInstaller as hidden imports, so the frozen exe can actually list
  7Z and RAR archives the README advertises. RAR *decompression* still needs an
  `unrar`/`bsdtar` binary on the machine (stated in `requirements.txt`).
- **One version stamp:** the package, `build\version_info.txt`, the PyInstaller
  default and the Inno Setup default disagreed (2.2.0 vs 2.0.0), so a shipped build
  would have mislabeled itself. Both build scripts now read `APP_VERSION` from
  `dropLens/__init__.py` and `build.ps1` stamps `build\version_info.txt` before
  compiling.
- A failed `settings.json` write was swallowed with `except OSError: pass`, so a
  settings change could appear saved and revert on restart; it now logs a
  warning naming the path and the error (review item M6).
- `examples/plugins/csv_tables.py` passed a `csv.Sniffer` dialect object where
  `csv.reader` needs a mapping, so the shipped example plugin raised instead of
  extracting; it now returns proper reader keyword arguments and narrows its
  error handling to `OSError`/`csv.Error`/`UnicodeError`.

## [2.1.0] - 2026-10-30

### Added — retrieval
- **Chunk-level hybrid retrieval**: documents are split into paragraph-aligned
  windows (page/slide/sheet aware, 15 % overlap) stored in `chunks` + `chunks_fts`;
  queries retrieve from BM25 **and** chunk embeddings and fuse both rankings with
  Reciprocal Rank Fusion. Neither channel is a fallback for the other any more.
- **Real provenance**: the assistant cites `[#42] file.pdf · p.3`, resolved to
  `(doc_id, page, start_char, end_char)` from the chunk table — no more filename
  guessing, so duplicate filenames cite the right file and the preview scrolls to
  the cited passage.
- **Batched embeddings** (32 chunks per request, 8 000-char cap per chunk) and a
  document vector averaged from its chunks, so a 300-page PDF is no longer one
  embedding of its first 45k characters. Optional NumPy fast path for cosine.
- `PRAGMA user_version` migrations (`engine/migrations.py`); new libraries get
  `AUTOINCREMENT` ids.

### Added — durable enrichment queue
- `ai_jobs` table + `ai/jobs.py`: resumable enrichment with priority ordering
  (favourites first, then small files), retry with exponential backoff, real
  cancellation that leaves the rest queued, and no 3 000-file ceiling.
- **Staleness invalidation**: `docmeta.content_hash` records the version every
  summary, tag set and vector was computed from; editing a file drops the stale
  artifacts instead of advertising the old content forever.

### Added — transparency and reach
- **Offline-only mode** (`net.py`): one switch blocks every non-loopback request,
  including the Google Translate endpoint, and every outbound call is recorded in
  the `egress` table — inspectable from Activity → *Network log…* or `dropLens egress`.
- **Headless CLI** (`python -m dropLens search|stats|scan|ask|export|egress|jobs`),
  including `--json` output for scripting.
- **DPAPI-sealed API keys** on Windows (`secure.py`); plaintext keys in
  `settings.json` are migrated on the next save.

### Fixed
- `sqlite3.Row` has no `.get()`: CSV export, text export, the preview pane,
  favourites, notes, translation and the enrichment loop all crashed. Replaced by
  `db.row_get()` at every boundary, with `tests/test_export.py` as a regression test.
- Deleting or rescanning a file left orphaned `docmeta`/`translations` rows that
  could later attach to a recycled rowid; `mark_deleted` now cascades to docs,
  chunks, vectors, translations and jobs.
- `upsert_meta` no longer does read-modify-write + commit per field (2 statements,
  no SELECT); the search-results list no longer runs one `get_meta` query per row.
- `threads` and `max_content_chars` settings were dead: extraction now runs on a
  pool sized by `threads`, and the stored-text cap is applied per scan.
- `.rar` was routed to `py7zr` (which cannot read RAR) and `.ico` was treated as
  text; both are handled correctly. PDF pages keep form-feed boundaries so page
  numbers survive into citations.
- Swallowed exceptions outside `extract.py` are logged with tracebacks.
- Watcher is debounced (a save burst triggers one incremental rescan) and never
  dies on an unreadable root.

## [2.0.0] - 2026-09-19

### Added — AI (local & cloud)
- Pluggable AI framework connecting to **any local or cloud model**: Ollama,
  LM Studio, llama.cpp, OpenAI, Azure OpenAI, OpenRouter, Groq, Mistral,
  Anthropic Claude, Google Gemini and any OpenAI-compatible endpoint.
- Provider management UI (Settings → AI Models): presets, custom endpoint/key,
  live model listing, one-click **test connection**, active-provider switch.
- **AI summaries** per document (model-generated, cached in DB, shown in preview).
- **Smart auto-tagging** across the library with clickable tag chips + tag-filtered
  search.
- **Embeddings + semantic search**: toggle “Semantic (AI meaning)” to find files
  by topic; embedding vectors stored packed in `docmeta`.
- **AI Assistant**: ask questions about your library; answers use only indexed
  content and cite the exact source files (hybrid retrieval: embeddings + FTS5).
- **Folder reports**: executive AI report for any scanned folder.
- **AI-enrich library** (bulk summaries/tags/embeddings, cancellable) with optional
  automatic enrichment after scans.

### Added — Professional UX
- Branded **splash screen** (big runtime-generated icon) on startup.
- First-run **onboarding wizard**: pick Documents/Downloads/Desktop/Pictures/
  Music/Videos for silent background scanning and opt into offline local AI.
- Redesigned dark “deep-space” **sidebar UI**: Library / Search / AI Assistant /
  Duplicates / Activity pages with stat dashboard cards.
- **System tray**: silent background operation, scan/notification balloons,
  “quick scan all”, close-to-tray option.
- Favourites, notes and related-file (semantic similarity) inspector.
- **Inno Setup installer**: `installer/DropLens.iss` + `build_installer.ps1`
  producing `Setup-DropLens.exe` with branded welcome art, shortcuts,
  uninstaller and silent install.

### Added — Under the hood
- `docmeta` table (summary, tags, notes, favourite, embedding), `prefs` table,
  `AIConfig` persisted inside `settings.json`.
- New AI test (`tests/test_ai.py`) with a stubbed provider covering summaries,
  tags, embeddings, semantic ranking, related, citations and favourites.

## [1.0.0] - 2026-09-19

### Added
- Drag-and-drop ingestion of **any file or any folder** (single file drops index
  their containing folder).
- Incremental scanning with progress, live entry counter and per-root results.
  Unchanged files are skipped via size+mtime diffing; deletion pruning.
- Full-text search over names, paths and extracted content via SQLite **FTS5**
  (BM25 ranking, ranked snippets, prefix-wildcard tokenizer).
- Search modes: All / Name / Path / Content + category filter + preview pane
  with match highlighting.
- Content extraction for 25+ format families: text & code (charset auto-detect),
  PDF, DOCX/DOC, XLSX/XLS, PPTX/PPT, ODT/ODS/ODP, RTF, CSV/TSV/JSON/XML/YAML,
  EPUB, EML/MSG, ZIP/TAR/GZ/BZ2/XZ/7Z/RAR listings, and images via optional
  OCR (Tesseract).
- Automatic categorization by file type and duplicate detection by content hash
  (size-collision short-circuit keeps scanning fast).
- Machine translation of extracted content into 70+ languages with local
  caching; originals never modified.
- Library management: monitored folder list, rescan / full rebuild / remove,
  open in Explorer, remove clears local index only.
- Statistics: file/folder counts, human sizes, duplicate groups and wasted space.
- Auto-watch: background fingerprint polling, automatic incremental re-index on
  change (toggle + configurable interval in Settings).
- CSV export of the full catalogue, search results and duplicate groups;
  TXT export of document text and translations.
- Settings dialog (data location, threads, OCR toggle + Tesseract path, watch,
  ignored names, default language).
- Single-file Windows executable (PyInstaller) with application icon and version
  metadata; `build.ps1` one-command build.
- Rotating log at `%LOCALAPPDATA%\DropLens\droplens.log`; crash-screen with log
  path on fatal errors.

### Notes
- Thread-safe UI: worker threads enqueue events; only the main Tk loop touches
  widgets. No OCR/whisper binaries are bundled (optional installs only).