"""Regression test for the CSV/TXT export crash.

``sqlite3.Row`` has no ``.get()``; the export helpers used it on every row, so
both export paths raised AttributeError before writing a single line. This test
exports a real one-row catalogue and parses the result back.
"""
import csv
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.getcwd())

from dropLens.engine.db import Catalog, content_hash, row_get  # noqa: E402
from dropLens.engine.export import export_catalogue, export_search_results, export_text  # noqa: E402

BASE = tempfile.mkdtemp(prefix="droplens_export_test_")
db = Catalog(os.path.join(BASE, "catalog.db"))
root = os.path.join(BASE, "files")
os.makedirs(root, exist_ok=True)

# a real file so preview/open paths are realistic
target = os.path.join(root, "report.pdf")
with open(target, "wb") as fh:
    fh.write(b"%PDF-1.4 fake")

db.add_root(root)
fid = db.upsert_file({
    "path": target, "name": "report.pdf", "ext": "pdf", "size": 12, "mtime": 1700000000.0,
    "ctime": 1700000000.0, "root": root, "hash": "abc123", "category": "document",
    "kind": "file", "status": "indexed", "dup_id": "abc123", "scanned_at": 1700000100.0,
})
db.commit()
db.save_doc(fid, "report.pdf", target, "quarterly warehouse budget report\nline two")

out_csv = os.path.join(BASE, "catalogue.csv")
count = export_catalogue(db, out_csv)
assert count == 1, count
with open(out_csv, newline="", encoding="utf-8-sig") as fh:
    rows = list(csv.DictReader(fh))
assert len(rows) == 1, rows
row = rows[0]
assert row["path"] == target
assert row["name"] == "report.pdf"
assert row["hash_md5"] == "abc123"
assert "warehouse budget" in row["content_preview"], row["content_preview"]
assert row["size_human"].endswith("B")

# search-result export (same helper, different query path)
out2 = os.path.join(BASE, "results.csv")
assert export_search_results(db, [fid], out2) == 1
assert os.path.getsize(out2) > 0

# empty selection must still produce a header-only file, not a crash
out3 = os.path.join(BASE, "empty.csv")
assert export_search_results(db, [], out3) == 0

# plain-text export reads row["content"] through the same helper
out_txt = os.path.join(BASE, "doc.txt")
export_text(db, fid, out_txt)
with open(out_txt, encoding="utf-8") as fh:
    body = fh.read()
assert "quarterly warehouse budget" in body

# row_get semantics: rows, dicts, missing keys and NULL columns
assert row_get(rows and db.get(fid), "name") == "report.pdf"
assert row_get({"a": 1}, "a") == 1
assert row_get({"a": 1}, "b", "fallback") == "fallback"
assert row_get(None, "a", 7) == 7
assert row_get(db.get_meta(fid), "summary", "") == ""  # NULL column -> default
assert content_hash("x") == content_hash("x")
assert content_hash("x") != content_hash("y")

# cascade: deleting a document removes its derived rows (no orphans)
db.upsert_meta(fid, summary="s", tags="a,b", embedding=b"\x00" * 8)
db.execute("INSERT INTO translations(doc_id, lang, text, ts) VALUES(?,?,?,?)",
           (fid, "de", "übersetzt", 0.0))
db.commit()
assert db.get_meta(fid) is not None
db.mark_deleted([fid])
db.commit()
assert db.get_meta(fid) is None, "docmeta orphaned after delete"
assert db.execute("SELECT COUNT(*) AS n FROM translations WHERE doc_id=?", (fid,)).fetchone()["n"] == 0
assert db.execute("SELECT COUNT(*) AS n FROM docs WHERE doc_id=?", (fid,)).fetchone()["n"] == 0

print("EXPORT OK — catalogue, results, text export and cascade delete")
db.close()
shutil.rmtree(BASE, ignore_errors=True)
