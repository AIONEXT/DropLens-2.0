# DropLens — User Guide

## Getting started

1. Start `DropLens.exe`.
2. **Drop** folders or files anywhere into the blue drop zone — or use
   `File → Add Folders/Add Files`. Dropping a single file also indexes its
   containing folder.
3. Wait for the scan to finish (progress bar + live entry counter at the bottom).
4. Open the **Search** tab and type.

## Library tab (monitored folders)

Lists every monitored root with file/folder counts, last scan time and status.

- Right-click a folder → **Rescan**, **Full rebuild** (re-extract everything),
  **Open in Explorer**, or **Remove from Library** (removes its local index — the
  original folder is never touched).
- **Scan Now** rescans everything incrementally (unchanged files are skipped).
- **Stop** cancels the running scan safely (everything already indexed is kept).

## Search tab

- **Search** box + Enter (or press the Search button). Results appear as you type
  (2+ characters).
- **Mode** — *All / Name / Path / Content*:
  - *All* searches names, paths and extracted text together (recommended).
  - *Content* only matches inside extracted documents (PDF/Office/code/…).
  - *Name* / *Path* target just those fields.
- **Type filter** — restrict to one category, e.g. *Spreadsheet* or *PDF
  documents* (choose `Document` and search).
- Click a result for a **preview with highlights**; double-click to open.
- Buttons: **Open · Open Folder · Translate… · Copy Path · Export Results…**.
- *Load more* pulls in the next page of results (max display 5,000).

## Duplicates tab

Shows identical-file groups detected by content hash. **Details…** opens the full
list with the wasted-space estimate (excludes the first copy) and exports to CSV.
Files are only hashed when their byte-size collides, so duplicate detection is
fast on large libraries.

## Activity tab

A timestamped log of every scan, change and error — useful for verifying large
indexing runs and troubleshooting (the same lines are appended to
`%LOCALAPPDATA%\DropLens\droplens.log`).

## Settings

- **Data location** — where the catalogue lives. Change it and the library
  reopens from the new location immediately.
- **Default language** — target language for the Translate dialog.
- **Worker threads** (1–16) — parallel extraction inside one scan session.
- **Detect duplicate files** — toggles content hashing.
- **OCR images** — enable when Tesseract is installed; point at `tesseract.exe`
  if not auto-detected.
- **Auto-rescan when folders change** + **Watch interval** — enables the watcher
  at startup and controls polling frequency (5–600 s).
- **Ignored names** — comma-separated folder/file names skipped during scans.

## Tips

- Keep **Auto-watch** on for active folders (Downloads, Documents, project dirs)
  and let it keep your library current.
- Huge trees: first scan extracts everything (one-time); afterwards rescans are
  incremental and fast.
- Use **Export Catalogue (CSV)** (File menu) for inventories/audits; open the CSV
  in Excel directly (UTF-8 BOM).

## Data & privacy

- Indexes are read-only; your originals are never modified.
- Search works fully offline. Translation calls Google’s free web endpoint only
  when you click **Translate** and results are cached locally.
- Delete a monitored folder from the **Library** to purge its local index.