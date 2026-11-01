"""Extension registry: extractors, tools and hooks, loaded from disk.

A plugin is a plain Python module in ``<data_dir>/plugins`` — either a single
``*.py`` file or a package directory with an ``__init__.py``. It exposes a
``register(api)`` function and receives an :class:`ExtensionAPI`:

    NAME = "CSV tables"
    VERSION = "1.0"
    DESCRIPTION = "Reads CSV/TSV headers and rows as searchable text"

    def register(api):
        api.register_extractor(("csv", "tsv"), extract)   # better than the built-in
        @api.tool("lookup_row", description="Find a row by key")
        def lookup(args):
            return {"match": ...}
        @api.on("document_indexed")
        def after(doc):
            ...

Loading is defensive. A plugin that raises is recorded with its error and
skipped; it never takes the scan, the CLI or the GUI down with it. Nothing is
fetched from the network and each file is imported exactly once.
"""
from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional

from ..tools import Tool

log = logging.getLogger(__name__)

HOOK_EVENTS = ("scan_start", "document_indexed", "scan_complete")

_PLUGIN_PREFIX = "droplens_plugin_"


@dataclass
class Extension:
    """One discovered plugin and what happened to it."""

    name: str
    version: str = ""
    description: str = ""
    path: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "version": self.version,
            "description": self.description, "path": self.path,
            "ok": self.ok, "error": self.error,
        }


class Registry:
    """Holds every registered extractor, tool and hook."""

    def __init__(self) -> None:
        self.extensions: list[Extension] = []
        self._extractors: dict[str, Callable[[str], Optional[str]]] = {}
        self._extractor_sources: dict[str, str] = {}
        self._tools: dict[str, Tool] = {}
        self._hooks: dict[str, list[Callable[[dict[str, Any]], Any]]] = {}
        self._loaded_dirs: set[str] = set()
        self._lock = threading.RLock()

    # -- registration ------------------------------------------------------
    def register_extractor(self, extensions: str | Iterable[str],
                           func: Callable[[str], Optional[str]], *,
                           source: str = "plugin", replace: bool = False) -> bool:
        """Register a ``path -> text`` extractor for one or more file extensions."""
        if not callable(func):
            return False
        names = [extensions] if isinstance(extensions, str) else list(extensions)
        added = False
        with self._lock:
            for name in names:
                key = str(name or "").strip().lstrip(".").lower()
                if not key:
                    continue
                if key in self._extractors and not replace:
                    continue
                self._extractors[key] = func
                self._extractor_sources[key] = source
                added = True
        return added

    def register_tool(self, tool: Tool) -> bool:
        if not isinstance(tool, Tool) or not tool.name:
            return False
        with self._lock:
            if tool.name in self._tools:
                return False
            self._tools[tool.name] = tool
        return True

    def register_hook(self, event: str, func: Callable[[dict[str, Any]], Any]) -> bool:
        if event not in HOOK_EVENTS:
            raise ValueError(f"unknown hook {event!r}; expected one of {HOOK_EVENTS}")
        if not callable(func):
            return False
        with self._lock:
            self._hooks.setdefault(event, []).append(func)
        return True

    # -- lookup ------------------------------------------------------------
    def extractor_for(self, ext: str) -> Optional[Callable[[str], Optional[str]]]:
        return self._extractors.get(str(ext or "").strip().lstrip(".").lower())

    def extractor_extensions(self) -> list[str]:
        return sorted(self._extractors)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def tool_names(self) -> list[str]:
        return sorted(self._tools)

    def fire(self, event: str, payload: Optional[dict[str, Any]] = None) -> list[Any]:
        """Run every hook for *event*. A failing hook is logged, never raised."""
        data = dict(payload or {})
        results: list[Any] = []
        for func in list(self._hooks.get(event, ())):
            try:
                results.append(func(dict(data)))
            except Exception as exc:  # noqa: BLE001 - a plugin must not break a scan
                log.warning("extension hook %s failed: %s", event, exc)
        return results

    def summary(self) -> dict[str, Any]:
        return {
            "plugins": [e.to_dict() for e in self.extensions],
            "extractors": [{"extension": ext, "source": self._extractor_sources.get(ext, "")}
                           for ext in self.extractor_extensions()],
            "tools": [{"name": t.name, "source": t.source, "description": t.description}
                      for t in self.tools()],
            "hooks": {k: len(v) for k, v in self._hooks.items() if v},
        }

    # -- loading -----------------------------------------------------------
    def load(self, plugin_dir: str, *, force: bool = False) -> list[Extension]:
        """Import every plugin in *plugin_dir*. Idempotent per directory."""
        if not plugin_dir:
            return []
        plugin_dir = os.path.abspath(plugin_dir)
        with self._lock:
            if plugin_dir in self._loaded_dirs and not force:
                return list(self.extensions)
            self._loaded_dirs.add(plugin_dir)
        if not os.path.isdir(plugin_dir):
            return []
        for entry in sorted(os.listdir(plugin_dir)):
            if entry.startswith((".", "_")):
                continue
            path = os.path.join(plugin_dir, entry)
            if os.path.isdir(path):
                init = os.path.join(path, "__init__.py")
                if os.path.isfile(init):
                    self.load_file(init)
            elif entry.endswith(".py"):
                self.load_file(path)
        return list(self.extensions)

    def load_file(self, path: str) -> Extension:
        """Import one plugin file and call its ``register(api)``."""
        path = os.path.abspath(path)
        stem = os.path.splitext(os.path.basename(os.path.dirname(path)) if
                                os.path.basename(path) == "__init__.py"
                                else os.path.basename(path))[0]
        ext = Extension(name=stem, path=path)
        module_name = _PLUGIN_PREFIX + re.sub(r"\W", "_", stem) + "_" + \
            hashlib.sha1(path.encode("utf-8")).hexdigest()[:8]
        try:
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot import {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            ext.name = str(getattr(module, "NAME", stem))
            ext.version = str(getattr(module, "VERSION", ""))
            ext.description = str(getattr(module, "DESCRIPTION", ""))
            register = getattr(module, "register", None)
            if not callable(register):
                raise AttributeError("plugin does not define register(api)")
            from .api import ExtensionAPI  # local import breaks the cycle
            register(ExtensionAPI(self, ext))
        except Exception as exc:  # noqa: BLE001 - one bad plugin is isolated
            ext.error = f"{type(exc).__name__}: {exc}"
            log.warning("plugin %s failed to load: %s", path, ext.error)
        self.extensions.append(ext)
        return ext


def _sha1(text: str) -> str:  # pragma: no cover - kept for callers/tests
    return hashlib.sha1(text.encode("utf-8")).hexdigest()
