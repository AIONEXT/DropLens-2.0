"""Durable AI enrichment queue.

Replaces the old fire-and-forget loop (``for row in files_for_ai(): ...``) with
a real work queue persisted in SQLite, which buys properties the loop could not
have:

  * **resumability** — close the laptop mid-run and the queue waits;
  * **invalidation** — work is queued because a document's artifacts are
    missing or stale, not because the button was pressed;
  * **no 3,000-file ceiling** — ordering is by priority (favourites, then small
    files), not an arbitrary ``LIMIT``;
  * **retry with backoff** — a 429 or a transient 5xx schedules the job for
    later instead of abandoning the remaining work;
  * **real cancellation** — stopping leaves the rest queued;
  * **crash-safe progress** — one row per (document, kind).

The queue is only a scheduler: the actual work lives in :mod:`dropLens.ai.svc`.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from ..engine.db import Catalog
from .client import AIConnectionError, AINotConfigured

log = logging.getLogger(__name__)

BATCH = 16


def enqueue(catalog: Catalog, limit: int | None = None, retry_failed: bool = False) -> int:
    """Queue every missing summary / tag set / embedding. Returns queued count."""
    return catalog.enqueue_ai_jobs(limit=limit, retry_failed=retry_failed)


def run_queue(
    ai,
    progress: Optional[Callable[[int, int], None]] = None,
    stop: Optional[Callable[[], bool]] = None,
    limit: int | None = None,
    retry_limit: int = 3,
) -> str:
    """Drain the queue; returns a human summary for the Activity log."""
    catalog: Catalog = ai.catalog
    stop = stop or (lambda: False)
    queued_total = catalog.job_counts().get("queued", 0)
    if queued_total == 0:
        return "AI enrichment: nothing to do — every indexed file already has fresh AI data."

    done = {"summary": 0, "tags": 0, "embed": 0}
    failed = 0
    processed = 0
    seen: set[int] = set()
    cancelled = False

    while True:
        if stop():
            cancelled = True
            break
        rows = [r for r in catalog.runnable_jobs(limit=BATCH) if int(r["id"]) not in seen]
        if not rows:
            break
        for row in rows:
            if stop():
                cancelled = True
                break
            job_id = int(row["id"])
            doc_id = int(row["doc_id"])
            kind = str(row["kind"])
            attempts = int(row["attempts"] or 0)
            seen.add(job_id)
            try:
                if kind == "summary":
                    ai.summarize_doc(doc_id)
                elif kind == "tags":
                    ai.auto_tag(doc_id)
                elif kind == "embed":
                    ai.embed_doc(doc_id)
                else:  # unknown kind from a newer version
                    catalog.finish_job(job_id, doc_id, kind, "skipped")
                    continue
            except AINotConfigured:
                raise
            except AIConnectionError as exc:
                attempts += 1
                if attempts >= max(1, retry_limit):
                    catalog.finish_job(job_id, doc_id, kind, "failed", error=str(exc),
                                       attempts=attempts)
                    failed += 1
                else:
                    backoff = min(300.0, float(2 ** attempts))
                    catalog.finish_job(job_id, doc_id, kind, "queued", error=str(exc),
                                       attempts=attempts, next_at=time.time() + backoff)
                log.warning("AI job %s (%s doc %s) failed: %s", job_id, kind, doc_id, exc)
            else:
                digest = ai.content_hash(doc_id)
                catalog.finish_job(job_id, doc_id, kind, "done", attempts=attempts,
                                   content_hash=digest)
                done[kind] = done.get(kind, 0) + 1
            processed += 1
            catalog.commit()
            if progress:
                progress(processed, max(processed, queued_total))
            if limit is not None and processed >= limit:
                cancelled = cancelled or False
                break
        if limit is not None and processed >= limit:
            break

    counts = catalog.job_counts()
    parts = (f"{done['summary']} summaries, {done['tags']} tagged, "
             f"{done['embed']} embedded")
    if failed:
        parts += f", {failed} failed (retried on the next run)"
    remaining = counts.get("queued", 0)
    if remaining:
        parts += f", {remaining} still queued"
    prefix = "Stopped by user — AI enrichment" if cancelled else "AI enrichment complete:"
    return f"{prefix} {parts}."
