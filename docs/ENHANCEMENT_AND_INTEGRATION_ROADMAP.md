# DropLens 2.0 — Key Highlights, Incremental Enhancements, and Integration Horizon

Companion to [`ARCHITECTURE_REVIEW.md`](ARCHITECTURE_REVIEW.md). That document describes what
exists and what is broken; this one answers a different question: **what would actually make a
difference, in increments that can ship, and how far can the integration surface reach?**

Assumption stated up front: DropLens is a single-user, offline-first Windows desktop app whose
value proposition is *"point me at my folders and let me find and understand anything in them"*.
Every recommendation below is judged against that, not against "add more AI".

---

## 1. Executive summary

DropLens already has the two hard parts most local-search products never get right: a
**trustworthy local index** (SQLite + FTS5, read-only by construction) and a **provider-agnostic
model layer** (11 presets, 3 protocols, OpenAI-compatible). What it lacks is not features — it is
**retrieval depth**, **content reach**, and a **programmable surface**.

Ranked by the difference a user would actually feel:

| Rank | Move | Why it changes the product | Effort |
|---|---|---|---|
| 1 | **Chunk-level hybrid retrieval + reranking + real provenance** | Turns the assistant from "8 whole files, cited by filename" into "the right paragraph, clickable". Fixes the deepest quality ceiling. | 6–10 d |
| 2 | **Durable, resumable, invalidation-aware enrichment queue** | Makes "AI-enrich my library" survive restarts, cost money predictably, and stop serving stale summaries after a file changes. | 5–8 d |
| 3 | **Content reach: audio/video/photos** | An indexer that cannot search your voice notes, meetings and screenshots is searchable only for what you typed. | 6–10 d |
| 4 | **Programmable surface: CLI + MCP + local API + plugin registry** | The index becomes infrastructure other tools (and agents) can query. Highest leverage per line of code. | 4–7 d |
| 5 | **Trust layer: secrets, egress transparency, offline-only mode** | You are asking people to let software read every file they own. Trust is the feature. | 3–5 d |

Everything else — near-duplicates, index portability, auto-update, licensing — is real but
secondary. The anti-goals in §6 matter as much as the list.

---

## 2. Key highlights worth protecting

These are genuine architectural assets. Each comes with the failure mode that would destroy it.

### 2.1 Read-only by construction

There is no write path from `engine/` to any user file — no `open(..., 'w')` on a scanned path
anywhere. This is not a policy, it is the absence of code, which is why it holds.

**Why it matters:** it is the entire permission story. "It cannot modify, move or delete your
files" is a stronger trust claim than any privacy policy, and it is verifiable.
**Protect it:** make it a test. A CI check that greps for write-mode `open(`/`os.remove`/
`shutil.move` under `engine/` and fails the build costs ten minutes and prevents the slow erosion
that eventually breaks every indexer.

### 2.2 One file is the whole state

`catalog.db` (+ WAL) holds roots, files, text, FTS index, AI artifacts, translations and
preferences. That makes the product trivially backup-able, inspectable with any SQLite browser,
and migratable. Most competitors scatter state across caches and a cloud account.

**Protect it:** do not introduce a second datastore for embeddings (§4.2 keeps vectors *inside*
SQLite), and do not let a plugin write outside the data dir.

### 2.3 Injection-safe FTS5 with a real query language

`build_match` tokenises user input, quotes each token, applies `*` prefix wildcards, and allows
column filters from a fixed map. `_file_clause` binds category/root as parameters. There is no
f-string interpolation of user input in `db.py`.

**Protect it:** any new query surface (semantic, filters, saved searches, MCP tools) must funnel
through the same builder. The moment one path concatenates a query string, the property is gone.

### 2.4 Provider abstraction that already covers the local-first case

`providers.py` models 11 presets across three wire protocols. Because OpenAI-compatible covers
Ollama, LM Studio, llama.cpp and vLLM, the app is **already fully functional with zero network
egress** — that is rare and commercially important (regulated users, air-gapped machines).

