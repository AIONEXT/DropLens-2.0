"""Scan orchestration: incremental indexing with parallel extraction.

The walker is sequential (it has to be — duplicate detection depends on the
order files are seen), but content extraction is I/O bound and runs on a small
thread pool sized by ``settings.threads``. Results are consumed in walk order so
progress reporting and duplicate semantics stay deterministic.

Derived AI data is invalidated here: when a file's extracted text changes, its
summary, tags, vector and chunks are dropped (see ``Catalog.save_doc``) instead
of silently describing the previous version forever.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Optional

from .. import resources
from ..config import Config
from .categorize import categorize
from .db import Catalog, content_hash
from .discover import FileInfo, hash_file, walk_root
from .extract import extract_content, folder_listing, set_max_content

log = logging.getLogger(__name__)

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
                try:
                    res = self._scan_one(root, incremental)
                except Exception as exc:  # noqa: BLE001 - one bad root must not kill the run
                    log.exception("scan failed for %s", root)
                    self.on_progress({"kind": "log", "level": "error", "msg": f"{root}: {exc}"})
                    continue
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

        # an existing root keeps its stored root string so scope stays predictable
        stored = self.catalog.path_meta(root)
        root_col = stored["root"] if stored and stored["kind"] == "file" else root
        if stored and stored["kind"] == "folder":
            root_col = root

        existing = self.catalog.existing_in_root(root_col) if incremental else {}
        seen: set[str] = set()
        pending: list[dict] = []
        size_first: dict[int, str] = {}   # file size -> first path seen this scan (dup prefilter)
        path_rec: dict[str, dict] = {}    # path -> buffered record (lets us backfill hashes)
        flushed: set[str] = set()
        batch_limit = 400
        # Progress is throttled: a per-file message on a 200k-file scan pushed
        # 200k dicts through the GUI's drain loop, so the counter lagged
        # arbitrarily and the queue grew without bound (review §3.3.4).
        progress_every = 0.15
        last_progress = [0.0]

        def emit_progress(force: bool = False) -> None:
            now = time.time()
            if not force and now - last_progress[0] < progress_every:
                return
            last_progress[0] = now
            self.on_progress({"kind": "file", "count": len(seen)})

        from ..extensions import default as extensions, load_from_config

        cfg = self.config
        load_from_config(cfg)          # plugins register extractors/tools/hooks
        events = extensions()
        events.fire("scan_start", {"root": root, "incremental": incremental})
        ocr = bool(cfg.get("ocr_enabled", True))
        tesseract = str(cfg.get("tesseract_path", "") or "") or (resources.find_tesseract() or "")
        set_max_content(int(cfg.get("max_content_chars", 5_000_000)))
        workers = cfg.threads
        pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="droplens-extract")
        is_file_scan = os.path.isfile(root)
        self.catalog.begin()

        def ensure_hash(rec: dict) -> None:
            if rec.get("hash"):
                return
            digest = hash_file(rec["path"])
            if not digest:
                return
            rec["hash"] = digest
            rec["dup_id"] = digest
            if rec["path"] in flushed:
                # the row was already written this batch - persist the late hash
                self.catalog.execute("UPDATE files SET hash=?, dup_id=? WHERE path=?",
                                     (digest, digest, rec["path"]))

        def flush() -> None:
            if pending:
                docs = []
                for rec in pending:
                    fid = self.catalog.upsert_file(rec)
                    if fid:
                        text = rec["_content"] or ""
                        docs.append((fid, rec["name"], rec["path"], text,
                                     content_hash(text, rec["name"], rec["size"]),
                                     rec.get("status") or "indexed"))
                for fid, name, path, text, digest, status in docs:
                    self.catalog.save_doc(fid, name, path, text, digest)
                    events.fire("document_indexed", {
                        "doc_id": fid, "name": name, "path": path,
                        "chars": len(text), "status": status,
                    })
                flushed.update(r["path"] for r in pending)
            pending.clear()
            self.catalog.commit()
            self.catalog.begin()

        def on_error(path: str, msg: str) -> None:
            res.errors += 1
            self.on_progress({"kind": "log", "level": "error", "msg": f"[{path}] {msg}"})

        def process(batch: list[FileInfo]) -> None:
            work: list[FileInfo] = []
            for fi in batch:
                meta = existing.get(fi.path)
                unchanged = (meta is not None and meta[1] == fi.size
                             and abs(meta[2] - fi.mtime) < 1e-6 and meta[0])
                if unchanged:
                    seen.add(fi.path)
                    res.skipped += 1
                    continue
                work.append(fi)
            if not work:
                return

            futures = {
                fi.path: pool.submit(extract_content, fi.path,
                                     ocr_enabled=ocr, tesseract_path=tesseract)
                for fi in work if fi.kind == "file"
            }
            for fi in work:
                if self._cancel.is_set():
                    res.cancelled = True
                    break
                seen.add(fi.path)
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
                else:
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

                    future = futures.get(fi.path)
                    if future is not None:
                        try:
                            content, dstatus = future.result()
                        except Exception as exc:  # noqa: BLE001 - defensive
                            log.warning("extraction worker failed for %s: %s", fi.path, exc)
                            content, dstatus = None, "failed"
                    else:  # pragma: no cover - defensive
                        content, dstatus = None, "failed"
                    rec["_content"] = content
                    rec["status"] = dstatus
                    if content:
                        res.bytes_indexed += len(content)
                    path_rec[fi.path] = rec

                pending.append(rec)
                if len(pending) >= batch_limit:
                    flush()
                emit_progress()

        try:
            batch: list[FileInfo] = []
            chunk_size = max(16, workers * 8)
            for fi in walk_root(root, ignore_names=cfg.ignore_names, on_error=on_error):
                if self._cancel.is_set():
                    res.cancelled = True
                    break
                batch.append(fi)
                if len(batch) >= chunk_size:
                    process(batch)
                    batch.clear()
                    if res.cancelled:
                        break
            if batch and not res.cancelled:
                process(batch)
                batch.clear()

            res.indexed = max(0, len(seen) - res.skipped - res.folders)

            # remove entries that no longer exist under this root
            if not res.cancelled and not is_file_scan:
                gone = [pid for p, (pid, *_rest) in self.catalog.existing_in_root(root_col).items()
                        if p not in seen]
                res.deleted = self.catalog.mark_deleted(gone)
            flush()
        finally:
            try:
                self.catalog.commit()
            except Exception:  # noqa: BLE001
                self.catalog.rollback()
            pool.shutdown(wait=False, cancel_futures=True)

        emit_progress(force=True)
        if not is_file_scan:
            self.catalog.touch_root_scan(root_col, "cancelled" if res.cancelled else "indexed")
        res.duration = time.time() - started
        events.fire("scan_complete", {
            "root": root, "indexed": res.indexed, "skipped": res.skipped,
            "deleted": res.deleted, "errors": res.errors,
            "cancelled": res.cancelled, "duration": round(res.duration, 3),
        })
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
