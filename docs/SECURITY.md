# DropLens — Security & Trust Model

**Scope:** the desktop application in this repository (DropLens 2.3.0) and the artifacts
built from it (`DropLens.exe`, `Setup-DropLens*.exe`, the `droplens` wheel/CLI).

DropLens reads every file a user points it at. This document states — precisely and
without marketing language — what the software does with that data, what it refuses to do,
and where the residual risk is.

---

## 1. Trust summary

| Question | Answer |
|---|---|
| Does it modify, move, rename or delete user files? | **No.** There is no write path from `dropLens/engine/` to a scanned path. Enforceable and verifiable (`scripts/check_readonly.py`). |
| Where is the index stored? | One SQLite file (`catalog.db`) under `%LOCALAPPDATA%\DropLens`, or `DROPLENS_DIR` / Settings → Data location. |
| Does it need an account or a server? | **No.** Local models via Ollama / LM Studio / llama.cpp / vLLM run fully offline. |
| What leaves the machine? | Only what you configure: the excerpts sent to a **cloud** AI provider you added, and Google Translate when you click *Translate*. Both are logged. |
| Can outbound traffic be blocked completely? | **Yes** — Settings → Privacy → *Offline-only*. One switch in `dropLens/net.py::check()` rejects every non-loopback destination. |
| Where are API keys stored? | Sealed with **Windows DPAPI** (`dpapi:<base64>`) in `settings.json`, bound to the current Windows user. Not plaintext. |
| Is there telemetry, crash reporting or analytics? | **None.** No remote logging endpoint exists in the code. |
| Are the binaries signed? | Not yet by default — see §7. Signing is a release task, not a code change. |

---

## 2. Data flow

```
user folders ──(read-only)──▶ extractor ──▶ SQLite catalog.db  (local)
                                             │
                       ┌─────────────────────┼──────────────────────┐
                       ▼                     ▼                      ▼
              local model (Ollama)     cloud provider        Google Translate
              nothing leaves           excerpts only         only on click
                       │                     │                      │
                       └──── net.check() ────┴──────────────────────┘
                                  │
                            net.log_egress() ──▶ egress table (local, inspectable)
```

Invariants enforced in code:

1. **Read-only index.** `engine/` opens user files with `"rb"` only. Exports write to a path
   the user chose. There is no `os.remove`, `shutil.move` or write-mode `open()` on a
   scanned path.
2. **One egress choke point.** `dropLens/ai/client.py` and `engine/translate.py` call
   `net.check(url)` before any request and `net.log_egress()` after it. New network code is
   expected to do the same; the review checklist in `docs/RELEASE_CHECKLIST.md` verifies it.
3. **Scope of what is sent.** For summaries/tags/answers only the retrieved excerpt or the
   clipped document content (≤ 90 000 chars, or the configured top-k chunks) is sent — never
   the catalogue, never file listings you did not ask about.
