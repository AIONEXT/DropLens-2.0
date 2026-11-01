"""End-to-end engine smoke test (no GUI): builds test files, scans, searches."""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.getcwd())

from dropLens.config import Config
from dropLens.engine.db import Catalog
from dropLens.engine.discover import human_size
from dropLens.engine.scan import ScanManager
from dropLens.engine.search import build_match, ResultCursor

BASE = tempfile.mkdtemp(prefix="droplens_test_")
root = os.path.join(BASE, "sample")
os.makedirs(os.path.join(root, "docs"), exist_ok=True)
os.makedirs(os.path.join(root, "code"), exist_ok=True)

def w(p, s):
    with open(os.path.join(root, p), "w", encoding="utf-8") as f:
        f.write(s)

w("readme.txt", "Welcome to the annual financial report. Quarterly revenue is up.\nNext year looks bright.")
w("docs/notes.md", "# Meeting notes\nDiscussed budget allocation for the new data warehouse project.")
w("docs/plan.md", "Plan: migrate everything to the cloud platform before Q4.")
w("code/app.py", 'def greet(name):\n    return f"hello {name}",  # quarterly release\n')
w("code/data.json", '{"project": "warehouse", "status": "active", "team": ["a", "b"]}')
w("data.csv", "quarter,revenue\nQ1,1200\nQ2,1500\n")
w("dupe_a.txt", "identical content payload for duplicate test")
w("dupe_b.txt", "identical content payload for duplicate test")
w("empty.log", "")

# real PDF via pymupdf (optional: the extractor degrades gracefully without it)
pdf_path = os.path.join(root, "docs/report.pdf")
try:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "DropLens quarterly financial report - warehouse budget")
    doc.save(pdf_path)
    doc.close()
except ImportError:
    with open(pdf_path, "wb") as fh:
        fh.write(b"%PDF-1.4 not a real pdf")
    print("note: pymupdf not installed - PDF extraction falls back to no_content")

# zip with contents
import zipfile
with zipfile.ZipFile(os.path.join(root, "archive.zip"), "w") as z:
    z.writestr("inner/file1.txt", "zipped notes about the warehouse")
    z.writestr("inner/file2.txt", "more compressed data")

cfg = Config()
cfg.set("ocr_enabled", False)
db = Catalog(os.path.join(BASE, "catalog.db"))
db.add_root(root)
sm = ScanManager(db, cfg, on_progress=lambda d: print("  .", d.get("kind")))
sm.scan_roots([root])
import time
deadline = time.time() + 60
while sm.is_busy() and time.time() < deadline:
    time.sleep(0.1)

assert db.schema_version >= 2, db.schema_version
assert db.execute("PRAGMA user_version").fetchone()[0] >= 2

st = db.stats()
print("STATS:", st["files"], "files /", st["folders"], "folders /", human_size(st["bytes"]),
      "/ dup_groups:", st["dup_groups"])

expr = build_match("warehouse", "content")
rows = db.search(expr)
print("SEARCH warehouse (content mode):", len(rows), "hits")
for r in rows[:5]:
    print("   -", r["name"], "|", r["category"], "| snip:", (r["snip"] or "").replace("\x01","[").replace("\x02","]")[:90])

rows_n = db.name_search("report")
print("NAME search 'report':", [r["name"] for r in rows_n])

# ---------------------------------------------------------------------------
# pagination: the result cursor serves every id exactly once (H1)
# ---------------------------------------------------------------------------
name_pool = [{"id": i} for i in range(1, 301)]   # name channel: 1..300
fts_pool = [{"id": i} for i in range(50, 350)]   # fts channel: 50..349 (overlaps)


def _fetch_name(off, lim):
    return name_pool[off:off + lim]


def _fetch_fts(off, lim):
    return fts_pool[off:off + lim]


cur = ResultCursor(page=100)
page1 = cur.seed(_fetch_name(0, 100), _fetch_fts(0, 100))
assert [r["id"] for r in page1[:3]] == [1, 2, 3], [r["id"] for r in page1[:3]]
# 100 name hits + 100 fts hits - 51 ids both channels already served
assert len(page1) == 149, len(page1)
seen = [r["id"] for r in page1]

