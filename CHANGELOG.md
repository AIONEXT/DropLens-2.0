# Changelog

All notable changes to DropLens are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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