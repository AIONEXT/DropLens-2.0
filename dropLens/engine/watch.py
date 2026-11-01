"""Background folder watcher.

Polls every monitored root on an interval. When a root's signature changes the
callback fires at most once per ``watch_debounce`` window, because a single
editor save produces a burst of writes; the burst collapses into one rescan.
Scans are incremental, so a debounced root costs a stat walk rather than a full
re-extraction.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..config import Config
from .db import Catalog
from .discover import quick_signature

log = logging.getLogger(__name__)

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
        self._last_fired: dict[str, float] = {}

    def run(self) -> None:
        while not self._stop.wait(self.config.watch_interval):
            try:
                self._pass()
            except Exception:  # noqa: BLE001 - the watcher must never die
                log.warning("watcher pass failed", exc_info=True)
                time.sleep(2)

    def stop(self) -> None:
        self._stop.set()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

    @property
    def debounce(self) -> float:
        try:
            return max(0.0, float(self.config.get("watch_debounce", 3) or 0))
        except (TypeError, ValueError):
            return 3.0

    def _pass(self) -> None:
        roots = [r["path"] for r in self.catalog.list_roots()]
        if not roots:
            return
        now = time.time()
        for root in roots:
            if self._stop.is_set():
                return
            try:
                sig = quick_signature(root)
            except Exception:  # noqa: BLE001 - unreadable root: try again next pass
                log.debug("signature failed for %s", root, exc_info=True)
                continue
            prev = self._sigs.get(root)
            if prev is None:
                self._sigs[root] = sig
                continue
            if prev == sig:
                continue
            self._sigs[root] = sig
            if now - self._last_fired.get(root, 0.0) < self.debounce:
                continue  # part of a burst already announced
            self._last_fired[root] = now
            try:
                self.on_changed(root)
            except Exception:  # noqa: BLE001 - callback is best effort
                log.debug("watch callback failed for %s", root, exc_info=True)

    def forget(self, root: str) -> None:
        self._sigs.pop(root, None)
        self._last_fired.pop(root, None)