while True:
    more = cur.next_page(_fetch_name, _fetch_fts)
    if not more:
        break
    seen.extend(r["id"] for r in more)

assert len(seen) == len(set(seen)), "a result was served twice across pages"
assert set(seen) == set(range(1, 350)), sorted(set(range(1, 350)) - set(seen))
assert cur.exhausted, "cursor should be exhausted once both channels drain"
assert cur.served == 349, cur.served

# a search mode without a name channel must never query it
name_calls = {"n": 0}

cur2 = ResultCursor(page=50)
first = cur2.seed([], [{"id": i} for i in range(1, 51)], name_enabled=False)
assert len(first) == 50
second = cur2.next_page(
    lambda o, l: name_calls.__setitem__("n", name_calls["n"] + 1) or [],
    lambda o, l: [])
assert second == [], second
assert name_calls["n"] == 0, name_calls
assert cur2.exhausted
print("PAGINATION OK — cursor serves every id once, both channels, no repeats")

# ---------------------------------------------------------------------------
# release truth: one version, declared optionals, .svgz decompressed (H9/H10)
# ---------------------------------------------------------------------------
import gzip
import pathlib as _pl
import re as _re
import tempfile as _tf

from dropLens.engine.extract import _TEXT_EXTENSIONS, extract_content

_root = _pl.Path(__file__).resolve().parent.parent
_app_version = _re.search(
    r'APP_VERSION\s*=\s*"([^"]+)"',
    (_root / "dropLens" / "__init__.py").read_text(encoding="utf-8")).group(1)
_quad = tuple(int(x) for x in _app_version.split(".")) + (0,)
_vi = (_root / "build" / "version_info.txt").read_text(encoding="utf-8")
for _key in ("filevers", "prodvers"):
    assert f"{_key}=({_quad[0]},{_quad[1]},{_quad[2]},{_quad[3]})" in _vi, \
        f"{_key} in build/version_info.txt drifted from APP_VERSION {_app_version}"
assert f"u'{_app_version}'" in _vi, f"version_info.txt not stamped with {_app_version}"

_req = (_root / "requirements.txt").read_text(encoding="utf-8")
for _dep in ("py7zr", "rarfile"):
    assert _re.search(rf"^{_dep}", _req, _re.MULTILINE), \
        f"{_dep} missing from requirements.txt while the README advertises 7Z/RAR"

assert "svgz" not in _TEXT_EXTENSIONS, "svgz must not be decoded as plain text (H9)"
_tmp = _tf.mkdtemp()
_svgz = _pl.Path(_tmp) / "schematic.svgz"
with gzip.open(_svgz, "wt", encoding="utf-8") as _fh:
    _fh.write('<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">'
              "<text>laminated widget schematic</text></svg>")
_svg_text, _svg_status = extract_content(str(_svgz))
assert _svg_status == "indexed", _svg_status
assert _svg_text and "laminated widget schematic" in _svg_text, _svg_text
_svgz.unlink()
_pl.Path(_tmp).rmdir()
print("RELEASE TRUTH OK — one version everywhere, 7Z/RAR declared, svgz gunzipped")

dups = db.duplicate_groups()
print("DUPLICATE GROUPS:", len(dups), "-> example:", dups[0]["example"] if dups else None)

# the CSV export path is exercised here too (it used to crash on every row)
from dropLens.engine.export import export_catalogue  # noqa: E402
csv_out = os.path.join(BASE, "catalogue.csv")
exported = export_catalogue(db, csv_out)
assert exported >= 8, exported
print("EXPORT:", exported, "rows ->", csv_out)

# incremental rescan should skip everything unchanged
sm.scan_roots([root])
deadline = time.time() + 60
while sm.is_busy() and time.time() < deadline:
    time.sleep(0.1)

print("OK")
shutil.rmtree(BASE, ignore_errors=True)