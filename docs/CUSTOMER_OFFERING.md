# DropLens 2.1 — Customer Offering & Improvement Scope

Internal source of truth for what we sell, what the customer actually receives, and exactly how
much better it is than 2.0. Written to be reused: the “Offering” half is customer-facing, the
“Scope of improvement” half is the evidence pack behind it.

Version: **2.2.0** · Engine verified on Linux · GUI verification pending on Windows (see §7).

---

## 1. The one-liner

**DropLens is a private, offline-first search and understanding layer for every file you own.**
Point it at your folders and it makes hundreds of thousands of documents, spreadsheets, PDFs,
e-mails, archives and code files searchable by keyword and by meaning — on your machine, without
an account, a server, or a single byte of your library uploaded.

Positioning line for a landing page:

> Your files already contain the answers. DropLens finds the exact paragraph — offline.

---

## 2. What we deliver to customers

Five promises. Each one is a thing the customer can verify in the first ten minutes, which is why
they are phrased as behaviour rather than features.

### 2.1 It finds the passage, not just the file

Ask “what did we agree about renewal terms?” and the answer cites **the paragraph** it came from —
`[#42] master-services.pdf · p.7` — not a filename. Double-click the citation and the preview
scrolls to that passage with it highlighted.

*Why it matters:* a search result you still have to re-read is only half a result. Paragraph-level
citations are the difference between “here are 8 files” and “here is the clause”.

### 2.2 It never lies about what it knows

- Summaries, tags and vectors are tied to the **version of the file** they were computed from.
  Edit a document and its old summary is dropped instead of being served forever.
- If the model cannot answer from your files, it says so. Answers are built only from retrieved
  excerpts, and every claim carries a reference id that is resolved against the index.
- Empty, unreadable and OCR-less files are labelled as such (`no_content`, `ocr_unavailable`)
  rather than silently treated as empty documents.

### 2.3 Your library stays yours

- The catalogue is **one SQLite file** you can open, copy, back up or delete.
- **Originals are read-only inputs.** There is no code path in the engine that writes to a scanned
  file — not a policy, an absent capability.
- **Offline-only switch**: one checkbox blocks every non-local request, including the translation
  endpoint.
- **“What leaves this machine” log**: every outbound request (provider, character count, when) is
  recorded locally and is inspectable from Activity → *Network log…* or `dropLens egress`.
- API keys are sealed with **Windows DPAPI**, so a copied `settings.json` is worthless on another
  machine.

### 2.4 It survives real libraries

- **Resumable AI enrichment**: close the laptop at file 4,000 of 50,000 and it continues where it
  stopped; a rate limit schedules a retry with backoff instead of abandoning the run.
- **No file-count ceiling** on enrichment, and no “small files only” bias: favourites and small
  files first, then everything else.
- Editing one file re-indexes that file; the watcher coalesces a save-burst into one incremental
  pass.
- Interrupt any scan or enrichment: what completed is committed, what did not is still queued.

### 2.5 It is automatable, not a silo

```bash
python -m dropLens search "gdpr retention" --json     # machine-readable results
python -m dropLens stats                              # files, chunks, vectors, queue
python -m dropLens scan "D:/Projects" --quiet         # index from a script or CI job
python -m dropLens ask "what is our notice period?"   # answers + structured citations
python -m dropLens export --out catalogue.csv         # audit / inventory
python -m dropLens egress                             # what left this machine
```

The engine was already UI-free; 2.1 exposes it. Anything a launcher, a script or an assistant can
do with a folder of files, it can now do against your index.

---

## 3. What ships in 2.1.0 and 2.2.0 (customer view)

| Customer-visible change | What they will notice |
|---|---|
| Paragraph-level answers with page numbers | Answers point at a page, and the preview jumps there |
| Hybrid keyword + meaning retrieval, fused | Exact-term questions stop losing to vaguely similar documents, and vice versa |
| Stale AI data is invalidated | Editing a document stops showing last month’s summary |
| CSV / TXT export actually works | Export used to crash on the first row; it now writes the catalogue, results and translations |
| AI enrichment resumes and can be stopped | The Stop button stops; closing the app no longer loses the run |
| Offline-only mode + network log | A security reviewer can see and block every outbound call |
| API keys sealed with DPAPI | Credentials are no longer plaintext in `settings.json` |
| CLI with `--json` | The library is usable from scripts, CI and other tools |
| Plugins for formats, tools and hooks | A new file type or an assistant capability is a file drop, not a release |
| Assistant that can call tools (`ask --tools`) | Multi-part questions (“compare both invoices”) can search, read and then answer with citations |
| Faster large-library search | Search no longer issues one metadata query per result row |
| Background scans use several threads | Large trees index faster; the setting now does something |

