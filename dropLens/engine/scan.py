"""Scan orchestration: incremental indexing with progress and cancellation."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..config import Config
from .categorize import categorize
from .db import Catalog
from .discover import FileInfo, hash_file, walk_root
from .extract import extract_content, folder_listing

ProgressCb = Callable[[dict], None]
"""progress dicts: {kind, ...}"""


@dataclass
class ScanResult:
    root: str
    indexed: int = 0
    skipped: int = 0
    deleted: int = 0
    folders: int = 0
    errors: int = 0
    bytes_indexed: int = 0
    cancelled: bool = False
    duration: float = 0.0


class ScanManager:
    """Runs a scan on a background thread; one scan at a time."""

    def __init__(self, catalog: Catalog, config: Config, on_progress: Optional[ProgressCb] = None):
        self.catalog = catalog
        self.config = config
        self.on_progress = on_progress or (lambda _d: None)
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._busy = threading.Event()

    # -- public controls ---------------------------------------------------
    def is_busy(self) -> bool:
        return self._busy.is_set()

    def cancel(self) -> None:
        self._cancel.set()

    def scan_roots(self, roots: list[str], incremental: bool = True) -> None:
        """Start an asynchronous scan. Safe to call from the GUI thread."""
        if self._busy.is_set():
            self.on_progress({"kind": "log", "level": "warning", "msg": "A scan is already running."})
            return
        self._busy.set()
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(list(roots), incremental),
            name="droplens-scan",
            daemon=True,
        )
        self._thread.start()

    def _run(self, roots: list[str], incremental: bool) -> None:
        self.on_progress({"kind": "state", "phase": "scanning", "total": len(roots), "done": 0})
        try:
            for i, root in enumerate(roots, 1):
                if self._cancel.is_set():
                    break
                self.on_progress({"kind": "log", "level": "info", "msg": f"Scanning: {root}"})
                res = self._scan_one(root, incremental)
                self.on_progress({"kind": "done", "root": root, "result": res})
                self.on_progress({"kind": "state", "phase": "scanning", "total": len(roots), "done": i})
                if res.cancelled:
                    break
        finally:
            self._busy.clear()
            self.on_progress({"kind": "state", "phase": "idle"})

    def _scan_one(self, root: str, incremental: bool) -> ScanResult:
        res = ScanResult(root=root)
        started = time.time()
        root = root.rstrip("\\/") or root

        # catalogue deepest common prefix so scope is predictable:
        # an existing root already in db keeps its stored root string
        stored = self.catalog.path_meta(root)
        root_col = stored["root"] if stored and stored["kind"] == "file" else root
        if stored and stored["kind"] == "folder":
            root_col = root

        existing = self.catalog.existing_in_root(root_col) if incremental else {}
        seen: set[str] = set()
        pending_files: list[dict] = []
        pending_docs: list[tuple[int, str, str, str]] = []
        size_first: dict[int, str] = {}   # file size -> first path seen this scan (dup prefilter)
        path_rec: dict[str, dict] = {}     # path -> buffered record (lets us backfill hashes)
        batch_limit = 800
        self.catalog.begin()

        def ensure_hash(rec: dict) -> None:
            if rec.get("hash"):
                return
            digest = hash_file(rec["path"])
            if digest:
                rec["hash"] = digest
                rec["dup_id"] = digest

        def flush():
            if pending_files:
                for rec in pending_files:
                    fid = self.catalog.upsert_file(rec)
                    if fid:
                        pending_docs.append((fid, rec["name"], rec["path"], rec["_content"] or ""))
                # FTS5 has no UPSERT: delete stale copies then re-insert
                self.catalog.executemany(
                    "DELETE FROM docs WHERE doc_id=?",
                    [(p[0],) for p in pending_docs],
                )
                self.catalog.executemany(
                    "INSERT INTO docs(doc_id, name, path, content) VALUES(?,?,?,?)",
                    pending_docs,
                )
            pending_files.clear()
            pending_docs.clear()
            self.catalog.commit()
            self.catalog.begin()

        def on_error(path: str, msg: str) -> None:
            res.errors += 1
            self.on_progress({"kind": "log", "level": "error", "msg": f"[{path}] {msg}"})

        cfg = self.config
        is_file_scan = os.path.isfile(root)
        try:
            for fi in walk_root(root, ignore_names=cfg.ignore_names, on_error=on_error):
                if self._cancel.is_set():
                    res.cancelled = True
                    break
                seen.add(fi.path)
                meta = existing.get(fi.path)
                if meta is not None and meta[1] == fi.size and abs(meta[2] - fi.mtime) < 1e-6 and meta[0]:
                    res.skipped += 1
                    continue  # unchanged since last scan -> keep existing index

                rec = {
                    "path": fi.path,
                    "name": fi.name,
                    "ext": fi.ext,
                    "size": fi.size,
                    "mtime": fi.mtime,
                    "ctime": fi.ctime,
                    "root": root_col,
                    "hash": None,
                    "category": categorize(fi.ext) if fi.kind == "file" else "folder",
                    "kind": fi.kind,
                    "status": "indexed",
                    "dup_id": None,
                    "scanned_at": time.time(),
                    "_content": None,
                }

                if fi.kind == "folder":
                    rec["_content"] = folder_listing(fi.path)
                    res.folders += 1
                    pending_files.append(rec)
                    if len(pending_files) >= batch_limit:
                        flush()
                    continue

                # duplicate detection (cheap: only hashes files whose byte-size collides)
                if cfg.get("hash_duplicates", True) and fi.size > 0:
                    if fi.size in size_first:
                        ensure_hash(rec)
                        other = size_first[fi.size]
                        if other != fi.path and other in path_rec:
                            ensure_hash(path_rec[other])
                    elif self.catalog.has_same_size(fi.size):
                        ensure_hash(rec)
                    else:
                        size_first[fi.size] = fi.path
                elif fi.size == 0:
                    rec["hash"] = "empty"

                status_override = None
                content, dstatus = extract_content(
                    fi.path,
                    ocr_enabled=bool(cfg.get("ocr_enabled", True)),
                    tesseract_path=str(cfg.get("tesseract_path", "") or ""),
                )
                rec["_content"] = content
                rec["status"] = dstatus if dstatus != "indexed" else "indexed"

                if content:
                    res.bytes_indexed += len(content)
                pending_files.append(rec)
                path_rec[fi.path] = rec
                if len(pending_files) >= batch_limit:
                    flush()

                self.on_progress({
                    "kind": "file",
                    "path": fi.path,
                    "kind2": fi.kind,
                    "count": len(seen),
                })

            res.indexed = len(seen) - res.skipped - res.folders

            # remove entries that no longer exist under this root
            if not res.cancelled and not is_file_scan:
                gone = [pid for p, (pid, *_rest) in self.catalog.existing_in_root(root_col).items() if p not in seen]
                res.deleted = self.catalog.mark_deleted(gone)
            flush()

        finally:
            try:
                self.catalog.commit()
            except Exception:
                self.catalog.rollback()

        if not is_file_scan:
            self.catalog.touch_root_scan(root_col, "cancelled" if res.cancelled else "indexed")
        res.duration = time.time() - started
        self.on_progress({
            "kind": "root_stats",
            "root": root,
            "indexed": res.indexed,
            "skipped": res.skipped,
            "deleted": res.deleted,
            "errors": res.errors,
        })
        if res.errors:
            self.on_progress({
                "kind": "log",
                "level": "warning",
                "msg": f"Scan {root}: {res.indexed} indexed, {res.skipped} unchanged, "
                       f"{res.deleted} removed, {res.errors} errors.",
            })
        return res