"""At-rest protection for API keys stored in ``settings.json``.

On Windows the key is sealed with DPAPI (``CryptProtectData``) under the
current user account, so a copied settings file is worthless on another
machine. On other platforms — and if DPAPI is unavailable — the value is kept
as-is and :func:`available` reports ``False`` so callers can be honest about it.

The on-disk form is ``dpapi:<base64>``; plaintext values from older versions
are migrated the next time settings are written.
"""
from __future__ import annotations

import base64
import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Optional

log = logging.getLogger(__name__)

PREFIX = "dpapi:"

_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = b"DropLens/settings/v2"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _as_bytes(blob: _DataBlob) -> bytes:
    return ctypes.string_at(blob.pbData, blob.cbData)


def _free(blob: _DataBlob) -> None:
    try:
        ctypes.windll.kernel32.LocalFree(blob.pbData)  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - best effort
        pass


def available() -> bool:
    """True when key material can actually be sealed on this machine."""
    return sys.platform == "win32"


def protect(text: str) -> Optional[str]:
    """Return ``dpapi:<base64>`` for *text*, or ``None`` if sealing failed."""
    if not text or not available():
        return None
    try:
        data_in = _blob(text.encode("utf-8"))
        entropy = _blob(_ENTROPY)
        data_out = _DataBlob()
        ok = ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
            ctypes.byref(data_in), None, ctypes.byref(entropy), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out),
        )
        if not ok:
            return None
        try:
            return PREFIX + base64.b64encode(_as_bytes(data_out)).decode("ascii")
        finally:
            _free(data_out)
    except Exception:  # pragma: no cover - defensive
        log.debug("DPAPI protect failed", exc_info=True)
        return None


def unprotect(value: str) -> str:
    """Inverse of :func:`protect`. Returns ``""`` when the value cannot be opened."""
    if not value or not value.startswith(PREFIX):
        return value or ""
    if not available():  # pragma: no cover - cross-platform reading of Windows settings
        log.warning("Stored API key is DPAPI-sealed; re-enter it on this platform.")
        return ""
    try:
        raw = base64.b64decode(value[len(PREFIX):])
        data_in = _blob(raw)
        entropy = _blob(_ENTROPY)
        data_out = _DataBlob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
            ctypes.byref(data_in), None, ctypes.byref(entropy), None, None,
            _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(data_out),
        )
        if not ok:
            return ""
        try:
            return _as_bytes(data_out).decode("utf-8", "replace")
        finally:
            _free(data_out)
    except Exception:  # pragma: no cover - defensive
        log.debug("DPAPI unprotect failed", exc_info=True)
        return ""


def seal_field(value: str) -> str:
    """Seal *value* for storage; leaves already-sealed or empty values untouched."""
    if not value or value.startswith(PREFIX):
        return value
    sealed = protect(value)
    return sealed or value


def open_field(value: str) -> str:
    return unprotect(value)
