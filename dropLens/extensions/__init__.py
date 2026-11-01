"""Extension system: load plugins that add extractors, tools and hooks.

    from dropLens.extensions import default, load_from_config

    load_from_config(cfg)              # once, at startup (or before a scan)
    default().extractor_for("emlx")    # plugin-provided text extractor
    default().tools()                  # plugin-provided assistant tools
    default().fire("scan_complete", {"root": root, "indexed": n})

Plugins live in ``<data_dir>/plugins`` and are optional: with no plugins the
registry is empty and every code path behaves exactly as before.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional

from .api import ExtensionAPI
from .registry import HOOK_EVENTS, Extension, Registry

__all__ = [
    "Extension", "ExtensionAPI", "HOOK_EVENTS", "Registry",
    "default", "reset", "load", "load_from_config", "plugin_dir",
]

_default: Optional[Registry] = None
_lock = threading.Lock()


def default() -> Registry:
    """Process-wide registry."""
    global _default
    with _lock:
        if _default is None:
            _default = Registry()
        return _default


def reset() -> Registry:
    """Replace the registry (tests, and 'reload plugins' in the UI)."""
    global _default
    with _lock:
        _default = Registry()
        return _default


def plugin_dir(data_dir: str) -> str:
    return os.path.join(data_dir, "plugins") if data_dir else ""


def load(directory: str) -> Registry:
    """Load plugins from *directory* into the default registry."""
    registry = default()
    registry.load(directory)
    return registry


def load_from_config(cfg: Any) -> Registry:
    """Load plugins for a config object, honouring ``plugins_enabled``."""
    registry = default()
    try:
        enabled = bool(cfg.get("plugins_enabled", True))
        data_dir = str(getattr(cfg, "data_dir", "") or "")
    except Exception:  # noqa: BLE001 - duck-typed config in tests
        return registry
    if not enabled:
        return registry
    if not data_dir:  # no custom data folder: use the one the app really uses
        from .. import resources
        data_dir = resources.data_dir()
    if data_dir:
        registry.load(plugin_dir(data_dir))
    return registry