Kept exactly as they were (they are the reasons people stay): the single-file catalogue, the
provider abstraction (11 presets, local or cloud, OpenAI-compatible), graceful degradation when an
optional format handler is missing, the read-only guarantee, and the cross-thread queue contract
that keeps the window responsive.

---

## 4. Scope of improvement — before → after

The 2.0 ceiling was not “the model is not good enough”. It was granularity, provenance, freshness
and durability. Each row below is a change we can point at in code.

### 4.1 Retrieval depth

| Dimension | 2.0 | 2.1 |
|---|---|---|
| Unit of retrieval | one vector per file | one vector per paragraph-sized chunk (~800–900 tokens) |
| Largest document coverage | first 45,000 characters only | entire document, page by page |
| Channels | semantic top-8; keyword only if fewer than 3 hits | BM25 **and** vectors on every query, fused with Reciprocal Rank Fusion |
| Prompt payload | 6 whole excerpts, ~15k chars each | top 8 chunks, budgeted per provider (16k local / 60–120k cloud) |
| Citation | `[file: name]`, resolved by filename, wrong on duplicates | `[#id]` resolved to `(doc_id, page, start_char, end_char)`, exact |
| “Jump to source” | impossible | opens the file and highlights the cited passage |
| Embedding requests | one per document, 45k chars (may exceed model limits) | batched, 32 chunks per request, 8,000-char cap per chunk |
| Provider switch | silently skipped dimension mismatches | model + dimension recorded per vector |

### 4.2 Freshness and durability

| Dimension | 2.0 | 2.1 |
|---|---|---|
| Enrichment scope | `ORDER BY size ASC LIMIT 3000` — same small files forever | durable queue, priority ordered, no ceiling |
| Interrupt a run | remaining work lost | remaining work stays queued |
| Provider error | run ends, “interrupted” | retry with `2^attempts` backoff, then a recorded failure |
| Edited file | summary/tags/vector describe the old version forever | artifacts dropped when `content_hash` changes |
| Progress | per-file counter, no resume | per-job rows in SQLite, resumable across restarts |

### 4.3 Correctness and trust

| Dimension | 2.0 | 2.1 |
|---|---|---|
| Export | crashed on every row (`sqlite3.Row.get`) | works; covered by `tests/test_export.py` |
| Preview / favourites / notes / translate / enrich | same crash class | same fix, one boundary helper |
| Deleting a file | left orphaned metadata that could re-attach to a recycled id | cascade to docs, chunks, vectors, translations, jobs |
| Schema upgrades | none — an old library could not be migrated | `PRAGMA user_version` + idempotent migrations |
| Search metadata | one `get_meta` query per result row (up to 5,000) | selected in the same query |
| Keys at rest | plaintext in `settings.json` | DPAPI-sealed; plaintext migrated on save |
| Egress | invisible; translation endpoint undocumented | offline-only switch + per-request local log |
| Silent failures | `except Exception: pass` in scan/watch/AI | logged with tracebacks |

### 4.4 Surface area

| Dimension | 2.0 | 2.1 |
|---|---|---|
| Automation | GUI only | 7 CLI commands, `--json`, same engine and permissions |
| Settings that work | `threads` and `max_content_chars` were dead | extraction pool sized by `threads`; stored-text cap applied |
| Format edge cases | `.rar` sent to `py7zr` (cannot read RAR); `.ico` decoded as text | `.rar` via `rarfile`; `.ico` treated as an image |
| Page structure | PDF pages flattened | form feeds preserved into page numbers and citations |
| Watcher | full recursive stat walk every 15 s, fired on every burst | debounced (one incremental pass per burst), never dies on an unreadable root |

### 4.5 What did **not** get better (stated plainly)

- **No measured speed claim.** Search now issues one query instead of N, but we have not
  benchmarked p95 on a 50k library yet. See §7 for what we may and may not say.
- **Brute-force vectors.** Chunk vectors are compared in Python (optional NumPy fast path) —
  comfortable to roughly 50k chunks, not a million.
- **No audio, video or image semantics.** Voice notes, meetings and screenshots are still not
  searchable beyond OCR'd text and filenames.
- **No near-duplicate intelligence.** Only byte-identical files are grouped.
- **No signed binaries, auto-update or licensing yet.** SmartScreen will warn until we sign.

---

## 5. Who this is for, and who it is not

**Strong fit**

- Consultants, analysts, lawyers and researchers with tens of thousands of documents and no
  appetite for uploading them.
