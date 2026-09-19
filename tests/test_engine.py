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
from dropLens.engine.search import build_match

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

# real PDF via pymupdf
pdf_path = os.path.join(root, "docs/report.pdf")
import pymupdf
doc = pymupdf.open()
page = doc.new_page()
page.insert_text((72, 72), "DropLens quarterly financial report - warehouse budget")
doc.save(pdf_path); doc.close()

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

dups = db.duplicate_groups()
print("DUPLICATE GROUPS:", len(dups), "-> example:", dups[0]["example"] if dups else None)

# incremental rescan should skip everything unchanged
sm.scan_roots([root])
deadline = time.time() + 60
while sm.is_busy() and time.time() < deadline:
    time.sleep(0.1)

print("OK")
shutil.rmtree(BASE, ignore_errors=True)