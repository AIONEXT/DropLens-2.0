"""The object every plugin receives in ``register(api)``."""
from __future__ import annotations

import os
from typing import Any, Callable, Iterable, Optional

from ..tools import Tool
from .registry import Extension, Registry


class ExtensionAPI:
    """Small, explicit surface: extractors, tools and hooks.

    Deliberately narrow — a plugin gets no catalogue handle and no network
    helper, so an extension cannot quietly mutate the library or exfiltrate
    anything the rest of the app would not. It works through the same tool and
    extractor contracts as the built-ins.
    """

    def __init__(self, registry: Registry, extension: Extension):
        self._registry = registry
        self.extension = extension

    @property
    def name(self) -> str:
        return self.extension.name

    @property
    def data_dir(self) -> str:
        return os.path.dirname(self.extension.path)

    # -- extractors --------------------------------------------------------
    def register_extractor(self, extensions: str | Iterable[str],
                           func: Callable[[str], Optional[str]], *,
                           replace: bool = False) -> bool:
        """Claim one or more file extensions for a ``path -> text`` function."""
        return self._registry.register_extractor(
            extensions, func, source=self.extension.name, replace=replace)

    # -- tools -------------------------------------------------------------
    def register_tool(self, tool: Tool) -> bool:
        return self._registry.register_tool(tool)

    def tool(self, name: str, description: str = "",
             parameters: Optional[dict[str, Any]] = None) -> Callable[[Callable], Callable]:
        """Decorator: expose a function that takes an argument dict."""
        def decorator(func: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
            self._registry.register_tool(Tool(
                name=name,
                description=description or (func.__doc__ or "").strip().split("\n")[0],
                parameters=parameters or {"type": "object", "properties": {}},
                handler=func,
                source=self.extension.name,
            ))
            return func
        return decorator

    # -- hooks -------------------------------------------------------------
    def on(self, event: str) -> Callable[[Callable], Callable]:
        """Decorator: run after an engine event (see ``HOOK_EVENTS``)."""
        def decorator(func: Callable[[dict[str, Any]], Any]) -> Callable[[dict[str, Any]], Any]:
            self._registry.register_hook(event, func)
            return func
        return decorator
