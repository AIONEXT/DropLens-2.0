"""System tray integration (opt-in).

Uses ``pystray`` when available; everything degrades to a plain window close
otherwise. The tray lets DropLens keep scanning silently in the background
while the main window is hidden.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from . import icon as icon_mod

_import_error = None
try:
    import pystray  # type: ignore
    from PIL import Image
except Exception as exc:  # pragma: no cover
    pystray = None  # type: ignore[assignment]
    _import_error = exc


class Tray:
    def __init__(self,
                 on_open: Callable[[], None],
                 on_quick_scan: Callable[[], None],
                 on_exit: Callable[[], None]):
        self._on_open = on_open
        self._on_quick_scan = on_quick_scan
        self._on_exit = on_exit
        self._icon = None
        self._thread: Optional[threading.Thread] = None
        self._visible = False

    @property
    def available(self) -> bool:
        return pystray is not None

    @property
    def visible(self) -> bool:
        return self._visible

    def start(self) -> bool:
        if not self.available or self._visible:
            return False
        try:
            image = icon_mod.app_icon(64)
            menu = pystray.Menu(
                pystray.MenuItem("Open DropLens", lambda: self._on_open(), default=True),
                pystray.MenuItem("Quick scan (all folders)", lambda: self._on_quick_scan()),
                pystray.MenuItem("Exit", lambda: self._on_exit()),
            )
            self._icon = pystray.Icon("DropLens", _to_pil(image), "DropLens",
                                      menu=menu)
            self._visible = True
            self._thread = threading.Thread(target=self._icon.run, daemon=True)
            self._thread.start()
            return True
        except Exception:
            self._visible = False
            self._icon = None
            return False

    def notify(self, title: str, message: str) -> None:
        if self._icon is not None and self._visible:
            try:
                self._icon.notify(message, title)
            except Exception:
                pass

    def hide(self) -> None:
        """Remove the tray icon."""

    def stop(self) -> None:
        if self._icon is not None and self._visible:
            try:
                self._icon.stop()
            except Exception:
                pass
        self._icon = None
        self._visible = False


def _to_pil(img):
    from PIL import Image
    return img.convert("RGBA")


def tray_available() -> bool:
    return pystray is not None