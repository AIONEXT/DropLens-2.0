"""Outbound network policy and the local egress log.

DropLens promises that a local-first configuration never talks to the
internet. That promise is only credible if it is enforced in one place and
logged where the user can inspect it, so every outbound HTTP call funnels
through :func:`check` (policy) and :func:`log` (transparency).

Offline-only mode blocks every non-loopback destination — AI providers and the
Google Translate endpoint alike.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

_lock = threading.Lock()
_offline_only = False
_sink: Optional[Callable[..., None]] = None
_entries: list[dict[str, Any]] = []
_MAX_MEMORY = 500


class EgressBlocked(Exception):
    """Raised when offline-only mode refuses an outbound request."""


def is_local_url(url: str) -> bool:
    """True for loopback / private-network endpoints (never leave the machine)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:  # pragma: no cover - defensive
        return False
    if not host:
        return False
    if host in _LOOPBACK_HOSTS:
        return True
    if host.endswith(".local") or host.endswith(".localhost"):
        return True
    if host.startswith("10.") or host.startswith("192.168."):
        return True
    if host.startswith("172."):
        try:
            second = int(host.split(".")[1])
        except (IndexError, ValueError):
            return False
        return 16 <= second <= 31
    return False


def configure(offline_only: bool = False, sink: Optional[Callable[..., None]] = None) -> None:
    """Set the policy and the persistence sink (a ``Catalog``-like object)."""
    global _offline_only, _sink
    with _lock:
        _offline_only = bool(offline_only)
        if sink is not None:
            _sink = sink


def offline_only() -> bool:
    with _lock:
        return _offline_only


def check(url: str) -> None:
    """Raise :class:`EgressBlocked` when the request is not allowed."""
    if offline_only() and not is_local_url(url):
        log.warning("egress blocked (offline-only): %s", url)
        raise EgressBlocked(
            "Offline-only mode is on — DropLens refused to contact "
            f"{urlparse(url).hostname or url}. Turn it off in Settings to use a cloud model."
        )


def log_egress(kind: str, target: str, chars: int = 0, doc_id: Optional[int] = None) -> None:
    """Record one outbound request (in memory and, when available, in SQLite)."""
    entry = {"ts": time.time(), "kind": kind, "target": target, "chars": int(chars or 0),
             "doc_id": doc_id, "local": is_local_url(target)}
    with _lock:
        _entries.append(entry)
        if len(_entries) > _MAX_MEMORY:
            del _entries[: len(_entries) - _MAX_MEMORY]
        sink = _sink
    if sink is not None:
        try:
            sink(entry)
        except Exception:  # noqa: BLE001 - logging must never break a request
            log.debug("egress log write failed", exc_info=True)


def recent(limit: int = 200) -> list[dict[str, Any]]:
    with _lock:
        return list(reversed(_entries[-limit:]))


def clear() -> None:
    with _lock:
        _entries.clear()
