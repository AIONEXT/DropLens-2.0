"""SQLite file catalogue with FTS5 full-text search.

Schema
------
files        one row per indexed file / folder (metadata + status)
docs         FTS5 virtual table holding name, path and extracted content
translations cached machine translations of extracted content
roots        monitored folder roots

Thread safety: a single connection guarded by an RLock. Scans write from
worker threads, the GUI reads from the main thread.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roots (
    id         INTEGER PRIMARY KEY,
    path       TEXT UNIQUE NOT NULL,
    added_at   REAL NOT NULL,
    last_scan  REAL,
    status     TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS files (
    id       INTEGER PRIMARY KEY,
    path     TEXT UNIQUE NOT NULL,
    name     TEXT NOT NULL,
    ext      TEXT,
    size     INTEGER NOT NULL DEFAULT 0,
    mtime    REAL NOT NULL DEFAULT 0,
    ctime    REAL NOT NULL DEFAULT 0,
    root     TEXT NOT NULL,
    hash     TEXT,
    category TEXT NOT NULL DEFAULT 'other',
    kind     TEXT NOT NULL DEFAULT 'file',
    status   TEXT NOT NULL DEFAULT 'indexed',
    dup_id   TEXT,
    scanned_at REAL NOT NULL DEFAULT 0
);

CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    doc_id  UNINDEXED,
    name,
    path,
    content,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS translations (
    doc_id   INTEGER NOT NULL,
    lang     TEXT NOT NULL,
    text     TEXT NOT NULL,
    ts       REAL NOT NULL,
    PRIMARY KEY (doc_id, lang)
);

CREATE TABLE IF NOT EXISTS docmeta (
    doc_id     INTEGER PRIMARY KEY,
    summary    TEXT,
    summary_lang TEXT,
    tags       TEXT,
    notes      TEXT,
    favorite   INTEGER NOT NULL DEFAULT 0,
    ai_status  TEXT,
    embedding  BLOB,
    meta_ts    REAL
);

CREATE TABLE IF NOT EXISTS prefs (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_root   ON files(root);
CREATE INDEX IF NOT EXISTS idx_files_hashes ON files(hash);
CREATE INDEX IF NOT EXISTS idx_files_size   ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_cat    ON files(category);
"""

_RE_ESCAPE = re.compile(r'["*^]')


def _common_prefix(a: str, b: str) -> str:
    """Return the longest common directory prefix of two absolute paths."""
    a = os.path.normcase(os.path.normpath(a))
    b = os.path.normcase(os.path.normpath(b))
    if a == b:
        return a
    ap = a.split(os.sep)
    bp = b.split(os.sep)
    out = []
    for x, y in zip(ap, bp):
        if x != y:
            break
        out.append(x)
    return os.sep.join(out) or os.sep