**Protect it:** when adding features, never hard-code a vendor. The plugin registry (§4.7) is the
right generalisation of what already works.

### 2.5 Graceful degradation instead of a dependency wall

`extract.py` guards PyMuPDF, python-docx, openpyxl, python-pptx, Pillow, pytesseract,
extract-msg and py7zr behind try/except imports. The one-file exe therefore works even when a
format handler is absent, and OCR can be enabled later by pointing at Tesseract.

**Protect it:** keep every new integration (transcription, vision, reranking) behind the same
optional-import discipline, and surface capability in the UI ("audio not indexed — enable
Whisper") rather than failing silently.

### 2.6 A disciplined cross-thread contract

Workers only put dicts on a queue; the Tk loop drains it every 80 ms. No widget is touched off
the main thread (two `after(0, …)` escapes aside). This is what keeps a 200k-file scan from
freezing the window.

**Protect it:** formalise it as a typed message union (§4.4), and never let a service take a
widget or a Tk variable as a parameter — the `TranslateDialog` passing `"dlg": self` through the
queue is the anti-pattern to extinguish.

### 2.7 Instrumentation that is honest

Rotating log, Activity page, per-root scan summaries with indexed/unchanged/removed/errors/
duration, and a crash screen naming the log path. Most desktop apps ship with none of this.

**Protect it:** keep it local and human-readable. Telemetry is opt-in and additive (§4.5), never
a replacement.

---

## 3. The current ceiling, in code

Before proposing changes, the specific reasons the AI features plateau. All verified by reading
the code; line numbers from the current tree.

| Limit | Evidence | Consequence |
|---|---|---|
| **One vector per file** | `svc.embed_doc` embeds `clip(f"{name}\n{content}", MAX_CONTENT // 2)` — a single 45,000-char slice (`svc.py:141-150`, `MAX_CONTENT=90_000`) | A 300-page PDF gets one vector of its first ~45k chars. Anything after that is semantically invisible. |
| **Input length can exceed model limits, silently** | 45k chars ≈ 11k tokens; OpenAI embedding models cap at 8,191 tokens. The failure is caught by `except AIConnectionError: pass` inside `index_ai` (`svc.py:305-311`) | Large documents may never get an embedding at all, and the user is told enrichment "completed". |
| **Whole-document cosine in pure Python, every query** | `semantic_search` → `embedding_rows()` loads all blobs → `_unpack` per row → `_cosine` (`svc.py:169-184`) | O(n) unpacks + Python loops per query; no ANN, no cache, no normalisation. Fine at 2k docs, painful at 50k. |
| **Retrieval discards scores and never fuses** | `_retrieve` takes semantic top-8, pushes full content, falls back to FTS only when `len(hits) < 3` (`svc.py:234-254`) | No rank fusion; a mediocre semantic hit outranks an exact keyword match; if embeddings exist but are bad, FTS never gets a say. |
| **Citations are filenames, resolved by name** | `ask` matches `[file: name]`, then `_path_for(name)` → `by_name` → `ORDER BY size DESC LIMIT 1` (`svc.py:202-232`) | Two files named `report.pdf` cite the wrong path; no page/offset, so "jump to source" is impossible. |
| **Six whole excerpts, ~15k chars each** | `ask` clips each to `MAX_CONTENT // 6` (`svc.py:214-220`) | Context is spent on preamble and boilerplate rather than the relevant passage. |
| **AI artifacts never invalidated on change** | `summarize_doc`/`auto_tag`/`embed_doc` return cached values whenever a value exists (`svc.py:109,124,141`); the scanner rewrites `docs` but never touches `docmeta` | Edit a file and its summary, tags and vector describe the *old* version — forever. This is a correctness bug users will notice. |
| **Enrichment only ever sees 3,000 smallest files** | `files_for_ai(limit=3000)` → `ORDER BY f.size ASC LIMIT 3000` (`db.py:480`) | On a 50k library the same small files are re-examined every run; large documents are never reached, no matter how many times you press the button. |
| **Per-document commits and read-modify-write** | `upsert_meta` does `get_meta` then `UPDATE` then `commit` (`db.py:366-400`) | Every summary/tag/embedding is 3 round trips + a WAL commit; slow and unnecessary. |
| **No cancellation, no retry, no cost accounting** | `index_ai` is a plain loop with a `stop` callable; `_auto_ai_enrich` passes `stop=lambda: False` | A rate-limit at file 400 loses all remaining work (progress is per-file, state is only what made it into `docmeta`). |

That list is the real answer to "what would make a difference": not a new model, but **granularity,
fusion, provenance, invalidation and durability**.

---

## 4. Improvement ladder

Each item is independently shippable, ordered so that later items are cheaper. Effort is
engineer-days for one experienced Python developer, including tests.

### E1. Trust foundation — 2–3 d *(prerequisite for everything else)*

1. Fix the five `sqlite3.Row.get()` crashes at the boundary (see review §9.1) with a single
   `row_get(row, key, default=None)` helper, and add assert-based coverage for export, preview,
   favourite, notes and `index_ai`.
2. `PRAGMA user_version` + a `migrations.py` runner; delete `docmeta`/`translations` in
   `mark_deleted`; `AUTOINCREMENT` on `files.id`.
3. Move provider keys to Windows DPAPI / `keyring`, migrating existing plaintext values on load.
4. Log every swallowed failure outside `extract.py` (`logger.warning(..., exc_info=True)`).

**Payoff:** unblocks rescanning old libraries, stops silent data loss, and makes the next nine
items safe to build on. **No user-visible feature** — which is exactly why it must come first.

### E2. Chunked hybrid retrieval with reranking and true provenance — 6–10 d

The single highest-leverage change in this document.

**Schema**

```sql
CREATE TABLE chunks(
  id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL,
  ord INTEGER NOT NULL,          -- position in document
  page INTEGER,                  -- PDF page / sheet / slide, when known
  start_char INTEGER, end_char INTEGER,
  text TEXT NOT NULL,
  token_est INTEGER
);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='id',
                                           tokenize='unicode61');
-- vectors: either a chunk_vectors table with BLOB + norm, indexed by sqlite-vec,
-- or keep them in chunks and brute-force in numpy (see below)
CREATE INDEX chunks_doc ON chunks(doc_id);
```

**Chunking.** 800–1,200 tokens with 15 % overlap, split on paragraph then sentence boundaries,
recording `page` and `start_char/end_char`. Respect the extractor's own structure: PDF pages,
DOCX paragraphs, XLSX rows, PPTX slides. Store `content_hash` per doc in `docmeta` so
invalidation is a comparison, not a guess.

**Retrieval.** Retrieve 40–60 candidates from **both** channels (FTS5 BM25 over `chunks_fts` and
vector similarity over chunks), fuse with **Reciprocal Rank Fusion** (`score = Σ 1/(60+rank)`),
optionally rerank the top 20 with a cross-encoder, then take 6–8 chunks into the prompt. Fusion
is ~20 lines and is what makes "hybrid" actually hybrid instead of "semantic, else keyword".

**Provenance.** Pass chunks to the model with stable ids (`[#17] name · p.4`), require the model
to cite ids, then map ids back to `(doc_id, page, start_char)` server-side — never parse
filenames. The UI can then scroll the preview to the cited offset and highlight it, which is the
feature that makes an assistant feel trustworthy.

**Vectors.** Two viable paths, both keeping state inside SQLite:

- *Minimal:* store float32 (or float16) blobs in `chunks`, load into a `numpy` matrix once per
  process, cache it, and do `matmul` — a 50k×1536 float32 matrix is 300 MB, so use float16
  (150 MB) or quantise; normalise at write time so cosine is a dot product.
- *Scalable:* `sqlite-vec` (MIT/Apache-2.0, verify before bundling) gives an in-process ANN index
  as a SQLite extension with `vec0` virtual tables — same file, same backup story, no server.
  Guard it like every other optional dependency.

**Also in this item:** batch embedding requests (64–256 chunks per call), cap chunk text well
inside the model's input limit, and record `embed_model` + `dim` per row so switching providers
triggers a controlled re-index instead of silently skipping mismatched vectors.

**Payoff:** answers cite the right paragraph; keyword-exact questions stop being outranked by
vaguely similar documents; the same index powers "related files" at paragraph level. This is the
difference between a demo and a tool.

### E3. Content reach — 6–10 d

The index currently covers text, PDFs, Office documents, archives and (optionally) images via
OCR. The three biggest blind spots are **audio/video**, **image semantics**, and **structure**.

| Add | Local-first default | Hosted option | Unlocks |
|---|---|---|---|
| Speech-to-text | `faster-whisper` (CTranslate2, CPU/GPU, MIT — verify) with `ffmpeg` bundled; model chosen by VRAM | Deepgram / AssemblyAI / Groq Whisper | Meeting notes, voice memos, lectures, podcasts, screen recordings |
| Image understanding | CLIP embeddings for visual similarity; OCR already present | Any vision-capable chat model (opt-in, per-file) | Screenshots, photos of whiteboards, scanned receipts |
| Structure | Docling (layout + tables → Markdown) or MarkItDown as a structured fallback alongside existing extractors | Azure Document Intelligence / Google Document AI / AWS Textract for hard scans | Tables that are actually searchable; reading order that survives two-column PDFs |
| Code awareness | `tree-sitter` symbols (functions, classes, imports) into a `symbols` table | — | "where is `parse_config` defined" across a repo |
| Email/calendar | `.eml` parsing (stdlib) beyond the existing `.msg` support | — | Attachments and threads become first-class |

Transcription is the highest-value single addition: it converts hours of unsearchable media into
text with timestamps, and timestamps give you the same "jump to the moment" provenance as E2's
page offsets.

### E4. Durable enrichment queue — 5–8 d

Replace the fire-and-forget `index_ai` loop with a real work queue in SQLite:

```sql
CREATE TABLE ai_jobs(
  id INTEGER PRIMARY KEY,
  doc_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,            -- summary | tags | embed | transcribe | caption
  state TEXT NOT NULL,           -- queued | running | done | failed | skipped
  attempts INTEGER NOT NULL DEFAULT 0,
  next_at REAL, last_error TEXT,
  content_hash TEXT,             -- the version this job was computed from
  UNIQUE(doc_id, kind)
);
```

Properties this buys, each of which is a current defect:

- **Resumability** — a crash or a closed laptop resumes where it stopped.
- **Invalidation** — the scanner enqueues a job whenever `content_hash` changes, so summaries and
  vectors stop describing the previous version of a file (today they never expire).
- **No 3,000-file ceiling** — the queue is ordered by priority (favourites, recently viewed,
  small-first), not by an arbitrary `LIMIT`.
- **Retry with backoff** — 429s and transient 5xx become `next_at = now + 2^attempts`, not
  "interrupted".
- **Real cancellation** — a `stop` flag halves the queue instead of being ignored.
- **Cost and progress you can show** — tokens/chunks processed, estimated spend per provider,
  ETA. Users tolerate a slow, visible batch job; they do not tolerate an opaque one.
- **Crash-safe per-unit commits** — one job row per document instead of three round trips and a
  WAL commit per field.

### E5. Always-fresh index — 3–5 d

- Replace the polling walk with `watchdog` (ReadDirectoryChangesW on Windows) and keep polling as
  a fallback. The current "cheap signature" is a full recursive `stat` pass every 15 s.
- Debounce bursts (editors write 5–10 events per save), coalesce by path, and enqueue a
  **re-extract + re-embed job for just that file** rather than a whole-tree rescan.
- Show "indexed 3 s ago · watching 4 folders" in the status bar — freshness that is visible is
  freshness that is trusted.

### E6. Near-duplicate intelligence — 3–5 d

MD5 only finds byte-identical files. Add, in order of value:

1. **Text near-duplicates** — MinHash/SimHash over extracted content, bucketed in SQLite. Finds
   the same contract as PDF, DOCX and a scanned copy.
2. **Image near-duplicates** — `imagehash` perceptual hashes (pHash/dHash) with a Hamming
   threshold; groups resized/re-encoded copies.
3. **Audio fingerprints** — Chromaprint for the same song/recording at different bitrates.

Surface as duplicate *groups* with a reason ("identical bytes", "98 % text similarity", "visually
identical"), and never offer deletion of originals — only "open containing folder" and export of
the report, consistent with §2.1.

### E7. Programmable surface — 4–7 d

This is the "wider horizon" multiplier: it turns a closed desktop app into a platform, and it is
cheap because the engine is already UI-free.

- **CLI** (`droplens search "q" --json`, `droplens stats`, `droplens scan`, `droplens ask "q"`):
  composes `Catalog` + `ScanManager` with no engine change. Enables scripting and CI.
- **MCP server** (`droplens mcp`, stdio transport): exposes `search`, `read_document`,
  `list_folders`, `ask` as tools so coding agents and chat clients can query the local index
  directly. High novelty, low cost, and it makes the read-only guarantee a selling point.
- **Local HTTP API** bound to `127.0.0.1` with a per-session bearer token, for browser extensions,
  Obsidian plugins, Alfred/Raycast/PowerToys Run launchers and Stream Deck.
- **Plugin registry via `importlib.metadata` entry points** (`droplens.extractors`,
  `droplens.providers`, `droplens.embedders`, `droplens.connectors`): the documented way to add a
  format, a model protocol or a data source without forking. Version the plugin ABI from day one.

Design rule: every tool call goes through the same `db.py` query builder and the same permission
check ("this root is indexed"), so §2.3 and §2.1 survive the expansion.

### E8. Trust and privacy layer — 3–5 d

- **"What leaves this machine" panel** — per-provider, per-feature egress log (which document,
  how many chars, when), inspectable and clearable.
- **Offline-only mode** — one switch that disables every non-loopback destination, including the
  Google Translate endpoint (which today is an undocumented, unconfigured outbound call).
- **Redaction before send** — regex passes for API keys, card numbers, emails, national IDs, with
  a preview of what was removed.
- **Secrets** in DPAPI (§E1), never in `settings.json`.
- **Signed binaries** (Azure Trusted Signing or an EV certificate) and a reproducible build note,
  because an unsigned indexer of personal files is a hard sell to any IT department.

### E9. Portability — 4–6 d

- **Encrypted index snapshot** (`age`/libsodium) with export/import, so a user can move to a new
  machine without re-indexing 200k files.
- **Merge by content hash** for multi-device use without syncing the files themselves — the index
  is portable, the user's documents never leave their disks.
- **Retention controls**: drop `docs` content above N chars, prune orphaned `docmeta`, `VACUUM`
  on demand (today the index only grows).

### E10. Distribution and commerce — 4–8 d

`winget` / Scoop manifests, WinSparkle-style auto-update with signed delta packages, a trial +
license-key flow (Paddle, Lemon Squeezy, Polar or Keygen — pick one, all are standard for
one-time-purchase desktop software), and Windows Explorer integration via `IExplorerCommand`
rather than a shell-extension DLL.

---

## 5. Integration horizon

Two ground rules that follow from the architecture: **local-first is the default**, and every
hosted service is **opt-in, per-provider, and visible in the egress panel**. The app's promise is
that it works on a plane.

### 5.1 Model and embedding providers

| Need | Local-first | Hosted | Notes |
|---|---|---|---|
| Chat / summarise / answer | Ollama, LM Studio, llama.cpp, vLLM (already supported via OpenAI-compatible presets) | OpenAI, Anthropic, Gemini (**supported today**), Mistral, Groq, OpenRouter, Together, DeepInfra, **Baseten** (OpenAI-compatible chat + embeddings, pay-as-you-go; the only service in the discovery catalog that documents both) | The abstraction already exists — additions are preset rows, not code |
| Embeddings | `bge-m3`, `nomic-embed-text`, `all-MiniLM-L6-v2` via Ollama / sentence-transformers | OpenAI `text-embedding-3-*`, Voyage, Cohere, Jina | Must record `dim` + model per vector (§E2) |
| Reranking | `bge-reranker-v2-m3`, `mxbai-rerank` (ONNX/CPU) | Cohere Rerank, Jina Reranker | Highest quality-per-millisecond win in RAG; optional, off by default |
| Structured extraction | Local instruct model with JSON schema | Any provider with JSON mode | Invoices, receipts, contracts → columns |

### 5.2 Content and media

| Capability | Local | Hosted | Why |
|---|---|---|---|
| OCR | Tesseract (present), PaddleOCR/RapidOCR | Azure Document Intelligence, Google Document AI, AWS Textract | Scans, photos, screenshots |
| Document structure | Docling, MarkItDown, Unstructured | LlamaParse | Tables, reading order, layout |
| Speech-to-text | faster-whisper, whisper.cpp | Deepgram, AssemblyAI, Groq | The biggest missing content class |
| Vision tagging | CLIP / SigLIP, moondream | Any vision chat model | Photos and screenshots become findable |
| Translation | LibreTranslate (self-host), local NLLB/M2M | DeepL, Google Cloud Translation v3 | Replaces the undocumented `gtx` endpoint with something with terms and quotas |
| Entity extraction | spaCy, GLiNER | — | People/orgs/dates → faceted search |

### 5.3 Infrastructure and operations

| Need | Option | Fit |
|---|---|---|
| Vector index | `sqlite-vec` (in-process), `numpy` brute force, LanceDB (embedded) | Keeps one-file state; **no server** |
| Full-text at scale | stay on FTS5; `tantivy-py` if ranking needs grow | FTS5 is not the bottleneck yet |
| Secrets | Windows DPAPI / `keyring` | Required before any commercial release |
| Crash reporting | Sentry SDK or self-hosted GlitchTip, **opt-in**, scrubbed payloads | The catalog has no match; a library choice |
| Analytics | none by default; if ever, self-hosted, aggregate-only | A local indexer asking for telemetry must ask very politely |
| Update delivery | WinSparkle, signed delta packages, `winget` | Desktop-appropriate |
| Licensing | Paddle, Lemon Squeezy, Polar, Keygen | One-time purchase + trial |
| Index backup | user-chosen folder, S3/R2-compatible bucket, or Syncthing | Encrypt client-side; never upload documents |
| Automation | MCP server, localhost HTTP, CLI | The platform play (§E7) |
| Launchers | PowerToys Run, Raycast, Alfred, Stream Deck | Localhost API consumers |
| Notes/knowledge tools | Obsidian vault export, Notion import/export | Summaries and links travel with the user |

**Discovery-catalog note.** A service-discovery pass for embedded vector search, document
parsing/OCR, crash reporting, transcription, translation and desktop licensing returned no
matching services for those categories — they are library or self-hosted decisions, which suits
an offline-first desktop app better anyway. Baseten came back as the one grounded service option
for OpenAI-compatible chat **and** embeddings, and it fits the existing `providers.py` shape
without new protocol code.

---

## 6. Sequencing and measurable outcomes

| Release | Scope | Target outcome |
|---|---|---|
| **2.0.1 (hotfix)** | E1 items 1–2; the export/preview/favourite/notes/enrich crashes; migration + cascade; upload docs to the repo | Zero known crash paths; existing v1 libraries upgrade safely |
| **2.1 (retrieval)** | E2 + E4 skeleton + the `_fill` N+1 JOIN; pgvector-free, all in SQLite | Answer cites the right paragraph; search p95 under 300 ms at 50k docs; enrichment resumable and cancellable |
| **2.2 (reach)** | E3 transcription + image captions + E5 native watcher | Media is searchable; edits reflected within seconds |
| **2.3 (platform)** | E7 CLI + MCP + localhost API + plugin entry points | Third-party launchers and agents can query the index |
| **2.4 (trust/commerce)** | E8 + E9 + E10 | Signed, updatable, license-managed, portable index |

Metrics worth instrumenting **locally** (never uploaded) to know whether it worked:

| Metric | Today (est.) | Target |
|---|---|---|
| Search p95, 50k docs | dominated by N+1 metadata (up to 5,000 extra queries) | < 300 ms |
| Answer citation accuracy (does the cited file contain the answer?) | filename-level, wrong on duplicate names | > 90 % at paragraph level |
| Enrichment completion rate over 10k files | limited to 3,000 smallest, no resume | 100 % of queued jobs, resumable |
| Staleness after editing a file | never invalidated | re-embedded within one watch cycle |
| Indexable content classes | text/PDF/Office/archives (+OCR) | + audio, video, images, code symbols |

---

## 7. Anti-goals

Equally important, because each one has tempting short-term appeal:

1. **Do not add a mandatory server.** No Docker, no Postgres, no local daemon beyond the app. The
   one-file index is the product's integrity.
2. **Do not sync user documents.** Sync the *index*, encrypted, on demand. A "Dropbox for your
   files" pivot destroys the trust story and the read-only guarantee.
3. **Do not build a plugin marketplace before a plugin ABI.** Entry points and a versioned
   contract first; discovery later.
4. **Do not chase ANN before chunking.** A perfect index over whole-document vectors still cannot
   retrieve the right paragraph.
5. **Do not let AI write to the catalogue's truth.** Summaries, tags and vectors are derived data
   in `docmeta`; `files`/`docs` stay authoritative and reproducible by rescanning.
6. **Do not ship telemetry by default.** Opt-in, aggregate, documented.
7. **Do not add a second database engine "for search".** FTS5 plus chunk vectors covers the next
   order of magnitude; a second store doubles the backup, migration and trust surface.

---

## 8. If only one thing ships

**E2, chunked hybrid retrieval with reranking and id-based provenance**, plus the E1 correctness
fixes it depends on.

It is the only change that improves every AI surface at once — search, related files, the
assistant's answers, and folder reports — without adding a vendor, a server, or a new data store.
Everything else on this list makes DropLens broader; this makes it *right*.

---

## 9. Implementation status — 2.1.0

What this document recommended and what has actually shipped (see
[`IMPLEMENTATION_NOTES.md`](IMPLEMENTATION_NOTES.md) for the file-level detail).

| Item | Status | Notes |
|---|---|---|
| **E1** trust foundation | **done** | `row_get` at every Row/dict boundary; `user_version` migrations; cascade deletes; DPAPI keys; swallowed failures logged; dead settings wired or removed |
| **E2** chunked hybrid retrieval | **done** | `engine/chunk.py`, `chunks` + `chunks_fts` + `chunk_vectors`, RRF fusion, batched embeddings, `[#id]` provenance with page + offsets |
| **E4** durable enrichment queue | **done** | `ai_jobs`, priority ordering, exponential backoff, resumable, cancellable, `content_hash` invalidation |
| **E7** programmable surface — CLI | **done** | `python -m dropLens search/stats/scan/ask/export/egress/jobs`, `--json` |
| **E5** always-fresh index | **partial** | debounced watcher + per-file re-extraction on change; native `ReadDirectoryChangesW` (watchdog) still to do |
| **E8** trust layer | **partial** | offline-only switch, egress log, DPAPI secrets done; redaction-before-send and signed binaries open |
| **E3** media reach | **open** | needs new optional dependencies (faster-whisper, ffmpeg, CLIP) — deliberately not shimmed |
| **E6** near-duplicate intelligence | **open** | needs MinHash/imagehash; the schema has room but nothing was added prematurely |
| **E9** portability | **open** | encrypted snapshot export/import not implemented |
| **E10** distribution & commerce | **open** | winget/Scoop, auto-update, licensing untouched |

Reranking (part of E2) is intentionally **not** wired: with chunk-level retrieval in place the
quality gain is small compared with the cost of shipping a cross-encoder runtime, and the fusion
hook in `AIService.retrieve` is where it will slot in later.

Known limitation: chunk vectors are brute-forced in Python (with an optional NumPy fast path).
That is comfortable to ~50k chunks; `sqlite-vec` remains the answer beyond that, exactly as §E2 said.
