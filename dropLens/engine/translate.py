"""Machine translation of extracted content (on demand, cached in the DB).

Uses the free Google Translate web endpoint (no API key required). The
original extracted content is never modified — translations are stored as a
separate, clearly-derivative cache so source data quality is never impacted.
"""
from __future__ import annotations

import html
import re
import threading
import time

import requests

from .db import Catalog

GTX_URL = "https://translate.googleapis.com/translate_a/single"

SUPPORTED_LANGS = {
    "af": "Afrikaans", "ar": "Arabic", "az": "Azerbaijani", "be": "Belarusian",
    "bg": "Bulgarian", "bn": "Bengali", "bs": "Bosnian", "ca": "Catalan",
    "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German",
    "el": "Greek", "en": "English", "eo": "Esperanto", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish",
    "fr": "French", "ga": "Irish", "gl": "Galician", "gu": "Gujarati",
    "he": "Hebrew", "hi": "Hindi", "hr": "Croatian", "ht": "Haitian Creole",
    "hu": "Hungarian", "hy": "Armenian", "id": "Indonesian", "is": "Icelandic",
    "it": "Italian", "ja": "Japanese", "ka": "Georgian", "kk": "Kazakh",
    "km": "Khmer", "kn": "Kannada", "ko": "Korean", "ky": "Kyrgyz",
    "lo": "Lao", "lt": "Lithuanian", "lv": "Latvian", "mk": "Macedonian",
    "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "my": "Burmese",
    "ne": "Nepali", "nl": "Dutch", "no": "Norwegian", "pa": "Punjabi",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "ru": "Russian",
    "si": "Sinhala", "sk": "Slovak", "sl": "Slovenian", "sq": "Albanian",
    "sr": "Serbian", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil",
    "te": "Telugu", "th": "Thai", "tr": "Turkish", "uk": "Ukrainian",
    "ur": "Urdu", "uz": "Uzbek", "vi": "Vietnamese", "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)",
}


class TranslationError(Exception):
    pass


class Translator:
    """Thin client over the free Google Translate endpoint with DB caching."""

    def __init__(self, catalog: Catalog | None = None, timeout: float = 30.0):
        self.catalog = catalog
        self.timeout = timeout
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "DropLens/1.0"

    # -- public -------------------------------------------------------------
    def translate_to_db(self, doc_id: int, text: str, target: str) -> str:
        """Translate *text*, cache by (doc_id, target), return result."""
        cached = self.catalog.translation(doc_id, target) if self.catalog else None
        if cached:
            return cached
        result = self.translate(text, target)
        if self.catalog:
            try:
                self.catalog.save_translation(doc_id, target, result)
            except Exception:
                pass
        return result

    def translate(self, text: str, target: str) -> str:
        """Translate *text* into *target* (ISO-639 code)."""
        if not text or not text.strip():
            return ""
        lang = target if target in SUPPORTED_LANGS else "en"
        chunks = self._chunk(text)
        parts: list[str] = []
        with self._lock:
            for i, chunk in enumerate(chunks):
                parts.append(self._translate_chunk(chunk, lang))
                if i < len(chunks) - 1:
                    time.sleep(0.15)
        return "\n".join(p for p in parts if p)

    # -- internals -----------------------------------------------------------
    @staticmethod
    def _chunk(text: str, max_len: int = 3800) -> list[str]:
        if len(text) <= max_len:
            return [text]
        chunks: list[str] = []
        buffer = ""
        for para in re.split(r"(\n{2,})", text):
            if len(buffer) + len(para) > max_len:
                if buffer:
                    chunks.append(buffer)
                buffer = para
            else:
                buffer += para
        if buffer:
            chunks.append(buffer)
        return chunks

    def _translate_chunk(self, chunk: str, lang: str) -> str:
        try:
            resp = self._session.get(
                GTX_URL,
                params={"client": "gtx", "sl": "auto", "tl": lang, "dt": "t", "q": chunk},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception as exc:
            raise TranslationError(f"Translation request failed: {exc}") from exc
        try:
            sentences = payload[0]
            parts = []
            for s in sentences:
                parts.append(s[0] or "")
            return html.unescape("".join(parts))
        except (IndexError, TypeError, KeyError) as exc:
            raise TranslationError("Unexpected translation response.") from exc