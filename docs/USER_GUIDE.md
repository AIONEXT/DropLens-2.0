# DropLens — User Guide

## Getting started

1. Install **Setup-DropLens-2.x.exe** (or run the portable `DropLens.exe`).
2. On first launch a **welcome wizard** appears: tick the folders you want
   organized silently (Documents, Downloads, Desktop, Pictures, Music, Videos)
   and choose whether to use a **local AI model** (Ollama / LM Studio — fully
   offline). DropLens then starts scanning those folders in the background.
3. **Drop** folders or files anywhere into the drop zone at any time — or use
   *＋ Add Folders / ＋ Add Files*. Dropping a single file also indexes its
   containing folder.
4. Open the **Search** tab and type — or head to **AI Assistant** and ask a
   question about your library.

## Pages (left navigation rail)

### Library
- **Drop zone** — big hero panel: drop anything on it (or on the window body).
- **Stat cards** — files indexed, folders, size, duplicate groups, smart tags,
  AI-enriched files.
- **Monitored folders** — right-click for Rescan / Full rebuild / Open in
  Explorer / Remove from Library (removes only the local index; your files are
  never touched). `⟳ Scan all` rescans incrementally; `■ Stop` cancels safely.
- **Auto-watch** — background re-indexing whenever folders change.

### Search
- **Keyword modes** — *All / Name / Path / Content* (AND prefix-wildcards, BM25
  ranking, full-text highlights).
- **Semantic (AI meaning)** — engage it to find files *about* the same topic
  using embeddings instead of exact words. (Requires a provider with an
  embedding model — enable one in *Settings → AI Models*.)
- **Tag chips** — click any smart tag to list every file carrying it.
- Result actions: **Open · Folder · ★ Favorite · Translate… · Copy Path ·
  Export…**, plus the AI **summary / Notes / Related / Report** controls in the
  inspector panel on the right.
- *Load more* pulls the next page (max display 5,000).

### AI Assistant
- Ask questions — answers are built **only from your indexed files** and cite
  the exact passage (`[#42] contract.pdf · p.7`). Double-click a citation to
  open the file and jump the preview to that page.
- **AI-enrich library** — generates summaries, smart tags and embeddings for
  everything (smallest files first; progress bar; safe to interrupt).
- **Folder report…** — an executive summary of any scanned folder.

### Duplicates & Activity
- **Duplicates** — identical-byte groups with wasted-space estimates; details +
  CSV export.
- **Activity** — timestamped log (same lines written to
  `%LOCALAPPDATA%\DropLens\droplens.log`), plus **Network log…** — every
  outbound request DropLens made, with target and payload size.

## Extensions and tools

- The assistant can call four **read-only** tools — `search_library`,
  `read_document`, `library_stats`, `list_documents` — and answers with the same
  `[#42]` citations. It can look things up; it cannot edit, move or delete.
- **Add your own**: put a Python file in the `plugins` folder inside your data
  folder. It can teach DropLens a new file format, add a tool, or run a hook
  after a document is indexed. A plugin that fails is listed with its error and
  otherwise ignored. See [`EXTENSIONS.md`](EXTENSIONS.md).
- CLI: `droplens ask --tools "…"`, `droplens tools`, `droplens extensions`
  (add `--json` for scripts). Setting `plugins_enabled=false` disables plugins.

## Settings

- **Data location** — change where the catalogue lives; library reopens instantly.
- **Default language / Worker threads / Duplicate detection / OCR (Tesseract) /
  Auto-watch / Ignored names** — as before.
- **Tray controls** — show the system tray icon, close the window to tray,
  notifications after scans.
- **Offline-only** — blocks every non-local network request (cloud models and
  translation included). Use it when the answer to “does it phone home?” must be “no”.
- **AI-enrich automatically after scans** — keep metadata fresh without clicking.
- **AI Models…** — manage providers (see below). API keys are sealed with Windows
  DPAPI, never stored as plaintext.
- **Plugins** — loaded from `<data folder>/plugins` unless `plugins_enabled` is
  turned off in the config file. They are optional: with no plugins installed
  every feature behaves exactly as before.

## AI Models (Settings → AI Models)

1. Pick an **Add preset** (Ollama, LM Studio, OpenAI, OpenRouter, Groq, Mistral,
   Claude, Gemini, custom OpenAI-compatible…) or make your own.
2. For local models leave the API key empty; for cloud providers paste your key.
3. Click **Test connection** — a green “Connected ✓” confirms it works.
4. Enter an **embedding model** (e.g. `nomic-embed-text` for Ollama,
   `text-embedding-3-small` for OpenAI) to unlock semantic search.
5. **List models** polls the endpoint for available model ids (OpenAI-compatible
   servers).
6. **Set active** makes it the model used for summaries, tags and answers.

### Recommended free setups
- **Fully offline, no account:** install [Ollama](https://ollama.com) →
  `ollama pull llama3.1` + `ollama pull nomic-embed-text`, then select the
  Ollama preset (already the default active provider).
- **Fast cloud trials:** Groq or OpenRouter with a free API key; add
  `text-embedding-3-small`-style embedding model where offered.

### Respan Gateway (one endpoint, many providers)
The **Respan Gateway (local)** preset routes DropLens through an LLM gateway embedded in
the app and bound to loopback. Declare routes and fallback once in `gateway.toml` inside
the data folder (`listen` stays on `127.0.0.1`), then set **Model** to a route name
(`smart`) or `provider/model`. Your provider keys stay on this page, or in `env:NAME`
references inside `gateway.toml` — never in source. Configuration and options:
[gateway guide](https://github.com/respanai/respan-gateway?utm_source=freebuff&utm_medium=paid&utm_campaign=respan-gateway&bfcid=bfc_1.eyJpZCI6IjlmOGQyYzYyLTYwZjAtNDcxNC04YTRlLWY5MmI4NDk4ODY5MSIsImMiOiIyMDM2ODZkZS1mMzg0LTQ4NTQtYmViNC1hYzBiYWRmNmM1NTUiLCJrIjoiM2IxMGE4YmUtNzgyOS00MmJhLWFmNmEtYmI0YWUxZTRhY2Y4IiwiaWF0IjoxNzkzNDM2MDY0fQ.fL2Dz1C-E1VcSgzJUIuc047M-s1R1huNKjoheyPPBno).

## Tray (silent operation)

When the tray icon is enabled, DropLens keeps scanning and watching in the
background even while the window is hidden. Right-click the tray icon to
**Open DropLens**, **Quick scan (all folders)** or **Exit**; scan/notification
balloons appear when background jobs complete.

## Privacy notes

- Your originals are **read-only inputs** — never modified.
- Indexing, search and translation caches are local.
- **Offline-only** stops all outbound traffic; the **Network log** (Activity) shows
  exactly what was sent, to where, and how much — before you ever turn it on.
- With a **local** model nothing ever leaves your PC; with a **cloud** provider
  only the document excerpts you ask about (or summarize) are sent to that
  provider — never your whole catalogue.

_Continue below for the pre-v2 notes on translation details._

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