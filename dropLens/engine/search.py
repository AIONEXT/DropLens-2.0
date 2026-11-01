"""Full-text search helpers: safe FTS5 query construction + highlight tags."""
from __future__ import annotations

import re

MODES = ("all", "name", "path", "content")


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


class ResultCursor:
    """Stable pagination over the name and FTS result channels.

    Page 1 seeds both channels; every later page pulls the next window from
    each channel until enough *new* ids exist. Rows are served exactly once in
    a stable order, so "Load more" can neither skip nor repeat a hit — which
    is what offset arithmetic across a merged list did (review item H1).

    ``seed``/``next_page`` take plain rows and ``(offset, limit)`` fetchers, so
    the whole thing is unit-testable without Tk or a database.
    """

    def __init__(self, page: int = 300):
        self.page = max(1, int(page))
        self.served = 0
        self._queue: list = []
        self._seen: set[int] = set()
        self._offsets = {"name": 0, "fts": 0}
        self._done = {"name": False, "fts": False}

    @property
    def exhausted(self) -> bool:
        """Both channels drained and nothing left to serve."""
        return not self._queue and self._done["name"] and self._done["fts"]

    @property
    def queued(self) -> int:
        return len(self._queue)

    def seed(self, name_rows, fts_rows, name_enabled: bool = True) -> list:
        """First page: name hits first, then FTS hits, deduplicated by id."""
        name_rows = list(name_rows or [])
        fts_rows = list(fts_rows or [])
        self._offsets["name"] = len(name_rows) if name_enabled else 0
        self._offsets["fts"] = len(fts_rows)
        if not name_enabled or len(name_rows) < self.page:
            self._done["name"] = True
        if len(fts_rows) < self.page:
            self._done["fts"] = True
        self._push(name_rows)
        self._push(fts_rows)
        return self._take(len(self._queue))

    def next_page(self, fetch_name, fetch_fts) -> list:
        """Next batch of not-yet-served rows.

        ``fetch_name`` / ``fetch_fts`` are ``(offset, limit) -> rows``. A short
        window marks that channel exhausted; duplicated rows are dropped by
        ``_seen``, so a window that adds nothing simply advances the offset.
        """
        guard = 0
        while len(self._queue) < self.page and guard < 64:
            guard += 1
            if self._done["name"] and self._done["fts"]:
                break
            if not self._done["name"]:
                rows = list(fetch_name(self._offsets["name"], self.page) or [])
                self._offsets["name"] += len(rows)
                if len(rows) < self.page:
                    self._done["name"] = True
                self._push(rows)
            if len(self._queue) >= self.page:
                break
            if not self._done["fts"]:
                rows = list(fetch_fts(self._offsets["fts"], self.page) or [])
                self._offsets["fts"] += len(rows)
                if len(rows) < self.page:
                    self._done["fts"] = True
                self._push(rows)
        return self._take(self.page)

    def _push(self, rows) -> int:
        added = 0
        for r in rows:
            try:
                iid = int(r["id"])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if iid in self._seen:
                continue
            self._seen.add(iid)
            self._queue.append(r)
            added += 1
        return added

    def _take(self, n: int) -> list:
        out = self._queue[:n]
        self._queue = self._queue[n:]
        self.served += len(out)
        return out
