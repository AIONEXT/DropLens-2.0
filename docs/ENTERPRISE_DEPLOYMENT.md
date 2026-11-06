# DropLens — Enterprise Deployment Guide

Audience: IT administrators deploying DropLens to Windows fleets. Everything here works with
the shipped installer or the pip-installable package; nothing requires a server component.

---

## 1. Deployment shapes

| Shape | Good for | How |
|---|---|---|
| **Per-user install** (default) | laptops, VDI, BYOD | `Setup-DropLens-<V>.exe /VERYSILENT /NORESTART` |
| **Per-machine install** | shared workstations, RDS | build with `/DMyPrivileges=admin` (see §3) |
| **Portable exe** | USB drives, locked-down machines | copy `DropLens.exe`, no install |
| **pip package** | Linux/macOS analysis boxes, CI jobs | `pip install droplens` then `droplens …` |

The default installer needs **no administrator rights** and installs to
`%LOCALAPPDATA%\Programs\DropLens`.

---

## 2. Silent / unattended install

```powershell
# quiet install, no launch afterwards
.\Setup-DropLens-2.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-

# quiet install + accept the license page (required for unattended runs)
.\Setup-DropLens-2.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /LICENSEACCEPTED=yes /NORESTART

# uninstall silently (per-user)
& "$env:LOCALAPPDATA\Programs\DropLens\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

Deployment tooling examples:

```powershell
# Intune / SCCM: Win32 app
IntuneWinAppUtil.exe -c .\payload -s Setup-DropLens-2.3.0.exe -o .\out
# install:   Setup-DropLens-2.3.0.exe /VERYSILENT /SUPPRESSMSGBOXES /LICENSEACCEPTED=yes /NORESTART
# uninstall: "%LOCALAPPDATA%\Programs\DropLens\unins000.exe" /VERYSILENT

# PDQ Deploy / ManageEngine: push the same command line as a "Deploy Once" package.

# winget (local manifest, until it is in the community repo)
winget install --manifest .\deploy\winget\AIONEXT.DropLens.yaml
```

**Exit codes:** `0` success, `1` user cancelled, `2` fatal (Inno Setup standard).

---

## 3. Building the per-machine variant

```powershell
# build.ps1 produces the payload first
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Version 2.3.0

