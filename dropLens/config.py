"""DropLens application configuration.

Settings are persisted as JSON inside the data directory (see resources.py).
Every field carries a sensible default so the application works out of the box.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, List

log = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "data_dir": "",                    # resolved at runtime -> %LOCALAPPDATA%/DropLens
    "threads": 4,                      # worker threads for extraction
    "hash_duplicates": True,           # compute content hashes to find duplicates
    "ocr_enabled": True,               # optical character recognition for images
    "tesseract_path": "",              # optional explicit path to tesseract.exe
    "watch_enabled": False,            # auto-rescan monitored folders on change
    "watch_interval": 15,              # seconds between watcher passes
    "watch_debounce": 3,               # seconds a change burst is coalesced into
    "default_lang": "en",              # default translation target (ISO-639-1)
    "max_content_chars": 5_000_000,    # cap stored text per file (keeps index fast)
    "preview_chars": 20_000,           # characters shown in the search preview pane
    # retrieval (chunked, hybrid)
    "chunk_chars": 3200,               # target characters per retrieval chunk
    "chunk_overlap": 0.15,             # fraction of a chunk repeated in the next
    "retrieve_candidates": 60,         # candidates per retrieval channel (FTS + vectors)
    "retrieve_top": 8,                 # chunks handed to the model
    "embed_batch": 32,                 # chunks per embedding request
    # AI enrichment queue
    "ai_retry_limit": 3,               # attempts before a job is marked failed
    # privacy
    "offline_only": False,             # block every non-loopback destination
    "ignore_names": [
        ".git", "__pycache__", "node_modules", ".venv", "venv",
        "$RECYCLE.BIN", "System Volume Information", "Thumbs.db",
        "desktop.ini", ".DS_Store",
    ],
    # AI provider registry + active provider (dict matches AIConfig.to_dict())
    "ai": {},
    # UX preferences
    "tray_enabled": True,            # system tray icon
    "close_to_tray": False,          # closing the window keeps scanning silently
    "notify_scan_done": True,        # tray notification when a scan finishes
    "onboarded": False,              # first-run wizard shown once
    "semantic_default": False,       # semantic search enabled by default
    "ai_auto_enrich": False,         # run AI enrichment right after a scan
    "plugins_enabled": True,         # load user plugins from <data_dir>/plugins
}

_DEFAULTS_COPY = json.loads(json.dumps(DEFAULTS))  # deep copy: no shared lists


class Config:
    """Typed wrapper around the settings dictionary."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self._d: Dict[str, Any] = json.loads(json.dumps(_DEFAULTS_COPY))
        if data:
            for k, v in data.items():
                if k in self._d:
                    self._d[k] = v

    # -- attribute access -------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self._d.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._d[key] = value

    def as_dict(self) -> Dict[str, Any]:
        return dict(self._d)

    # -- typed helpers -----------------------------------------------------
    @property
    def data_dir(self) -> str:
        custom = self._d.get("data_dir")
        if custom:
            return str(custom)
        # one source of truth: resources honours DROPLENS_DIR and LOCALAPPDATA
        from . import resources
        return resources.data_dir()

    @property
    def threads(self) -> int:
        return max(1, min(16, int(self._d.get("threads", 4))))

    @property
    def chunk_chars(self) -> int:
        return max(400, int(self._d.get("chunk_chars", 3200)))

    @property
    def chunk_overlap(self) -> float:
        return max(0.0, min(0.5, float(self._d.get("chunk_overlap", 0.15))))

    @property
    def embed_batch(self) -> int:
        return max(1, min(256, int(self._d.get("embed_batch", 32))))

    @property
    def retrieve_candidates(self) -> int:
        return max(10, min(500, int(self._d.get("retrieve_candidates", 60))))

    @property
    def retrieve_top(self) -> int:
        return max(1, min(20, int(self._d.get("retrieve_top", 8))))

    @property
    def offline_only(self) -> bool:
        return bool(self._d.get("offline_only", False))

    @property
    def watch_interval(self) -> float:
        return max(2.0, float(self._d.get("watch_interval", 15.0)))

    @property
    def default_lang(self) -> str:
        return str(self._d.get("default_lang", "en"))

    @property
    def ignore_names(self) -> List[str]:
        names = self._d.get("ignore_names", [])
        return [n.strip() for n in names if n and n.strip()]

    # -- AI config ----------------------------------------------------------
    def ai_config(self):
        from dropLens.ai.providers import AIConfig  # local import avoids cycles
        return AIConfig(self._d.get("ai"))

    def set_ai_config(self, ai_cfg) -> None:
        self._d["ai"] = ai_cfg.to_dict()

    def patch(self, **kw: Any) -> None:
        for k, v in kw.items():
            if k in self._d:
                self._d[k] = v


class ConfigStore:
    """Loads and saves :class:`Config` to disk (thread safe)."""

    def __init__(self, path: str | None = None):
        from . import resources
        # one source of truth with Config.data_dir / resources.db_path(): the
        # same folder holds catalog.db, settings.json, the log and plugins
        self._path = path or resources.settings_path()
        self._lock = threading.Lock()
        self._cfg = Config(self._read())

    def _read(self) -> Dict[str, Any]:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, dict):
                return raw
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        return {}

    def get(self) -> Config:
        with self._lock:
            return Config(self._cfg.as_dict())

    def save(self, cfg: Config) -> None:
        with self._lock:
            self._cfg = Config(cfg.as_dict())
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as fh:
                json.dump(self._cfg.as_dict(), fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            # a failed settings write used to vanish; the user's AI config then
            # silently reverted on restart
            log.warning("could not save settings to %s: %s", self._path, exc)