"""Full-text search helpers: safe FTS5 query construction + highlight tags."""
from __future__ import annotations

import re

MODES = ("all", "name", "path", "content")

_SNIP_B = "\x01"
_SNIP_E = "\x02"


def tokenize(user_text: str) -> list[str]:
    """Split free text into quoted-safe FTS5 tokens (prefix match each)."""
    tokens = []
    for word in re.findall(r"[\w@.\-\u0080-\uffff]+", user_text or ""):
        word = word.replace('"', '""')
        tokens.append(f'"{word}*"')
    return tokens


def build_match(user_text: str, mode: str = "all") -> str | None:
    """Build an FTS5 MATCH expression from the user's text.

    Mode restricts which indexed columns are searched:
      all      -> name, path and content
      name     -> file names only
      path     -> folder paths only
      content  -> extracted text only
    Tokens are AND'd together and each gets a prefix wildcard so that
    "annual repor" also finds "annual-report".
    """
    tokens = tokenize(user_text)
    if not tokens:
        return None
    col = {"all": "", "name": "name:", "path": "path:", "content": "content:"}.get(mode, "")
    expr = f' {col}{tokens[0]}'
    for t in tokens[1:]:
        expr += f' AND {col}{t}'
    return expr.strip()


def highlight(text: str, terms: list[str], limit: int = 0) -> str:
    """Return *text* with every occurrence of the search terms wrapped in <mark>…</mark>."""
    if not text:
        return text
    if limit and len(text) > limit:
        text = text[:limit] + "\n…"
    if not terms:
        return text
    pattern = re.compile("|".join(re.escape(t) for t in terms if t), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", text)


def cleanse_snippet(snip: str, terms: list[str]) -> str:
    """Turn a raw FTS5 snippet (marker chars) into display text with <mark> tags."""
    if not snip:
        return ""
    out = snip.replace(_SNIP_B, "<mark>").replace(_SNIP_E, "</mark>")
    return out