"""SQLite file catalogue: FTS5 search, chunk retrieval, AI state, job queue.

Schema
------
files          one row per indexed file / folder (metadata + status)
docs           FTS5 virtual table holding name, path and extracted content
chunks         paragraph-level windows of extracted text (+ page, offsets)
chunks_fts     FTS5 index over ``chunks`` (kept in sync by triggers)
chunk_vectors  one embedding per chunk (kept apart from ``chunks`` so that
               re-embedding never rewrites FTS rows)
translations   cached machine translations of extracted content
docmeta        derived AI data: summary, tags, notes, favourite, doc vector,
               and the content hash those were computed from
ai_jobs        durable enrichment queue (summary / tags / embed)
egress         local log of outbound network requests
roots          monitored folder roots
prefs          key/value application preferences

Thread safety: a single connection guarded by an RLock. Scans write from
worker threads, the GUI reads from the main thread.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
from typing import Any, Iterable, Optional, Sequence

from . import migrations

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roots (
    id         INTEGER PRIMARY KEY,
    path       TEXT UNIQUE NOT NULL,
    added_at   REAL NOT NULL,
    last_scan  REAL,
    status     TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS files (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
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


def row_get(row: Any, key: str, default: Any = None) -> Any:
    """Read *key* from a ``sqlite3.Row``, a dict or None — without crashing.

    ``sqlite3.Row`` has no ``.get()``; every boundary that mixes dicts and rows
    goes through this helper instead.
    """
    if row is None:
        return default
    if isinstance(row, dict):
        value = row.get(key, default)
        return default if value is None else value
    try:
        keys = row.keys()
    except AttributeError:
        return default
    if key not in keys:
        return default
    try:
        value = row[key]
    except (IndexError, KeyError):
        return default
    return default if value is None else value


def content_hash(text: str, name: str = "", size: int = 0) -> str:
    """Stable fingerprint of a document's indexed text (detects staleness)."""
    h = hashlib.sha1()
    h.update((text or "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update((name or "").encode("utf-8", "replace"))
    h.update(b"\x00")
    h.update(str(int(size or 0)).encode("ascii"))
    return h.hexdigest()


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
            self.schema_version = migrations.apply(self._conn)
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
        self.execute(
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
            "UPDATE roots SET last_scan=?, status=? WHERE path=?",
            (time.time(), status, os.path.abspath(path)),
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
        row = self.execute("SELECT id FROM files WHERE hash=? LIMIT 1", (digest,)).fetchone()
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

    def save_doc(self, doc_id: int, name: str, path: str, text: str,
                 digest: str | None = None) -> bool:
        """Replace the searchable copy of a document.

        Returns True when the text changed, in which case every derived AI
        artifact (summary, tags, doc vector, chunks) is invalidated: today a
        file edited on disk keeps advertising its old summary forever.
        """
        if doc_id <= 0:
            return False
        digest = digest or content_hash(text, name)
        self.execute("DELETE FROM docs WHERE doc_id=?", (doc_id,))
        self.execute(
            "INSERT INTO docs(doc_id, name, path, content) VALUES(?,?,?,?)",
            (doc_id, name, path, text or ""),
        )
        row = self.execute("SELECT content_hash, summary, tags, embedding FROM docmeta "
                           "WHERE doc_id=?", (doc_id,)).fetchone()
        if row is not None and row["content_hash"] == digest:
            return False
        invalidated = row is not None and (
            row["summary"] is not None or row["tags"] is not None or row["embedding"] is not None
        )
        self.execute(
            "INSERT INTO docmeta(doc_id, favorite, content_hash, meta_ts) VALUES(?,0,?,?) "
            "ON CONFLICT(doc_id) DO UPDATE SET content_hash=excluded.content_hash, "
            "  summary=NULL, summary_lang=NULL, tags=NULL, ai_status=NULL, embedding=NULL, "
            "  embed_model=NULL, embed_dim=NULL, chunk_hash=NULL, meta_ts=excluded.meta_ts",
            (doc_id, digest, time.time()),
        )
        self.delete_chunks(doc_id)
        if invalidated:
            log.info("AI artifacts invalidated for doc %s (content changed)", doc_id)
        return True

    def mark_deleted(self, ids: Iterable[int]) -> int:
        """Remove catalogue rows and everything derived from them."""
        id_list = [int(i) for i in ids if i]
        if not id_list:
            return 0
        total = 0
        for i in range(0, len(id_list), 500):
            chunk = id_list[i:i + 500]
            marks = ",".join("?" * len(chunk))
            self.execute(f"DELETE FROM chunk_vectors WHERE doc_id IN ({marks})", chunk)
            self.execute(f"DELETE FROM chunks WHERE doc_id IN ({marks})", chunk)
            self.execute(f"DELETE FROM docs WHERE doc_id IN ({marks})", chunk)
            self.execute(f"DELETE FROM docmeta WHERE doc_id IN ({marks})", chunk)
            self.execute(f"DELETE FROM translations WHERE doc_id IN ({marks})", chunk)
            self.execute(f"DELETE FROM ai_jobs WHERE doc_id IN ({marks})", chunk)
            self.execute(f"DELETE FROM files WHERE id IN ({marks})", chunk)
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

    _HIT_COLUMNS = (
        "f.id, f.name, f.path, f.ext, f.size, f.mtime, f.category, f.kind, f.status, "
        "f.hash AS file_hash, f.dup_id, "
        "COALESCE(m.favorite, 0) AS favorite, "
        "CASE WHEN m.summary IS NULL OR m.summary='' THEN 0 ELSE 1 END AS has_summary"
    )

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
            "SELECT " + self._HIT_COLUMNS + ", "
            "       snippet(docs, 3, ?, ?, ' … ', 20) AS snip, "
            "       bm25(docs) AS rank "
            "FROM docs JOIN files f ON f.id = docs.doc_id "
            "LEFT JOIN docmeta m ON m.doc_id = f.id "
            "WHERE docs MATCH ?" + clause + " "
            "ORDER BY bm25(docs) LIMIT ? OFFSET ?"
        )
        params: list[Any] = [self._SNIP_B, self._SNIP_E, expr]
        params += self._file_clause_params(category, root)
        params += [limit, offset]
        return self.execute(sql, params).fetchall()  # type: ignore[return-value]

    @staticmethod
    def _file_clause(category: str, root: str) -> str:
        clauses = []
        if root:
            clauses.append("f.root = ?")
        if category:
            clauses.append("f.category = ?")
        return " AND " + (" AND ".join(clauses) if clauses else "1=1")

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
            clause += " AND f.root = ?"
            params.append(root)
        if category:
            clause += " AND f.category = ?"
            params.append(category)
        sql = (
            "SELECT " + self._HIT_COLUMNS + ", '' AS snip, 0 AS rank "
            "FROM files f LEFT JOIN docmeta m ON m.doc_id = f.id "
            "WHERE (lower(f.name) LIKE ? OR lower(f.path) LIKE ?)" + clause +
            " ORDER BY f.size DESC LIMIT ? OFFSET ?"
        )
        params = params + [limit, offset]
        return self.execute(sql, params).fetchall()  # type: ignore[return-value]

    # -- chunks (paragraph-level retrieval) ------------------------------
    def file_ids(self, category: str = "", root: str = "") -> Optional[set[int]]:
        """File ids matching the category/root filter.

        ``None`` when the filter is empty (nothing to restrict by). Used to
        give the vector retrieval channel the same filter the FTS channel
        already applies in SQL — without it, a category filter silently
        widened semantic results (review item H2).
        """
        if not category and not root:
            return None
        sql = "SELECT f.id FROM files f WHERE 1=1" + self._file_clause(category, root)
        rows = self.execute(sql, self._file_clause_params(category, root)).fetchall()
        return {int(r["id"]) for r in rows}
    def delete_chunks(self, doc_id: int) -> None:
        self.execute("DELETE FROM chunk_vectors WHERE doc_id=?", (doc_id,))
        self.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

    def replace_chunks(self, doc_id: int, chunks: Sequence[Any]) -> list[int]:
        """Store ``chunk.Chunk`` objects for a document; returns their ids."""
        self.delete_chunks(doc_id)
        rows = [
            (doc_id, c.ord, c.page, c.start_char, c.end_char, c.text, c.token_est)
            for c in chunks
        ]
        if not rows:
            return []
        self.executemany(
            "INSERT INTO chunks(doc_id, ord, page, start_char, end_char, text, token_est) "
            "VALUES(?,?,?,?,?,?,?)",
            rows,
        )
        ids = self.execute(
            "SELECT id FROM chunks WHERE doc_id=? ORDER BY ord", (doc_id,)
        ).fetchall()
        return [int(r["id"]) for r in ids]

    def chunks_for_doc(self, doc_id: int) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT * FROM chunks WHERE doc_id=? ORDER BY ord", (doc_id,)
        ).fetchall()  # type: ignore[return-value]

    def chunks_by_ids(self, ids: Sequence[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        out: dict[int, sqlite3.Row] = {}
        for i in range(0, len(ids), 500):
            part = list(ids[i:i + 500])
            marks = ",".join("?" * len(part))
            for r in self.execute(
                "SELECT c.*, f.name, f.path, COALESCE(m.favorite, 0) AS favorite FROM chunks c "
                "JOIN files f ON f.id = c.doc_id "
                "LEFT JOIN docmeta m ON m.doc_id = c.doc_id "
                f"WHERE c.id IN ({marks})",
                part,
            ):
                out[int(r["id"])] = r
        return out

    def search_chunks(self, expr: str, limit: int = 60,
                      category: str = "", root: str = "") -> list[sqlite3.Row]:
        """BM25 ranking over chunk text (same query language as ``search``)."""
        clause = self._file_clause(category, root)
        sql = (
            "SELECT c.id AS chunk_id, c.doc_id, c.ord, c.page, c.start_char, c.end_char, "
            "       c.text, f.name, f.path, bm25(chunks_fts) AS rank "
            "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
            "JOIN files f ON f.id = c.doc_id "
            "WHERE chunks_fts MATCH ?" + clause + " "
            "ORDER BY bm25(chunks_fts) LIMIT ?"
        )
        params: list[Any] = [expr] + self._file_clause_params(category, root) + [limit]
        try:
            return self.execute(sql, params).fetchall()  # type: ignore[return-value]
        except sqlite3.OperationalError as exc:
            log.warning("chunk FTS query failed: %s", exc)
            return []

    def chunk_count(self) -> int:
        row = self.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()
        return int(row["n"]) if row else 0

    # -- chunk vectors ----------------------------------------------------
    def set_chunk_vector(self, chunk_id: int, doc_id: int, blob: bytes,
                         model: str = "", dim: int = 0) -> None:
        self.execute(
            "INSERT INTO chunk_vectors(chunk_id, doc_id, embedding, model, dim, ts) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(chunk_id) DO UPDATE SET "
            "embedding=excluded.embedding, model=excluded.model, "
            "dim=excluded.dim, ts=excluded.ts",
            (chunk_id, doc_id, blob, model, dim, time.time()),
        )

    def chunk_vector_rows(self) -> list[tuple[int, int, bytes, int]]:
        """(chunk_id, doc_id, blob, dim) for every embedded chunk."""
        rows = self.execute(
            "SELECT chunk_id, doc_id, embedding, COALESCE(dim, 0) AS dim FROM chunk_vectors"
        ).fetchall()
        return [(int(r["chunk_id"]), int(r["doc_id"]), bytes(r["embedding"]), int(r["dim"]))
                for r in rows]

    def chunk_vector_count(self) -> int:
        row = self.execute("SELECT COUNT(*) AS n FROM chunk_vectors").fetchone()
        return int(row["n"]) if row else 0

    def docs_with_chunks(self, limit: int = 2000) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT DISTINCT doc_id FROM chunks LIMIT ?", (limit,)
        ).fetchall()  # type: ignore[return-value]

    # -- document metadata (AI insights, notes, favourites) -------------------
    def get_meta(self, doc_id: int) -> Optional[sqlite3.Row]:
        return self.execute("SELECT * FROM docmeta WHERE doc_id=?", (doc_id,)).fetchone()

    def upsert_meta(self, doc_id: int, commit: bool = True, **fields: Any) -> None:
        """Write AI metadata without a read-modify-write cycle.

        Two statements, no SELECT: None means "leave the existing value".
        """
        if doc_id <= 0:
            return
        self.execute(
            "INSERT INTO docmeta(doc_id, summary, summary_lang, tags, notes, favorite, "
            "ai_status, embedding, meta_ts, content_hash, embed_model, embed_dim, chunk_hash) "
            "VALUES(?,?,?,?,?,COALESCE(?,0),?,?,?,?,?,?,?) "
            "ON CONFLICT(doc_id) DO UPDATE SET "
            "  summary      = COALESCE(excluded.summary, docmeta.summary), "
            "  summary_lang = COALESCE(excluded.summary_lang, docmeta.summary_lang), "
            "  tags         = COALESCE(excluded.tags, docmeta.tags), "
            "  notes        = COALESCE(excluded.notes, docmeta.notes), "
            "  favorite     = COALESCE(?, docmeta.favorite), "
            "  ai_status    = COALESCE(excluded.ai_status, docmeta.ai_status), "
            "  embedding    = COALESCE(excluded.embedding, docmeta.embedding), "
            "  content_hash = COALESCE(excluded.content_hash, docmeta.content_hash), "
            "  embed_model  = COALESCE(excluded.embed_model, docmeta.embed_model), "
            "  embed_dim    = COALESCE(excluded.embed_dim, docmeta.embed_dim), "
            "  chunk_hash   = COALESCE(excluded.chunk_hash, docmeta.chunk_hash), "
            "  meta_ts      = excluded.meta_ts",
            (
                doc_id,
                fields.get("summary"),
                fields.get("summary_lang"),
                fields.get("tags"),
                fields.get("notes"),
                fields.get("favorite"),
                fields.get("ai_status"),
                fields.get("embedding"),
                time.time(),
                fields.get("content_hash"),
                fields.get("embed_model"),
                fields.get("embed_dim"),
                fields.get("chunk_hash"),
                fields.get("favorite"),
            ),
        )
        if commit:
            self.commit()

    def toggle_favorite(self, doc_id: int) -> int:
        row = self.execute("SELECT favorite FROM docmeta WHERE doc_id=?", (doc_id,)).fetchone()
        new_flag = 0 if (row and row["favorite"]) else 1
        self.upsert_meta(doc_id, favorite=new_flag)
        return new_flag

    def favorites(self, limit: int = 1000) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT " + self._HIT_COLUMNS + ", '' AS snip, 0 AS rank, m.tags, m.summary "
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
            "SELECT " + self._HIT_COLUMNS + ", '' AS snip, 0 AS rank "
            "FROM docmeta m JOIN files f ON f.id=m.doc_id "
            "WHERE m.tags IS NOT NULL AND (',' || REPLACE(m.tags,' ', '') || ',') LIKE ? "
            "LIMIT ?",
            (f"%,{tag},%", limit),
        ).fetchall()  # type: ignore[return-value]

    def embedding_rows(self) -> list[tuple[int, bytes, str]]:
        """(doc_id, embedding blob, name) for every documented file with a vector."""
        rows = self.execute(
            "SELECT m.doc_id, m.embedding, f.name FROM docmeta m "
            "JOIN files f ON f.id=m.doc_id WHERE m.embedding IS NOT NULL"
        ).fetchall()
        return [(r["doc_id"], bytes(r["embedding"]), r["name"]) for r in rows]

    def has_embeddings(self) -> bool:
        row = self.execute(
            "SELECT 1 FROM chunk_vectors LIMIT 1"
        ).fetchone()
        if row is not None:
            return True
        row = self.execute("SELECT 1 FROM docmeta WHERE embedding IS NOT NULL LIMIT 1").fetchone()
        return row is not None

    def ai_enriched_count(self) -> int:
        row = self.execute(
            "SELECT COUNT(*) AS n FROM docmeta WHERE summary IS NOT NULL AND summary != ''"
        ).fetchone()
        return int(row["n"]) if row else 0

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

    # -- durable AI job queue ----------------------------------------------
    AI_KINDS = ("summary", "tags", "embed")

    def enqueue_ai_jobs(self, kinds: Sequence[str] = AI_KINDS, limit: int | None = None,
                        retry_failed: bool = False) -> int:
        """Queue enrichment work for every file whose artifacts are missing."""
        missing_sql = {
            "summary": "(m.summary IS NULL OR m.summary='')",
            "tags": "(m.tags IS NULL OR m.tags='')",
            "embed": ("(m.embedding IS NULL OR NOT EXISTS "
                      "(SELECT 1 FROM chunk_vectors v WHERE v.doc_id=f.id))"),
        }
        for kind in kinds:
            cond = missing_sql.get(kind)
            if not cond:
                continue
            rows = self.execute(
                "SELECT f.id AS doc_id, m.content_hash AS content_hash "
                "FROM files f LEFT JOIN docmeta m ON m.doc_id=f.id "
                "WHERE f.kind='file' AND f.status IN ('indexed','no_content') AND " + cond +
                " ORDER BY COALESCE(m.favorite,0) DESC, f.size ASC"
                + (f" LIMIT {int(limit)}" if limit else ""),
            ).fetchall()
            if not rows:
                continue
            # Re-open finished jobs: their artifacts are missing again (the file
            # changed), so a 'done' row must not shadow the work. A 'queued' row is
            # left alone so an in-flight backoff survives repeated enqueues.
            reopen = "'done','failed'" if retry_failed else "'done'"
            insert = (
                "INSERT INTO ai_jobs(doc_id, kind, state, attempts, content_hash, updated_at) "
                "VALUES(?,?,'queued',0,?,?) "
                "ON CONFLICT(doc_id, kind) DO UPDATE SET "
                "state='queued', attempts=0, next_at=NULL, updated_at=excluded.updated_at "
                f"WHERE ai_jobs.state IN ({reopen})"
            )
            now = time.time()
            self.executemany(insert, [(int(r["doc_id"]), kind, r["content_hash"], now) for r in rows])
        self.commit()
        row = self.execute("SELECT COUNT(*) AS n FROM ai_jobs WHERE state='queued'").fetchone()
        return int(row["n"]) if row else 0

    def runnable_jobs(self, limit: int = 32) -> list[sqlite3.Row]:
        now = time.time()
        return self.execute(
            "SELECT j.id, j.doc_id, j.kind, j.attempts, f.name, f.size, "
            "       COALESCE(m.favorite,0) AS favorite "
            "FROM ai_jobs j JOIN files f ON f.id=j.doc_id "
            "LEFT JOIN docmeta m ON m.doc_id=j.doc_id "
            "WHERE j.state='queued' AND (j.next_at IS NULL OR j.next_at <= ?) "
            "ORDER BY COALESCE(m.favorite,0) DESC, f.size ASC, j.id LIMIT ?",
            (now, limit),
        ).fetchall()  # type: ignore[return-value]

    def finish_job(self, job_id: int, doc_id: int, kind: str, state: str,
                   error: str | None = None, attempts: int = 0,
                   next_at: float | None = None, content_hash: str | None = None) -> None:
        self.execute(
            "UPDATE ai_jobs SET state=?, attempts=?, last_error=?, next_at=?, "
            "content_hash=COALESCE(?, content_hash), updated_at=? WHERE id=?",
            (state, attempts, (error or "")[:500] or None, next_at, content_hash,
             time.time(), job_id),
        )

    def job_counts(self) -> dict[str, int]:
        rows = self.execute("SELECT state, COUNT(*) AS n FROM ai_jobs GROUP BY state").fetchall()
        return {str(r["state"]): int(r["n"]) for r in rows}

    def clear_finished_jobs(self) -> int:
        self.execute("DELETE FROM ai_jobs WHERE state IN ('done','skipped')")
        self.commit()
        row = self.execute("SELECT changes() AS c").fetchone()
        return int(row["c"] or 0) if row else 0

    # -- egress log ---------------------------------------------------------
    def log_egress_entry(self, entry: dict[str, Any]) -> None:
        self.execute(
            "INSERT INTO egress(ts, kind, target, chars, doc_id) VALUES(?,?,?,?,?)",
            (entry.get("ts") or time.time(), entry.get("kind", ""),
             entry.get("target", ""), int(entry.get("chars") or 0), entry.get("doc_id")),
        )
        self.commit()

    def egress_rows(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT * FROM egress ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()  # type: ignore[return-value]

    def clear_egress(self) -> None:
        self.execute("DELETE FROM egress")
        self.commit()

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

    def recent_files(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.execute(
            "SELECT " + self._HIT_COLUMNS + ", '' AS snip, 0 AS rank "
            "FROM files f LEFT JOIN docmeta m ON m.doc_id = f.id "
            "WHERE f.kind='file' AND f.status != 'gone' "
            "ORDER BY f.scanned_at DESC, f.mtime DESC LIMIT ?",
            (limit,),
        ).fetchall()  # type: ignore[return-value]