- Regulated or air-gapped environments: offline-only mode, DPAPI keys, local models via Ollama,
  and an auditable egress log answer most security questionnaires without a cloud DPA.
- Developers and ops teams who want their archive queryable from a script (`--json`).

**Poor fit (say so early)**

- Teams that need multi-user sync, shared permissions or a web UI — that is a server product, and
  adding one would break the one-file, read-only story.
- Anyone who wants their documents in someone else’s cloud. We sync the *index*, if ever, never
  the files.
- Anyone expecting transcription of meeting recordings today.

---

## 6. Deployment fit (for IT and procurement)

| Requirement | How 2.1 answers it |
|---|---|
| “Where does data live?” | `%LOCALAPPDATA%\DropLens\catalog.db` (+ `settings.json`, rotating log). Fully portable via `DROPLENS_DIR`. |
| “Can it modify our files?” | No write path exists from the engine to a scanned path. |
| “Does it phone home?” | Not by default; offline-only mode blocks everything non-local; the egress log proves it after the fact. |
| “Where are credentials?” | DPAPI-sealed per user; never in logs, never in the catalogue. |
| “Can we pre-provision a model?” | Yes — `settings.json → ai.providers`, or a local Ollama/LM Studio endpoint with no key. |
| “Audit / migration?” | Catalogue CSV, results CSV, translation TXT, `dropLens export --out`. |
| “What about OCR?” | Optional Tesseract, auto-detected, silent-install friendly; images without it are flagged, not silently skipped. |
| Licence | MIT — internal use, redistribution, or a branded closed fork are all permitted. |

---

## 7. Proof, and what we are allowed to claim

**Verified in this tree (Linux, no GUI):**

- `python -m compileall dropLens tests` — clean.
- `tests/test_export.py` — export regression + cascade delete: pass.
- `tests/test_retrieval.py` — chunking offsets/pages, hybrid fusion (a query with **no** keyword
  match is answered by the vector channel; the same query returns nothing with embeddings off),
  chunk citations resolving to page 3 of a 3-page document, invalidation on edit, queue
  retry/backoff/cancel: pass.
- `tests/test_ai.py` — summaries, tags, embeddings, semantic ranking, related, structured
  citations, favourites, prefs: pass.
- `tests/test_engine.py` — scan (incl. parallel extraction), search, dedup, extraction, CSV
  export: pass.
- CLI smoke: `scan → stats → search (table + `--json`) → export → egress → jobs` all exit 0;
  `ask` exits 3 with a clear message when no provider is configured.

**Not yet verified:** the Tk GUI (`tests/test_gui.py` needs Windows + `tkinterdnd2`), DPAPI sealing
on a real Windows profile, Explorer integration, and any performance figure.

**Claims we may make now:** “paragraph-level citations with page numbers”, “hybrid keyword +
semantic retrieval”, “resumable, cancellable enrichment”, “offline-only mode with an egress log”,
“API keys sealed with Windows DPAPI”, “CLI with JSON output”, “export works”.

**Claims we must not make yet:** any millisecond or throughput number, “handles millions of
chunks”, “finds audio and video”, “detects near-duplicates”, “signed installer”, “enterprise SSO”.

---

## 8. What comes next (in the order the offering improves)

1. **Media reach** — local Whisper transcription with timestamp citations. Turns meetings, voice
   memos and screen recordings from dead weight into first-class, citable sources. This is the
   single biggest widening of “what is searchable”.
2. **Near-duplicate groups** — MinHash over extracted text and perceptual hashes over images, with
   a stated reason per group (“identical bytes”, “98 % text similarity”, “visually identical”).
3. **Measured retrieval quality** — a labelled question set per corpus, tracked for citation
   accuracy and answer correctness, so the next claims are numbers rather than features.
4. **Agent surface** — MCP server plus a loopback HTTP API with a session token, so assistants and
   launchers can query the index without the GUI.
5. **Trust and commerce completion** — signed binaries, signed index snapshots, auto-update,
   licence keys.

Deferred on purpose, with reasoning in [`IMPLEMENTATION_NOTES.md`](IMPLEMENTATION_NOTES.md) §5:
cross-encoder reranking, native filesystem events, a second database engine, and anything that
would put user documents on a server.

---

## 9. The honest summary

2.1 is not a feature release in the marketing sense — it is a **retrieval and trust release**. The
customer gets answers that point at a paragraph, metadata that stops lying after an edit,
enrichment that survives a closed laptop, and a network story an IT reviewer can audit. What it
does not get is new content types; that is item one of the next release.