class Catalog:
    """Owns the SQLite connection and exposes all persistence operations."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=15000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # -- transactional helpers -------------------------------------------
    def begin(self) -> None:
        with self._lock:
            self._conn.execute("BEGIN")

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, tuple(params))

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, seq)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # -- roots -----------------------------------------------------------
    def add_root(self, path: str) -> int:
        path = os.path.abspath(path)
        now = time.time()
        cur = self.execute(
            "INSERT INTO roots(path, added_at) VALUES(?, ?) "
            "ON CONFLICT(path) DO UPDATE SET added_at=excluded.added_at",
            (path, now),
        )
        self.commit()
        row = self.execute("SELECT id FROM roots WHERE path=?", (path,)).fetchone()
        return int(row["id"])

    def list_roots(self) -> list[sqlite3.Row]:
        rows = self.execute(
            "SELECT r.*, "
            "  (SELECT COUNT(*) FROM files f WHERE f.root=r.path) AS files, "
            "  (SELECT COUNT(*) FROM files f WHERE f.root=r.path AND f.kind='folder') AS folders "
            "FROM roots r ORDER BY r.path"
        ).fetchall()
        return rows  # type: ignore[return-value]

    def remove_root(self, path: str) -> None:
        path = os.path.abspath(path)
        ids = self.execute("SELECT id FROM files WHERE root=?", (path,)).fetchall()
        id_list = [r["id"] for r in ids]
        self._delete_files(id_list)
        self.execute("DELETE FROM roots WHERE path=?", (path,))
        self.commit()

    def touch_root_scan(self, path: str, status: str = "indexed") -> None:
        self.execute(
            "UPDATE roots SET last_scan=?, status=? WHERE path=?", (time.time(), status, os.path.abspath(path))
        )
        self.commit()

    # -- files + docs -----------------------------------------------------
    def resolve_id(self, path: str) -> int | None:
        row = self.execute("SELECT id FROM files WHERE path=?", (path,)).fetchone()
        return int(row["id"]) if row else None

    def get(self, file_id: int) -> Optional[sqlite3.Row]:
        return self.execute(
            "SELECT *, (SELECT content FROM docs d WHERE d.doc_id=files.id) AS content "
            "FROM files WHERE id=?",
            (file_id,),
        ).fetchone()

    def path_meta(self, path: str) -> Optional[sqlite3.Row]:
        return self.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()

    def existing_in_root(self, root: str) -> dict[str, tuple[int, int, float]]:
        """Return {path: (id, size, mtime)} for every file already indexed under root."""
        rows = self.execute("SELECT path, id, size, mtime FROM files WHERE root=?", (root,)).fetchall()
        return {r["path"]: (r["id"], r["size"], r["mtime"]) for r in rows}

    def has_same_size(self, size: int) -> bool:
        row = self.execute(
            "SELECT 1 FROM files WHERE size=? AND kind='file' LIMIT 1", (size,)
        ).fetchone()
        return row is not None

    def hash_exists(self, digest: str) -> int:
        row = self.execute(
            "SELECT id FROM files WHERE hash=? LIMIT 1", (digest,)
        ).fetchone()
        return int(row["id"]) if row else 0

    def upsert_file(self, rec: dict[str, Any]) -> int:
        keys = ["path", "name", "ext", "size", "mtime", "ctime", "root", "hash",
                "category", "kind", "status", "dup_id", "scanned_at"]
        vals = [rec.get(k) for k in keys]
        sql = (
            "INSERT INTO files(" + ",".join(keys) + ") VALUES(" + ",".join("?" * len(keys)) + ") "
            "ON CONFLICT(path) DO UPDATE SET "
            "name=excluded.name, ext=excluded.ext, size=excluded.size, mtime=excluded.mtime, "
            "ctime=excluded.ctime, hash=excluded.hash, category=excluded.category, "
            "kind=excluded.kind, status=excluded.status, dup_id=excluded.dup_id, "
            "scanned_at=excluded.scanned_at"
        )
        self.execute(sql, vals)
        return self.resolve_id(rec["path"]) or 0

    def upsert_doc(self, doc_id: int, name: str, path: str, content: str) -> None:
        if doc_id <= 0:
            return
        self.execute("DELETE FROM docs WHERE doc_id=?", (doc_id,))
        self.execute(
            "INSERT INTO docs(doc_id, name, path, content) VALUES(?,?,?,?)",
            (doc_id, name, path, content),
        )

    def pending_inserts_commit(self) -> None:
        pass

    def mark_deleted(self, ids: Iterable[int]) -> int:
        id_list = list(ids)
        if not id_list:
            return 0
        # delete in chunks to respect SQLite variable limits
        total = 0
        for i in range(0, len(id_list), 900):
            chunk = id_list[i:i + 900]
            marks = ",".join("?" * len(chunk))
            self.execute(f"DELETE FROM files WHERE id IN ({marks})", chunk)
            for c in chunk:
                self.execute("DELETE FROM docs WHERE doc_id=?", (c,))
            total += len(chunk)
        return total

    def _delete_files(self, ids: Iterable[int]) -> int:
        return self.mark_deleted(ids)

    def translation(self, doc_id: int, lang: str) -> Optional[str]:
        row = self.execute(
            "SELECT text FROM translations WHERE doc_id=? AND lang=?", (doc_id, lang)
        ).fetchone()
        return str(row["text"]) if row else None

    def save_translation(self, doc_id: int, lang: str, text: str) -> None:
        self.execute(
            "INSERT INTO translations(doc_id, lang, text, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(doc_id, lang) DO UPDATE SET text=excluded.text, ts=excluded.ts",
            (doc_id, lang, text, time.time()),
        )
        self.commit()

    # -- search -----------------------------------------------------------
    _SNIP_B = "\x01"
    _SNIP_E = "\x02"

    def search(
        self,
        expr: str,
        category: str = "",
        root: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        clause = self._file_clause(category, root)
        sql = (
            "SELECT f.id, f.name, f.path, f.ext, f.size, f.mtime, f.category, f.kind, f.status, "
            "       f.hash AS file_hash, f.dup_id, "
            "       snippet(docs, 3, ?, ?, ' … ', 20) AS snip, "
            "       bm25(docs) AS rank "
            "FROM docs JOIN files f ON f.id = docs.doc_id "
            "WHERE docs MATCH ?" + clause + " "
            "ORDER BY bm25(docs) LIMIT ? OFFSET ?"
        )
        params: list[Any] = [self._SNIP_B, self._SNIP_E, expr]
        params += self._file_clause_params(category, root)
        params += [limit, offset]
        return self.execute(sql, params).fetchall()  # type: ignore[return-value]

    def search_count(self, expr: str, category: str = "", root: str = "") -> int:
        sql = (
            "SELECT COUNT(*) AS n FROM docs JOIN files f ON f.id=docs.doc_id "
            "WHERE docs MATCH ?" + self._file_clause(category, root)
        )
        params: list[Any] = [expr] + self._file_clause_params(category, root)
        row = self.execute(sql, params).fetchone()
        return int(row["n"]) if row else 0

    @staticmethod
    def _file_clause(category: str, root: str) -> str:
        parts = [" AND "]
        clauses = []
        if root:
            clauses.append("f.root = ?")
        if category:
            clauses.append("f.category = ?")
        if clauses:
            parts.append(" AND ".join(clauses))
        return parts[0] + (" AND ".join(clauses) if clauses else "1=1")

    @staticmethod
    def _file_clause_params(category: str, root: str) -> list[Any]:
        params: list[Any] = []
        if root:
            params.append(root)
        if category:
            params.append(category)
        return params

    def name_search(
        self,
        term: str,
        category: str = "",
        root: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[sqlite3.Row]:
        like = f"%{term}%"
        params: list[Any] = [like, like]
        clause = ""
        if root:
            clause += " AND root = ?"
            params.append(root)
        if category:
            clause += " AND category = ?"
            params.append(category)
        sql = (
            "SELECT id, name, path, ext, size, mtime, category, kind, status, hash AS file_hash, dup_id, "
            "  '' AS snip, 0 AS rank "
            "FROM files WHERE (lower(name) LIKE ? OR lower(path) LIKE ?)" + clause +
            " ORDER BY size DESC LIMIT ? OFFSET ?"
        )
        params = params + [limit, offset]
        return self.execute(sql, params).fetchall()  # type: ignore[return-value]

    # -- document metadata (AI insights, notes, favourites) -------------------
    def get_meta(self, doc_id: int) -> Optional[sqlite3.Row]:
        return self.execute(
            "SELECT * FROM docmeta WHERE doc_id=?", (doc_id,)
        ).fetchone()

    def upsert_meta(self, doc_id: int, **fields) -> None:
        if doc_id <= 0:
            return
        current = self.get_meta(doc_id)
        values = {
            "summary": fields.get("summary"),
            "summary_lang": fields.get("summary_lang"),
            "tags": fields.get("tags"),
            "notes": fields.get("notes"),
            "favorite": fields.get("favorite"),
            "ai_status": fields.get("ai_status"),
            "embedding": fields.get("embedding"),
            "meta_ts": time.time(),
        }
        if current:
            merged = dict(current)
            for k, v in values.items():
                if v is not None:
                    merged[k] = v
            merged["meta_ts"] = values["meta_ts"]
            self.execute(
                "UPDATE docmeta SET summary=?, summary_lang=?, tags=?, notes=?, favorite=?, "
                "ai_status=?, embedding=?, meta_ts=? WHERE doc_id=?",
                (merged["summary"], merged["summary_lang"], merged["tags"], merged["notes"],
                 merged["favorite"], merged["ai_status"], merged["embedding"], merged["meta_ts"], doc_id),
            )
        else:
            self.execute(
                "INSERT OR REPLACE INTO docmeta(doc_id, summary, summary_lang, tags, notes, favorite, "
                "ai_status, embedding, meta_ts) VALUES(?,?,?,?,?,?,?,?,?)",
                (doc_id, values["summary"], values["summary_lang"], values["tags"], values["notes"],
                 values["favorite"] or 0, values["ai_status"], values["embedding"], values["meta_ts"]),
            )
        self.commit()

    def toggle_favorite(self, doc_id: int) -> int:
        meta = self.get_meta(doc_id)
        flag = 1 if (meta and meta["favorite"]) else 0
        new_flag = 0 if flag else 1
        self.upsert_meta(doc_id, favorite=new_flag)
        return new_flag

    def favorites(self, limit: int = 1000) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT f.id, f.name, f.path, f.ext, f.size, f.mtime, f.category, f.kind, f.status, "
            "       f.hash AS file_hash, f.dup_id, '' AS snip, 0 AS rank, m.tags, m.summary "
            "FROM docmeta m JOIN files f ON f.id=m.doc_id "
            "WHERE m.favorite=1 AND f.kind='file' ORDER BY m.meta_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()  # type: ignore[return-value]

    def list_tags(self) -> list[tuple[str, int]]:
        """Return [(tag, file_count), ...] across the whole library."""
        rows = self.execute(
            "SELECT tags FROM docmeta WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
        counts: dict[str, int] = {}
        for r in rows:
            for t in str(r["tags"]).split(","):
                t = t.strip()
                if t:
                    counts[t] = counts.get(t, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def files_with_tag(self, tag: str, limit: int = 500) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT f.id, f.name, f.path, f.ext, f.size, f.mtime, f.category, f.kind, f.status, "
            "       f.hash AS file_hash, f.dup_id, '' AS snip, 0 AS rank "
            "FROM docmeta m JOIN files f ON f.id=m.doc_id "
            "WHERE m.tags IS NOT NULL AND (',' || REPLACE(m.tags,' ', '') || ',') LIKE ? "
            "LIMIT ?",
            (f"%,{tag},%", limit),
        ).fetchall()  # type: ignore[return-value]

    def embedding_rows(self, max_chars_of_note: int = 4000) -> list[tuple[int, bytes, str]]:
        """(doc_id, embedding blob, name) for every documented file with an embedding."""
        rows = self.execute(
            "SELECT m.doc_id, m.embedding, f.name FROM docmeta m "
            "JOIN files f ON f.id=m.doc_id WHERE m.embedding IS NOT NULL"
        ).fetchall()
        return [(r["doc_id"], bytes(r["embedding"]), r["name"]) for r in rows]

    def has_embeddings(self) -> bool:
        row = self.execute("SELECT 1 FROM docmeta WHERE embedding IS NOT NULL LIMIT 1").fetchone()
        return row is not None

    # -- app preferences (key/value, e.g. onboarding state) --------------------
    def pref_get(self, key: str, default: str = "") -> str:
        row = self.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else default

    def pref_set(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO prefs(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.commit()

    # -- AI helpers ----------------------------------------------------------
    def by_name(self, name: str) -> Optional[sqlite3.Row]:
        return self.execute(
            "SELECT f.*, d.content FROM files f LEFT JOIN docs d ON d.doc_id=f.id "
            "WHERE f.kind='file' AND f.name=? ORDER BY f.size DESC LIMIT 1", (name,)
        ).fetchone()

    def files_under(self, root: str, limit: int = 2000) -> list[sqlite3.Row]:
        """Files whose path lives inside ``root`` (excluding root itself if a file)."""
        root = root.rstrip("\\/")
        like = root + "%"
        return self.execute(
            "SELECT * FROM files WHERE kind='file' AND path != ? AND path LIKE ? "
            "ORDER BY mtime DESC LIMIT ?", (root, like, limit),
        ).fetchall()  # type: ignore[return-value]

    def files_for_ai(self, limit: int = 3000) -> list[sqlite3.Row]:
        """Files most likely to benefit from AI enrichment (has text content)."""
        return self.execute(
            "SELECT f.id FROM files f WHERE f.kind='file' AND f.status='indexed' "
            "ORDER BY f.size ASC LIMIT ?", (limit,),
        ).fetchall()  # type: ignore[return-value]

    # -- stats ------------------------------------------------------------
    def stats(self) -> dict[str, Any]:
        total = self.execute("SELECT COUNT(*) AS n FROM files WHERE kind='file'").fetchone()
        folders = self.execute("SELECT COUNT(*) AS n FROM files WHERE kind='folder'").fetchone()
        size = self.execute(
            "SELECT COALESCE(SUM(size),0) AS s FROM files WHERE kind='file' AND status!='gone'"
        ).fetchone()
        by_cat = self.execute(
            "SELECT category, COUNT(*) AS n, COALESCE(SUM(size),0) AS bytes "
            "FROM files WHERE kind='file' GROUP BY category ORDER BY n DESC"
        ).fetchall()
        dups = self.execute(
            "SELECT COUNT(*) AS g FROM "
            "(SELECT hash FROM files WHERE hash IS NOT NULL AND hash NOT IN ('','empty') "
            "GROUP BY hash HAVING COUNT(*)>1)"
        ).fetchone()
        dup_files = self.execute(
            "SELECT COUNT(*) AS n FROM files "
            "WHERE hash IN (SELECT hash FROM files WHERE hash IS NOT NULL AND hash NOT IN ('','empty') "
            "GROUP BY hash HAVING COUNT(*)>1) "
            "AND kind='file'"
        ).fetchone()
        return {
            "files": int(total["n"]),
            "folders": int(folders["n"]),
            "bytes": int(size["s"]),
            "by_category": [(r["category"], r["n"], r["bytes"]) for r in by_cat],
            "dup_groups": int(dups["g"]),
            "dup_files": int(dup_files["n"]),
        }

    def duplicate_groups(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT hash, COUNT(*) AS n, SUM(size) AS bytes, MIN(path) AS example "
            "FROM files WHERE hash IS NOT NULL AND hash NOT IN ('','empty') "
            "GROUP BY hash HAVING COUNT(*) > 1 ORDER BY bytes DESC LIMIT ?",
            (limit,),
        ).fetchall()  # type: ignore[return-value]

    def extensions_top(self, limit: int = 25) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT COALESCE(NULLIF(ext,''),'[none]') AS ext, COUNT(*) AS n "
            "FROM files WHERE kind='file' GROUP BY ext ORDER BY n DESC LIMIT ?",
            (limit,),
        ).fetchall()  # type: ignore[return-value]

    def by_size_recent(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT name, path, size, mtime, category FROM files "
            "WHERE kind='file' ORDER BY mtime DESC LIMIT ?", (limit,)
        ).fetchall()  # type: ignore[return-value]

    def recent_files(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT id, name, path, ext, size, mtime, category, kind, status, "
            "       hash AS file_hash, dup_id, '' AS snip, 0 AS rank "
            "FROM files WHERE kind='file' AND status != 'gone' "
            "ORDER BY scanned_at DESC, mtime DESC LIMIT ?",
            (limit,),
        ).fetchall()  # type: ignore[return-value]