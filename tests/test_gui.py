"""GUI smoke test: builds the main window, pumps events, exercises search UI."""
import os
import sys
import tempfile
import time

os.environ["DROPLENS_DIR"] = tempfile.mkdtemp(prefix="droplens_gui_test_")
sys.path.insert(0, os.getcwd())

from tkinterdnd2 import TkinterDnD
from dropLens.ui.mainwindow import DropLensApp

root = TkinterDnD.Tk()
root.withdraw()
app = DropLensApp(root)
root.deiconify()
root.update()
root.update_idletasks()

app.add_paths([os.path.dirname(os.path.abspath(__file__))])  # index this test folder

pumps = 0
for _ in range(400):
    root.update()
    pumps += 1
    if not app.scanner.is_busy():
        break
print("scan done after", pumps, "pumps; qsize:", app.q.qsize())
for _ in range(50):
    root.update()
    time.sleep(0.01)

print("rows in db:", app.db.execute('select count(*) as n from files').fetchone()["n"],
      "| roots:", len(app.db.list_roots()))
st = app.db.stats()
print("GUI OK — indexed files:", st["files"])

# exercise search via UI state wiring
app.term_var.set("engine")
app.mode_var.set("Content")
app.cat_var.set("Code")
try:
    app.do_search()
    root.update()
    n = len(app.results_tree.get_children())
    print("search results:", n)
except Exception as exc:
    import traceback; print("SEARCH FAIL", exc); traceback.print_exc()

# exercise preview on first result
kids = app.results_tree.get_children()
if kids:
    app.results_tree.selection_set(kids[0])
    root.update()

app._on_close()
print("GUI CLOSED OK")