4. **Key handling.** Keys are read from `settings.json` into memory, used in an
   `Authorization: Bearer` / `x-api-key` / `x-goog-api-key` header, and never written to
   logs. Gemini keys are **not** placed in the URL query string (that would leak keys into
   proxy and server logs — it is a deliberate deviation from Google's example).
5. **No shell interpolation.** File open/reveal uses `subprocess.Popen([...])` with an
   argument list on Windows, and `os.startfile`. No `shell=True` anywhere.

---

## 3. Credential and data-at-rest handling

| Item | Protection | Residual risk |
|---|---|---|
| Provider API keys | DPAPI (`CryptProtectData`, user-scoped, with app entropy) | Any process running **as that same Windows user** can unseal them. DPAPI is not a defence against local code execution. |
| `catalog.db` | Plain SQLite — **not encrypted** | Anyone with file access to the profile can read the index. Use BitLocker / an encrypted volume for confidential libraries. |
| `droplens.log` | Local rotating log, no keys, no file contents beyond paths and counts | Paths appear in the log; treat it as sensitive metadata. |
| Egress log | Local table, clearable from the UI or `droplens egress --clear` | Contains provider hostnames and payload sizes. |
| Translation cache | Local `translations` table | Contains the translated text of indexed documents. |

If you need an encrypted index at rest, put the data directory on an encrypted volume
(BitLocker/FileVault/LUKS). DropLens will not silently invent its own scheme — see the
E9 item in `docs/ENHANCEMENT_AND_INTEGRATION_ROADMAP.md`.

---

## 4. Plugins — the one place where arbitrary code runs

`<data_dir>/plugins/*.py` is imported with `importlib` and receives an `ExtensionAPI`.
Plugins are **user-installed Python code with full process privileges**. The registry
isolates *failures* (an exception is recorded and the plugin is skipped), not *intent*.

Consequences you must plan for:

- Only deploy plugins you or your administrators have reviewed.
- Set `plugins_enabled: false` in `settings.json` to disable loading entirely — the supported
  way to lock the feature down in a managed fleet.
- A plugin gets no catalogue handle and no network helper, but it can still `import requests`
  itself. The narrow API is a *design* boundary, not a sandbox.

---

## 5. Prompt-injection and model-trust boundary

The assistant reads document text that may contain hostile instructions. Current mitigations:

- System prompts instruct the model to answer only from retrieved excerpts and to cite ids.
- Citations are **verified server-side**: an id the model prints is looked up in `chunks`;
  ids that were never returned collapse to nothing. A model cannot fabricate a source.
- Tools are strictly read-only over the catalogue (`search_library`, `read_document`,
  `library_stats`, `list_documents`). The agent loop is bounded (`max_steps`, default 4) and
  the final turn runs without tools.
- Tool faults become error payloads, not exceptions.

Residual risk: a malicious document can steer **wording** of an answer. It cannot delete,
move or upload anything, because no such capability is exposed to the model.

---

## 6. Supply chain

- **Pinned dependencies.** `requirements-lock.txt` pins every transitive package for
  reproducible builds; `requirements.txt` holds the human-authored ranges.
- **Optional format handlers degrade, never crash.** PyMuPDF, python-docx, openpyxl,
  python-pptx, Pillow, pytesseract, extract-msg, py7zr and rarfile are imported behind
  `try/except ImportError`.
- **Advisory audit in CI.** `pip-audit` runs on every push
  (`.github/workflows/ci.yml`, job `audit`). It is advisory by design: a new advisory against
  a PDF parser should trigger an evaluation, not a red build that blocks unrelated work.
- **Review before release.** The release checklist requires reading the audit report and
  recording the decision.

---

## 7. Binary integrity and distribution

Known gaps, stated plainly:

- Artifacts are **not code-signed** in this repository's default build. Windows
  SmartScreen will warn on first run. Sign both `DropLens.exe` and
  `Setup-DropLens*.exe` with an OV/EV certificate before distributing to customers or
  employees (`docs/COMMERCIAL.md`, `build/BUILD.md`).
- There is **no auto-update mechanism**. Updates are administrator-driven. Verify the
  SHA-256 of a downloaded installer against the published release notes before deploying.
- The installer runs with `PrivilegesRequired=lowest` (per-user, no elevation) by default;
  per-machine installs are opt-in at build time.

---

## 8. Network policy reference

`dropLens/net.py::is_local_url` treats a destination as local only when the **parsed
hostname** is a loopback address, `*.local`/`*.localhost`, or an RFC 1918 private address.
Credentials embedded in the URL (`https://user@host`) are rejected outright rather than
trusted, so a crafted `http://127.0.0.1@attacker.example` cannot masquerade as loopback.

Outbound destinations the code can ever produce:

| Feature | Destination | Gate |
|---|---|---|
| Local AI | `http://localhost:<port>/v1/...` | always allowed |
| Embedded Respan Gateway | `http://127.0.0.1:<ephemeral>/v1` | loopback only, in-process |
| Cloud AI | the `base_url` you configured | `net.check` + egress log |
| Translation | `translate.googleapis.com` | `net.check` + egress log, per click |
| Updates / telemetry | **none exist** | — |

---

## 9. Reporting a vulnerability

Do not open a public issue for a security defect. Email the maintainer listed in the
repository profile with:

1. the affected version (`droplens stats --json` reports it) and platform,
2. a minimal reproduction (a file or config that triggers it),
3. the impact you believe it has.

Target acknowledgement: 3 business days. Please allow a fix window before public disclosure.