# per-machine installer (elevation required at install time)
iscc installer\DropLens.iss /DMyAppVersion=2.3.0 /DMyPrivileges=admin /DMyAppOutputDir=dist
```

Per-machine installs go to `{autopf}\DropLens`, install shortcuts for all users, and add an
uninstall entry under HKLM. The **catalogue still lives in each user's own profile** — that
is deliberate: an index of someone's Documents must never be shared between accounts.

---

## 4. Pre-seeding configuration (managed defaults)

Configuration is one JSON file. Seed it before first launch and the onboarding wizard is
skipped when `onboarded` is `true`.

```jsonc
// %LOCALAPPDATA%\DropLens\settings.json  (per machine+user, so use a logon script or
// a first-run task; for a roaming fleet, write it into the user's profile template)
{
  "onboarded": true,
  "offline_only": true,          // no cloud model, no translation endpoint
  "plugins_enabled": false,      // no third-party Python in a managed fleet
  "ai_auto_enrich": false,
  "watch_enabled": true,
  "watch_interval": 30,
  "threads": 6,
  "max_content_chars": 2000000,
  "tray_enabled": true,
  "notify_scan_done": false,
  "ignore_names": [".git", "node_modules", "__pycache__", ".venv", "$RECYCLE.BIN"],
  "ai": {
    "active": "Ollama (local)",
    "providers": [
      {
        "name": "Ollama (local)",
        "kind": "openai",
        "base_url": "http://localhost:11434/v1",
        "api_key": "",
        "model": "llama3.1",
        "embedding_model": "nomic-embed-text"
      }
    ]
  }
}
```

PowerShell to write it for the current user:

```powershell
$dir = Join-Path $env:LOCALAPPDATA 'DropLens'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item .\droplens-managed-settings.json (Join-Path $dir 'settings.json') -Force
```

Notes:

- **Never put a cloud API key in a pre-seeded file** — it would be plaintext on disk until the
  app re-seals it on the next save. Provision cloud keys once through the UI (they are then
  DPAPI-sealed) or use a local model.
- `data_dir` can redirect the catalogue to a managed location (e.g. a faster volume):
  `"data_dir": "D:\\Corp\\DropLens"`. The directory must be writable by the user.
- `DROPLENS_DIR` overrides everything at runtime, which is the cleanest way to keep the
  catalogue on a per-machine data volume.

---

## 5. Enforcing "no data leaves this machine"

Three layers, use one or more:

1. **Application switch** — `"offline_only": true` in `settings.json`. `net.check()` then
   rejects every non-loopback destination, including translation.
2. **Network egress policy** — block outbound HTTP(S) for `DropLens.exe` at the firewall.
   Everything the app needs locally is loopback (`127.0.0.1`), which is unaffected.
3. **Audit** — `droplens egress --limit 500` (or Activity → *Network log…*) lists every
   request ever attempted, with target and payload size. Ship that output with your
   security evidence pack; if it is empty, nothing left the machine.

For a local-only AI stack, deploy Ollama on the endpoint (or a LAN inference host) and point
`base_url` at it. `net.is_local_url` treats RFC 1918 addresses as local, so a LAN model keeps
`offline_only` effective.

---

## 6. Data location, backup and restore

| Artifact | Path | Back up? |
|---|---|---|
| Catalogue (roots, text, FTS, chunks, vectors, AI metadata, queue) | `%LOCALAPPDATA%\DropLens\catalog.db` (+ `-wal`, `-shm`) | Yes — this is the whole state |
| Settings (providers, keys sealed with DPAPI) | `%LOCALAPPDATA%\DropLens\settings.json` | Optional; DPAPI keys do not survive profile migration |
| Log | `%LOCALAPPDATA%\DropLens\droplens.log` | For support only |
| Plugins | `%LOCALAPPDATA%\DropLens\plugins\` | If you deploy them |

Backup is a file copy while the app is closed (or use SQLite's online backup API from a
script). Restore = copy `catalog.db*` back. Re-indexing is always possible, so a lost index
is a time cost, never a data loss.

```powershell
# scheduled backup of the whole state directory
robocopy "$env:LOCALAPPDATA\DropLens" "\\fileserver\backups\%USERNAME%\DropLens" catalog.db* settings.json /R:2 /W:2
```

---

## 7. Fleet-wide indexing without the GUI

The same engine runs headless — useful for pre-warming a shared volume, or for a scheduled
job on an indexing server:

```bash
droplens scan "D:/Corp/Contracts" --quiet
droplens jobs --queue --run          # summarize/tag/embed if a model is configured
droplens stats --json                # files, chunks, vectors, queue depth, schema version
droplens export --out inventory.csv  # audit inventory
```

Exit codes are conventional (`0` ok, `2` bad input, `3` AI not configured), so a scheduler can
rely on them. Nothing in the CLI touches the GUI or requires a display.

---

## 8. Hardening checklist

- [ ] Sign `DropLens.exe` and `Setup-DropLens*.exe` (`signtool sign /fd SHA256 /tr <ts> /td SHA256 /a …`).
- [ ] Publish SHA-256 hashes alongside the installer.
- [ ] Pre-seed `offline_only` / `plugins_enabled` per policy (or block egress at the firewall).
- [ ] Do **not** deploy unreviewed plugins; set `plugins_enabled: false` if unsure.
- [ ] Put the data directory on an encrypted volume if the library is confidential.
- [ ] Keep the catalogue out of roaming profiles (it can be large); back it up instead.
- [ ] Review `pip-audit` output from CI before each release (`docs/RELEASE_CHECKLIST.md`).
- [ ] Confirm the uninstaller keeps user data (it does), so a reinstall does not trigger a
      full re-index.
- [ ] Test one silent install and one silent uninstall on a clean VM of each OS build you support.

---

## 9. Support and diagnostics

Collect, in this order:

1. `droplens stats --json` — version, schema version, counts, queue state.
2. `%LOCALAPPDATA%\DropLens\droplens.log` (rotating, 5 MB × 2) — no document contents, no keys.
3. `droplens egress` — network activity for a "did it phone home?" question.
4. The affected file's **status** (`indexed` / `no_content` / `ocr_unavailable` / `failed`) —
   visible in the preview pane and in `droplens stats --json`.

Common resolutions:

| Symptom | Cause | Fix |
|---|---|---|
| `ocr_unavailable` on images | Tesseract not installed | Install Tesseract and point Settings → OCR at it |
| Semantic search disabled | Active provider has no embedding model | Set one (e.g. `nomic-embed-text`) |
| `7Z/RAR` produce no content | Extractor wheel missing in a custom build | Rebuild with `py7zr`/`rarfile` (already pinned) |
| Scans seem slow on a network share | Serial walk over SMB | Use `threads` ≥ 4, or index the local volume instead |
| Massive index growth | Very large text files | Lower `max_content_chars` |

---

## 10. What is intentionally not provided

Stated so no one plans around something that does not exist:

- No central management console, no telemetry, no MDM SDK.
- No auto-update channel; upgrades are administrator-driven.
- No license-key enforcement (the code is MIT).
- No multi-user shared index and no server-side search endpoint. The CLI is local.
