"""Background folder watcher.

Polls every monitored root on an interval; when the cheap signature (entry
count + newest mtime) changes, it asks the UI to trigger an incremental scan.
Safely ignores itself while a scan is already running.
"""
from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from ..config import Config
from .db import Catalog
from .discover import quick_signature

ChangedCb = Callable[[str], None]
"""callback(root) fired when a watched root changed"""


class FolderWatcher(threading.Thread):
    def __init__(self, catalog: Catalog, config: Config, on_changed: ChangedCb):
        super().__init__(name="droplens-watch", daemon=True)
        self.catalog = catalog
        self.config = config
        self.on_changed = on_changed
        self._stop = threading.Event()
        self._sigs: dict[str, tuple[int, float]] = {}

    def run(self) -> None:
        while not self._stop.wait(self.config.watch_interval):
            try:
                self._pass()
            except Exception:
                time.sleep(2)

    def stop(self) -> None:
        self._stop.set()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

    def _pass(self) -> None:
        roots = [r["path"] for r in self.catalog.list_roots()]
        if not roots:
            return
        cfg = self.config
        for root in roots:
            if self._stop.is_set():
                return
            try:
                sig = quick_signature(root)
            except Exception:
                continue
            prev = self._sigs.get(root)
            if prev is not None and prev != sig:
                self._sigs[root] = sig
                try:
                    self.on_changed(root)
                except Exception:
                    pass
            elif prev is None:
                self._sigs[root] = sig

    def forget(self, root: str) -> None:
        self._sigs.pop(root, None)