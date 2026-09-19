"""DropLens application configuration.

Settings are persisted as JSON inside the data directory (see resources.py).
Every field carries a sensible default so the application works out of the box.
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List

DEFAULTS: Dict[str, Any] = {
    "data_dir": "",                    # resolved at runtime -> %LOCALAPPDATA%/DropLens
    "threads": 4,                      # worker threads for extraction
    "hash_duplicates": True,           # compute content hashes to find duplicates
    "ocr_enabled": True,               # optical character recognition for images
    "tesseract_path": "",              # optional explicit path to tesseract.exe
    "watch_enabled": False,            # auto-rescan monitored folders on change
    "watch_interval": 15,              # seconds between watcher passes
    "default_lang": "en",              # default translation target (ISO-639-1)
    "max_content_chars": 5_000_000,    # cap stored text per file (keeps index fast)
    "preview_chars": 20_000,           # characters shown in the search preview pane
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
}

_DEFAULTS_COPY = dict(DEFAULTS)


class Config:
    """Typed wrapper around the settings dictionary."""

    def __init__(self, data: Dict[str, Any] | None = None):
        self._d: Dict[str, Any] = dict(_DEFAULTS_COPY)
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
        return self._d.get("data_dir") or _default_data_dir()

    @property
    def threads(self) -> int:
        return max(1, int(self._d.get("threads", 4)))

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
        self._path = path or os.path.join(_data_dir_override_or_default(), "settings.json")
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
        except OSError:
            pass


def _data_dir_override_or_default() -> str:
    return os.environ.get("DROPLENS_DIR") or _default_data_dir()


def _default_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "DropLens")