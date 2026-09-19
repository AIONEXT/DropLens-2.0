# Changelog

All notable changes to DropLens are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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