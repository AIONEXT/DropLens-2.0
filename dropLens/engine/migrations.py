"""Versioned schema migrations for the DropLens catalogue.

The catalogue stores its schema version in ``PRAGMA user_version``. Databases
created by DropLens 2.0 report version 0; every migration below is idempotent,
so an old library upgrades in place the first time it is opened and a fresh
database simply ends up at the current version.

Rules for adding a migration:
  1. bump :data:`SCHEMA_VERSION`
  2. add a ``_v<N>`` function that only *adds* objects (tables, columns,
     indexes) — never rewrite user rows
  3. append it to :data:`MIGRATIONS`

Migrations run inside the caller's lock, before the first commit.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

# --- v2: chunk-level retrieval, durable AI jobs, egress transparency ---------
_V2_SQL = """
CREATE TABLE IF NOT EXISTS chunks (
    id         INTEGER PRIMARY KEY,
    doc_id     INTEGER NOT NULL,
    ord        INTEGER NOT NULL,
    page       INTEGER,
    start_char INTEGER NOT NULL DEFAULT 0,
    end_char   INTEGER NOT NULL DEFAULT 0,
    text       TEXT NOT NULL,
    token_est  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chunks_doc_ord ON chunks(doc_id, ord);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text,
    content = 'chunks',
    content_rowid = 'id',
    tokenize = 'unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_au AFTER UPDATE OF text ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id INTEGER PRIMARY KEY,
    doc_id   INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    model    TEXT,
    dim      INTEGER,
    ts       REAL
);
CREATE INDEX IF NOT EXISTS idx_chunk_vectors_doc ON chunk_vectors(doc_id);

CREATE TABLE IF NOT EXISTS ai_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    state        TEXT NOT NULL DEFAULT 'queued',
    attempts     INTEGER NOT NULL DEFAULT 0,
    next_at      REAL,
    last_error   TEXT,
    content_hash TEXT,
    updated_at   REAL,
    UNIQUE(doc_id, kind)
);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_state ON ai_jobs(state, next_at);

CREATE TABLE IF NOT EXISTS egress (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    ts     REAL NOT NULL,
    kind   TEXT NOT NULL,
    target TEXT NOT NULL,
    chars  INTEGER NOT NULL DEFAULT 0,
    doc_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_egress_ts ON egress(ts);
"""


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column in _columns(conn, table):
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        log.info("migration: added %s.%s", table, column)
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("migration: could not add %s.%s (%s)", table, column, exc)


def _v2(conn: sqlite3.Connection) -> None:
    conn.executescript(_V2_SQL)
    # docmeta gains the "which version of the file is this derived from" columns
    _ensure_column(conn, "docmeta", "content_hash", "TEXT")
    _ensure_column(conn, "docmeta", "embed_model", "TEXT")
    _ensure_column(conn, "docmeta", "embed_dim", "INTEGER")
    _ensure_column(conn, "docmeta", "chunk_hash", "TEXT")


MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (2, _v2),
]


def apply(conn: sqlite3.Connection) -> int:
    """Bring *conn* up to :data:`SCHEMA_VERSION`; returns the new version."""
    try:
        current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    except (sqlite3.Error, TypeError):  # pragma: no cover - defensive
        current = 0
    for version, fn in MIGRATIONS:
        if current >= version:
            continue
        try:
            fn(conn)
        except sqlite3.Error as exc:  # pragma: no cover - defensive
            log.error("migration to v%d failed: %s", version, exc)
            raise
        log.info("migration: applied v%d", version)
    if current < SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    return max(current, SCHEMA_VERSION)


def version(conn: sqlite3.Connection) -> int:
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    except (sqlite3.Error, TypeError):  # pragma: no cover - defensive
        return 0
