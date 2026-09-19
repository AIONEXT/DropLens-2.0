"""CSV export of the catalogue and search results (UTF-8 with BOM for Excel)."""
from __future__ import annotations

import csv
import os
import time
from typing import Iterable, Optional

from .db import Catalog
from .discover import human_size

_COLUMNS = [
    "path", "name", "ext", "category", "kind", "size_bytes", "size_human",
    "modified", "created", "hash_md5", "duplicate_id", "status", "content_preview",
]


def _row_to_dict(row) -> dict:
    return {
        "path": row["path"],
        "name": row["name"],
        "ext": row["ext"] or "",
        "category": row["category"],
        "kind": row["kind"],
        "size_bytes": row["size"],
        "size_human": human_size(row["size"]),
        "modified": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["mtime"])),
        "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row["ctime"])) if row["ctime"] else "",
        "hash_md5": row["hash"] or row.get("file_hash") or "",
        "duplicate_id": row["dup_id"] or "",
        "status": row["status"],
        "content_preview": (row.get("content") or "")[:800].replace("\n", " "),
    }


def export_catalogue(catalog: Catalog, out_path: str) -> int:
    rows = catalog.execute(
        "SELECT f.*, d.content AS content FROM files f "
        "LEFT JOIN docs d ON d.doc_id=f.id WHERE f.kind='file' ORDER BY f.path"
    ).fetchall()
    return _write(out_path, (_row_to_dict(r) for r in rows))


def export_search_results(catalog: Catalog, file_ids: Iterable[int], out_path: str) -> int:
    ids = list(file_ids)
    if not ids:
        return _write(out_path, iter(()))
    rows = []
    for i in range(0, len(ids), 900):
        chunk = ids[i:i + 900]
        marks = ",".join("?" * len(chunk))
        rows.extend(catalog.execute(
            "SELECT f.*, d.content AS content FROM files f "
            f"LEFT JOIN docs d ON d.doc_id=f.id WHERE f.id IN ({marks}) ORDER BY f.path",
            chunk,
        ).fetchall())
    return _write(out_path, (_row_to_dict(r) for r in rows))


def _write(out_path: str, rows: Iterable[dict]) -> int:
    count = 0
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
            count += 1
    return count


def export_text(catalog: Catalog, doc_id: int, out_path: str, translated: Optional[str] = None) -> None:
    row = catalog.get(doc_id)
    if not row:
        raise FileNotFoundError("Document not found in catalogue.")
    lines = [
        "=" * 70,
        f"Document: {row['path']}",
        f"Category: {row['category']} | Kind: {row['kind']} | Size: {human_size(row['size'])}",
        f"Modified: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['mtime']))}",
        "=" * 70,
        "",
    ]
    if translated:
        lines.append("== TRANSLATION ==")
        lines.append(translated)
    if row.get("content"):
        lines.append("== ORIGINAL CONTENT (index copy) ==")
        lines.append(row["content"])
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))