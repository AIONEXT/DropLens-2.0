"""Main window: drop zone, library, search, duplicates and activity log."""
from __future__ import annotations

import os
import queue
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import APP_NAME, APP_TAGLINE, APP_VERSION
from ..config import Config, ConfigStore
from ..engine.categorize import all_categories, category_label
from ..engine.db import Catalog
from ..engine.discover import human_size
from ..engine.export import export_catalogue, export_search_results
from ..engine.scan import ScanManager
from ..engine.search import build_match
from ..engine.translate import Translator
from ..engine.watch import FolderWatcher
from .dialogs import (
    AboutDialog,
    DuplicatesDialog,
    SettingsDialog,
    TranslateDialog,
    open_containing_folder,
    open_path,
)

try:
    from tkinterdnd2 import DND_FILES
except ImportError:  # pragma: no cover
    DND_FILES = "DND_FILES"

HINT = (
    "DRAG AND DROP — drop any file or folder here, or click a button above.\n"
    "DropLens scans documents, PDFs, Office files, code, images (OCR), archives & more —\n"
    "then makes everything instantly searchable — without ever changing your originals."
)


class DropLensApp:
    def __init__(self, master: tk.Misc):
        self.master = master
        self.master.title(f"{APP_NAME}  ·  {APP_TAGLINE}")
        self.master.geometry("1240x760")
        self.master.minsize(980, 620)

        self.ready = False
        self.cfg_store = ConfigStore()
        self.cfg: Config = self.cfg_store.get()
        self.db_path = self._resolve_db_path(self.cfg)
        self.db = Catalog(self.db_path)
        self.scanner = ScanManager(self.db, self.cfg, on_progress=self._enqueue)
        self.translator = Translator(self.db)
        self.watcher: FolderWatcher | None = None
        self.q: "queue.Queue[dict]" = queue.Queue()
        self._results_meta = {"file_ids": [], "offset": 0}
        self._results_items: dict[str, int] = {}

        self._build_menu()
        self._build_layout()
        self._bind_drops()

        self.refresh_roots()
        self.refresh_stats()
        self.refresh_duplicates()
        self.results_empty_state()

        self.master.after(80, self._poll)
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)
        if self.cfg.get("watch_enabled"):
            self._start_watcher()
        self.ready = True

    @staticmethod
    def _resolve_db_path(cfg) -> str:
        from .. import resources
        custom = cfg.get("data_dir")
        return os.path.join(custom, "catalog.db") if custom else resources.db_path()

    def _reopen_db(self) -> None:
        self._stop_watcher()
        try:
            self.db.close()
        except Exception:
            pass
        self.db_path = self._resolve_db_path(self.cfg)
        self.db = Catalog(self.db_path)
        self.scanner = ScanManager(self.db, self.cfg, on_progress=self._enqueue)
        self.translator = Translator(self.db)
        self.refresh_roots()
        self.refresh_stats()
        self.refresh_duplicates()
        self.results_empty_state()

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _build_menu(self):
        m = tk.Menu(self.master)
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="Add Folders…", accelerator="Ctrl+O", command=self.add_folders)
        fm.add_command(label="Add Files…", command=self.add_files)
        fm.add_separator()
        fm.add_command(label="Rescan All", command=self.rescan_all)
        fm.add_command(label="Full Rebuild (re-extract everything)", command=self.full_rebuild)
        fm.add_separator()
        fm.add_command(label="Export Catalogue (CSV)…", command=self.export_catalogue_csv)
        fm.add_separator()
        fm.add_command(label="Exit", command=self._on_close)
        m.add_cascade(label="File", menu=fm)

        wm = tk.Menu(m, tearoff=0)
        wm.add_command(label="Settings…", command=self.open_settings)
        m.add_cascade(label="Options", menu=wm)

        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="About", command=lambda: AboutDialog(self.master))
        m.add_cascade(label="Help", menu=hm)
        self.master.config(menu=m)

        self.master.bind("<Control-o>", lambda e: self.add_folders())

    def _build_layout(self):
        # -- toolbar -----------------------------------------------------
        bar = ttk.Frame(self.master, padding=(8, 6))
        bar.pack(fill="x")
        ttk.Button(bar, text="+  Add Folders", command=self.add_folders).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="+  Add Files", command=self.add_files).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Scan Now", command=self.rescan_all).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Stop", command=self.stop_scan).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        self.auto_watch_var = tk.BooleanVar(value=bool(self.cfg.get("watch_enabled", False)))
        ttk.Checkbutton(
            bar, text="Auto-watch", variable=self.auto_watch_var, command=self._toggle_watch,
        ).pack(side="left")
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Button(bar, text="Settings…", command=self.open_settings).pack(side="left")
        ttk.Label(bar, text=f"  v{APP_VERSION}", foreground="#888").pack(side="right")

        # -- notebook ------------------------------------------------------
        nb = ttk.Notebook(self.master)
        nb.pack(fill="both", expand=True, padx=8, pady=(0, 2))
        self.nb = nb
        self.tab_library = ttk.Frame(nb);   nb.add(self.tab_library, text="  Library  ")
        self.tab_search = ttk.Frame(nb);    nb.add(self.tab_search, text="  Search  ")
        self.tab_dups = ttk.Frame(nb);      nb.add(self.tab_dups, text="  Duplicates  ")
        self.tab_activity = ttk.Frame(nb);  nb.add(self.tab_activity, text="  Activity  ")

        self._build_library_tab()
        self._build_search_tab()
        self._build_duplicates_tab()
        self._build_activity_tab()

        # -- status bar ------------------------------------------------------
        status = ttk.Frame(self.master, padding=(8, 4))
        status.pack(fill="x")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=200)
        self.progress.pack(side="left")
        self.var_status = tk.StringVar(value="Ready. Drop files or folders to begin.")
        ttk.Label(status, textvariable=self.var_status, anchor="w").pack(
            side="left", fill="x", expand=True, padx=10)
        self.var_stats = tk.StringVar(value="")
        ttk.Label(status, textvariable=self.var_stats, anchor="e", foreground="#555").pack(side="right")

    def _build_library_tab(self):
        t = self.tab_library
        # drop panel
        self.drop = tk.Label(
            t, text=HINT, justify="center", font=("Segoe UI", 11),
            bg="#eef4fb", fg="#2b4a6f", relief="groove", bd=2,
            highlightthickness=4, highlightbackground="#cfe0f0",
        )
        self.drop.pack(fill="x", padx=8, pady=8, ipady=18)
        self.drop.bind("<Enter>", lambda e: self.drop.configure(bg="#e0eefb", highlightbackground="#7aa5d6"))
        self.drop.bind("<Leave>", lambda e: self.drop.configure(bg="#eef4fb", highlightbackground="#cfe0f0"))

        # roots area
        mid = ttk.Frame(t); mid.pack(fill="both", expand=True, padx=8, pady=4)
        ttk.Label(mid, text="Monitored folders", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        cols = ("Folder", "Files", "Folders", "Last scan", "Status")
        tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, (360, 90, 80, 150, 110)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w" if c == "Folder" else "center")
        vsb = ttk.Scrollbar(mid, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y", padx=(4, 0))
        self.root_tree = tree

        menu = tk.Menu(tree, tearoff=0)
        menu.add_command(label="Rescan", command=self._menu_rescan)
        menu.add_command(label="Full rebuild (re-extract)", command=self._menu_full_rebuild)
        menu.add_command(label="Open in Explorer", command=self._menu_open_explorer)
        menu.add_separator()
        menu.add_command(label="Remove from Library", command=self._menu_remove_root)
        tree.bind("<Button-3>", lambda e: self._root_menu(e, menu))
        tree.bind("<Double-1>", lambda e: self._root_double_click())

        # quick actions under roots
        q = ttk.Frame(t); q.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(q, text="Current file:").pack(side="left")
        self.var_current = tk.StringVar(value="—")
        ttk.Label(q, textvariable=self.var_current, anchor="w", foreground="#555", width=70).pack(
            side="left", fill="x", expand=True)

    def _build_search_tab(self):
        t = self.tab_search
        row = ttk.Frame(t); row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="Search:").pack(side="left")
        self.term_var = tk.StringVar()
        self.term_entry = ttk.Entry(row, textvariable=self.term_var, width=46)
        self.term_entry.pack(side="left", padx=6)
        self.term_entry.bind("<Return>", lambda e: self.do_search())
        self.term_var.trace_add("write", self._on_term_changed)

        self.mode_var = tk.StringVar(value="All")
        ttk.Combobox(
            row, textvariable=self.mode_var, state="readonly", width=10,
            values=("All", "Name", "Path", "Content"),
        ).pack(side="left", padx=(4, 6))
        self.cat_var = tk.StringVar(value="All types")
        cats = ["All types"] + [category_label(c) for c in all_categories()]
        ttk.Combobox(row, textvariable=self.cat_var, state="readonly", width=16, values=cats).pack(
            side="left", padx=(0, 6))
        ttk.Button(row, text="Search", command=self.do_search).pack(side="left", padx=(0, 4))
        ttk.Button(row, text="Clear", command=self.clear_search).pack(side="left")

        # results + preview
        paned = ttk.PanedWindow(t, orient="vertical")
        paned.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        top = ttk.Frame(paned)
        paned.add(top, weight=3)
        self.results_label = tk.StringVar(value="Results")
        ttk.Label(top, textvariable=self.results_label).pack(anchor="w")
        cols = ("Name", "Category", "Size", "Modified", "Relevance", "Path")
        tree = ttk.Treeview(top, columns=cols, show="headings", selectmode="extended")
        widths = (240, 100, 80, 140, 70, 460)
        for c, w in zip(cols, widths):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w", stretch=(c == "Path"))
        vsb = ttk.Scrollbar(top, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y", padx=(4, 0))
        tree.bind("<<TreeviewSelect>>", self._on_result_select)
        tree.bind("<Double-1>", lambda e: self._open_selected())
        self.results_tree = tree

        btnrow = ttk.Frame(top); btnrow.pack(fill="x")
        ttk.Button(btnrow, text="Open", command=self._open_selected).pack(side="left", padx=(0, 4))
        ttk.Button(btnrow, text="Open Folder", command=self._reveal_selected).pack(side="left", padx=(0, 4))
        ttk.Button(btnrow, text="Translate…", command=self._translate_selected).pack(side="left", padx=(0, 4))
        ttk.Button(btnrow, text="Copy Path", command=self._copy_path).pack(side="left", padx=(0, 4))
        ttk.Button(btnrow, text="Export Results…", command=self._export_results).pack(side="left")
        ttk.Button(btnrow, text="Load more", command=self._load_more).pack(side="right")

        bottom = ttk.Frame(paned)
        paned.add(bottom, weight=2)
        self.preview = tk.Text(bottom, wrap="word", state="disabled", bg="#fbfcfe")
        pvsb = ttk.Scrollbar(bottom, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=pvsb.set)
        self.preview.pack(side="left", fill="both", expand=True)
        pvsb.pack(side="right", fill="y")
        self.preview.tag_configure("hl", background="#fff3a0", foreground="#000")
        self.preview.tag_configure("head", font=("Segoe UI", 9, "bold"), foreground="#1a4a78")
        self.preview.tag_configure("dim", foreground="#777")

    def _build_duplicates_tab(self):
        t = self.tab_dups
        row = ttk.Frame(t); row.pack(fill="x", padx=8, pady=8)
        ttk.Label(row, text="Duplicate groups use the same bytes on disk — candidates for cleanup.").pack(side="left")
        ttk.Button(row, text="Refresh", command=self.refresh_duplicates).pack(side="right")
        ttk.Button(row, text="Details…", command=lambda: self._dup_detail()).pack(side="right", padx=6)
        cols = ("Hash", "Copies", "Wasted", "Example path")
        tree = ttk.Treeview(t, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (130, 80, 100, 520)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(t, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=(0, 8))
        vsb.pack(side="right", fill="y", padx=(0, 8), pady=(0, 8))
        tree.bind("<Double-1>", lambda e: self._dup_detail())
        self.dup_tree = tree

    def _build_activity_tab(self):
        t = self.tab_activity
        self.log = tk.Text(t, wrap="word", bg="#101418", fg="#d8e2ec", state="disabled")
        lv = ttk.Scrollbar(t, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lv.set)
        self.log.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        lv.pack(side="right", fill="y", padx=(0, 8), pady=8)
        self.log.tag_configure("info", foreground="#c3d8f0")
        self.log.tag_configure("warning", foreground="#ffd27a")
        self.log.tag_configure("error", foreground="#ff8080")
        self._log("info", f"{APP_NAME} started. Drop files/folders to index them.")

    def _bind_drops(self):
        try:
            for w in (self.master, self.drop):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop_formatted)
                w.dnd_bind("<<DropEnter>>", lambda e: self.drop.configure(bg="#d7e9fb", highlightbackground="#3f7ec0"))
                w.dnd_bind("<<DropLeave>>", lambda e: self.drop.configure(bg="#eef4fb", highlightbackground="#cfe0f0"))
        except Exception:
            pass

    def _on_drop_formatted(self, event):
        try:
            paths = self.master.tk.splitlist(event.data)
        except Exception:
            paths = event.data.split("} ")
        self.drop.configure(bg="#eef4fb", highlightbackground="#cfe0f0")
        self.add_paths(list(paths))

    # ------------------------------------------------------------------
    # engine <-> UI bridge
    # ------------------------------------------------------------------
    def _enqueue(self, msg: dict) -> None:
        """Thread-safe: worker threads only enqueue; the main-thread poller
        drains the queue. Never touch Tcl/widgets from here."""
        self.q.put(msg)

    def _poll(self):
        drained = False
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            drained = True
            self._handle(msg)
        if drained:
            pass
        self.master.after(80, self._poll)

    def _handle(self, msg: dict):
        kind = msg.get("kind")
        if kind == "state":
            phase = msg.get("phase")
            if phase == "scanning":
                if not self.scanner.is_busy():
                    self.progress.start(12)
                self.var_status.set(f"Scanning…")
            else:
                self.progress.stop()
                self.var_status.set("Ready")
        elif kind == "file":
            self.var_current.set(os.path.basename(msg.get("path", "")) + f"  ·  {msg.get('count', 0):,} entries")
            self.var_status.set(f"Scanning … {msg.get('count', 0):,} entries")
        elif kind == "done":
            res = msg.get("result")
            if res:
                self.var_current.set("")
                self._log(
                    "info",
                    f"✓ {res.root}: {res.indexed:,} indexed · {res.skipped:,} unchanged · "
                    f"{res.deleted} removed · {res.errors} errors · {res.duration:.1f}s",
                )
            self.refresh_roots()
            self.refresh_stats()
            self.refresh_duplicates()
        elif kind == "log":
            self._log(msg.get("level", "info"), msg.get("msg", ""))
        elif kind == "root_stats":
            self.refresh_roots()
        elif kind == "watch_changed":
            self._on_watched_changed(msg.get("root", ""))
        elif kind == "tr_done":
            dlg = msg.get("dlg")
            if dlg and dlg.winfo_exists():
                dlg._done(msg.get("text", ""), msg.get("target", ""))
        elif kind == "tr_fail":
            dlg = msg.get("dlg")
            if dlg and dlg.winfo_exists():
                dlg._fail(msg.get("msg", "Translation failed."))

    def _log(self, level: str, text: str):
        tag = level if level in ("warning", "error") else "info"
        self.log.configure(state="normal")
        stamp = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{stamp}] {text}\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------
    # library actions
    # ------------------------------------------------------------------
    def add_folders(self) -> None:
        chosen = filedialog.askdirectory(title="Add folder(s) to the library")
        if not chosen:
            return
        self.add_paths([chosen])

    def add_files(self) -> None:
        chosen = filedialog.askopenfilenames(title="Add files")
        if not chosen:
            return
        self.add_paths(list(chosen))

    def add_paths(self, paths: list[str]) -> None:
        roots_to_scan: list[str] = []
        for p in paths:
            p = os.path.normpath(p)
            if not os.path.exists(p):
                self._log("warning", f"Skipped (not found): {p}")
                continue
            if os.path.isdir(p):
                if p.lower() in [r["path"].lower() for r in self.db.list_roots()]:
                    roots_to_scan.append(p)
                else:
                    self.catalog_add_root(p)
                    roots_to_scan.append(p)
            else:
                parent = os.path.dirname(p) or os.getcwd()
                existing = [r["path"].lower() for r in self.db.list_roots()]
                if parent.lower() not in existing:
                    self.catalog_add_root(parent)
                    self._log("info", f"Added containing folder: {parent}")
                roots_to_scan.append(parent)
        if roots_to_scan:
            self.refresh_roots()
            self.scanner.scan_roots(roots_to_scan)
        self.nb.select(self.tab_library)

    def catalog_add_root(self, p: str) -> None:
        self.db.add_root(p)
        self._log("info", f"Added to library: {p}")

    def _selected_roots(self) -> list[str]:
        items = self.root_tree.selection()
        if not items:
            return []
        by_path = {str(r["path"]): str(r["path"]) for r in self.db.list_roots()}
        out = []
        for iid in items:
            vals = self.root_tree.item(iid, "values")
            if not vals:
                continue
            out.append(str(vals[0]))
        return [p for p in out if p]

    def _roots_all(self) -> list[str]:
        return [str(r["path"]) for r in self.db.list_roots()]

    def rescan_all(self) -> None:
        roots = self._roots_all()
        if not roots:
            messagebox.showinfo(APP_NAME, "Add folders or files first — then drop them in, or use Add Folders.")
            return
        self.scanner.scan_roots(roots, incremental=True)

    def full_rebuild(self) -> None:
        roots = self._selected_roots() or self._roots_all()
        if not roots:
            messagebox.showinfo(APP_NAME, "Nothing to rebuild.")
            return
        if not messagebox.askyesno(APP_NAME, "Full rebuild re-extracts every file in:\n\n" + "\n".join(roots) +
                                           "\n\nThis can take a while on large folders. Continue?"):
            return
        self.scanner.scan_roots(roots, incremental=False)

    def stop_scan(self) -> None:
        if self.scanner.is_busy():
            self.scanner.cancel()
            self.var_status.set("Stopping…")
        else:
            self.var_status.set("No scan running.")

    def _menu_rescan(self) -> None:
        roots = self._selected_roots()
        if roots:
            self.scanner.scan_roots(roots, incremental=True)

    def _menu_full_rebuild(self) -> None:
        roots = self._selected_roots()
        if roots:
            self.scanner.scan_roots(roots, incremental=False)

    def _menu_remove_root(self) -> None:
        roots = self._selected_roots()
        if not roots:
            return
        if not messagebox.askyesno(APP_NAME, "Remove from library and delete the local index of:\n\n" + "\n".join(roots) +
                                           "?\n\n(Your original files are never touched.)"):
            return
        for r in roots:
            self.db.remove_root(r)
            if self.watcher:
                self.watcher.forget(r)
        self.refresh_roots()
        self.refresh_stats()
        self.refresh_duplicates()

    def _menu_open_explorer(self) -> None:
        roots = self._selected_roots()
        for r in roots:
            open_containing_folder(r)

    def _root_double_click(self) -> None:
        roots = self._selected_roots()
        if roots:
            open_containing_folder(roots[0])

    def _root_menu(self, event, menu):
        try:
            item = self.root_tree.identify_row(event.y)
            if item:
                self.root_tree.selection_set(item)
                menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def refresh_roots(self) -> None:
        for iid in self.root_tree.get_children():
            self.root_tree.delete(iid)
        for r in self.db.list_roots():
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_scan"])) if r["last_scan"] else "never"
            status = r["status"]
            self.root_tree.insert(
                "", "end", iid=str(r["path"]),
                values=(r["path"], f"{r['files'] - r['folders']:,}", f"{r['folders']:,}", last, status),
            )

    def refresh_stats(self) -> None:
        s = self.db.stats()
        dups = f"  ·  ⧉ {s['dup_groups']:,} duplicate groups ({s['dup_files']:,} files)" if s["dup_groups"] else ""
        self.var_stats.set(
            f"{s['files']:,} files · {s['folders']:,} folders · {human_size(s['bytes'])}{dups}"
        )

    def refresh_duplicates(self) -> None:
        tree = self.dup_tree
        for iid in tree.get_children():
            tree.delete(iid)
        for g in self.db.duplicate_groups():
            wasted = human_size(g["bytes"] * (g["n"] - 1))
            tree.insert("", "end", values=((g["hash"] or "")[:10] + "…", g["n"], wasted, g["example"]))

    def _dup_detail(self):
        sel = self.dup_tree.selection()
        if not sel:
            return
        DuplicatesDialog(self.master, self.db)

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def _on_term_changed(self, *_):
        if len(self.term_var.get().strip()) >= 2:
            self.master.after(350, self.do_search_silent)

    def do_search(self) -> None:
        self._run_search(reset=True)

    def do_search_silent(self) -> None:
        self._run_search(reset=True)

    def _category(self) -> str:
        label = self.cat_var.get()
        if label == "All types":
            return ""
        for c in all_categories():
            if category_label(c) == label:
                return c
        return ""

    def _mode(self) -> str:
        return {"All": "all", "Name": "name", "Path": "path", "Content": "content"}.get(self.mode_var.get(), "all")

    def _run_search(self, reset: bool) -> None:
        term = self.term_var.get().strip()
        mode = self._mode()
        category = self._category()
        if not term:
            self.results_empty_state()
            return
        expr = build_match(term, mode)
        if expr is None:
            self.results_empty_state("Type at least one character to search.")
            return
        # fall back to LIKE-based name matching when FTS column filter yields nothing is
        # unnecessary: FTS already indexes name/path/content.
        try:
            self._do_fts_search(expr, term, mode, category, reset=reset)
        except Exception as exc:
            self._log("error", f"Search failed: {exc}")

    def _do_fts_search(self, expr, term, mode, category, reset=True, limit_page=300):
        tree = self.results_tree
        if reset:
            for iid in tree.get_children():
                tree.delete(iid)
            self._results_meta = {"offset": 0, "expr": expr, "term": term, "mode": mode,
                                  "category": category, "total": 0}
            name_results = self.db.name_search(
                term, category=category, limit=limit_page, offset=0,
            ) if mode in ("all", "name") else []
            fts_results = self.db.search(expr, category=category, limit=limit_page, offset=0)
            merged = self._merge_results(name_results, fts_results)
            self._results_meta["total"] = len(merged)
            self._results_meta["offset"] = len(merged) - 1
            self._fill(merged)
        else:
            offset = self._results_meta.get("offset", 0) + 1
            more = self.db.search(expr, category=category, limit=limit_page, offset=offset)
            self._results_meta["offset"] += len(more)
            self._fill(more)

    def _merge_results(self, name_results, fts_results):
        seen = set()
        out = []
        for r in list(name_results) + list(fts_results):
            iid = int(r["id"])
            if iid in seen:
                continue
            seen.add(iid)
            out.append(r)
        return out

    def _fill(self, rows) -> None:
        tree = self.results_tree
        for r in rows:
            iid = str(r["id"])
            self._results_items[iid] = int(r["id"])
            count = len(tree.get_children())
            if count >= 5000:
                break
            tree.insert(
                "", "end", iid=iid,
                values=(
                    r["name"], category_label(r["category"]), human_size(r["size"]),
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"])),
                    self._rank_display(float(r["rank"] or 0)) if r["rank"] else "",
                    r["path"],
                ),
            )
        total = self._results_meta.get("total", 0)
        self.results_label.set(
            f"{len(tree.get_children()):,} result{'s' if len(tree.get_children()) != 1 else ''} "
            f"for “{self.term_var.get()}”  ·  {self._mode()} mode")
        if len(tree.get_children()) >= 300:
            self.results_label.set(self.results_label.get() + "  (use Load more for additional matches)")

    @staticmethod
    def _rank_display(rank: float) -> str:
        if rank > 0:
            return "·"
        return ""

    def results_empty_state(self, msg: str | None = None) -> None:
        tree = self.results_tree
        for iid in tree.get_children():
            tree.delete(iid)
        self._results_meta = {"offset": 0}
        self._results_items.clear()
        if msg:
            self.results_label.set(msg)
            return
        recent = self.db.recent_files(150)
        for r in recent:
            iid = str(r["id"])
            self._results_items[iid] = int(r["id"])
            tree.insert(
                "", "end", iid=iid,
                values=(r["name"], category_label(r["category"]), human_size(r["size"]),
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"])), "", r["path"]),
            )
        self.results_label.set(f"{len(recent):,} recently indexed files — type a query above to search everything.")

    def _load_more(self) -> None:
        meta = self._results_meta
        if not meta.get("expr"):
            return
        self._do_fts_search(meta["expr"], meta["term"], meta["mode"], meta["category"], reset=False)

    def clear_search(self) -> None:
        self.term_var.set("")
        self.results_empty_state()
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.configure(state="disabled")

    # ------------------------------------------------------------------
    # result interactions
    # ------------------------------------------------------------------
    def _selected_ids(self) -> list[int]:
        ids = []
        for iid in self.results_tree.selection():
            fid = self._results_items.get(iid)
            if fid:
                ids.append(fid)
        return ids

    def _on_result_select(self, event=None) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        self._show_preview(ids[0])

    def _show_preview(self, doc_id: int) -> None:
        row = self.db.get(doc_id)
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        if not row:
            self.preview.configure(state="disabled")
            return
        terms: list[str] = []
        if self.term_var.get().strip():
            import re as _re
            terms = _re.findall(r"[\w@.\-\u0080-\uffff]+", self.term_var.get().strip())
        header = (
            f"{row['path']}\n"
            f"{category_label(row['category'])}  ·  {human_size(row['size'])}  ·  "
            f"modified {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(row['mtime']))}"
            f"{'  ·  DUPLICATE' if row['dup_id'] else ''}\n"
        )
        self.preview.insert("end", header, "head")
        self.preview.insert("end", "—" * 90 + "\n", "dim")
        content = (row["content"] or "").strip()
        if not content:
            if row["status"] == "no_content" or row["status"] == "ocr_unavailable":
                self.preview.insert("end", "This file type has no extractable text (indexed by name/size only).", "dim")
                if row["status"] == "ocr_unavailable":
                    self.preview.insert("end", "\n\nTip: installing Tesseract OCR enables image text extraction.", "dim")
                self.preview.insert("end", row["path"].rstrip(), "")
            else:
                self.preview.insert("end", "(folder — see Library tab)\n", "dim")
                listing = row["content"] or ""
                if listing:
                    self.preview.insert("end", listing)
        else:
            shown = content[: int(self.cfg.get("preview_chars", 20000))]
            self.preview.insert("end", shown)
            self._apply_highlight(terms)
            if len(content) > len(shown):
                self.preview.insert("end", "\n… preview truncated")
        self.preview.configure(state="disabled")

    def _apply_highlight(self, terms) -> None:
        for t in terms:
            if not t:
                continue
            idx = "1.0"
            while True:
                idx = self.preview.search(t, idx, stopindex="end", nocase=True)
                if not idx:
                    break
                end = self.preview.index(f"{idx}+{len(t)}c")
                self.preview.tag_add("hl", idx, end)
                idx = end

    def _open_selected(self) -> None:
        for fid in self._selected_ids():
            row = self.db.get(fid)
            if row:
                open_path(row["path"])

    def _reveal_selected(self) -> None:
        for fid in self._selected_ids():
            row = self.db.get(fid)
            if row:
                open_containing_folder(row["path"])

    def _copy_path(self) -> None:
        paths = []
        for fid in self._selected_ids():
            row = self.db.get(fid)
            if row:
                paths.append(row["path"])
        if paths:
            self.master.clipboard_clear()
            self.master.clipboard_append("\n".join(paths))

    def _translate_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        fid = ids[0]
        row = self.db.get(fid)
        if not row or not row.get("content"):
            messagebox.showinfo(APP_NAME, "This item has no extractable text to translate.")
            return
        TranslateDialog(
            self.master, self.translator, self.db, fid, row["content"],
            default_lang=self.cfg.default_lang, msg_q=self.q,
        )

    def _export_results(self) -> None:
        ids = self._selected_ids() or list(self._results_items.values())
        if not ids:
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="droplens-search-results.csv")
        if not path:
            return
        n = export_search_results(self.db, ids, path)
        self._log("info", f"Exported {n:,} results → {path}")
        self.var_status.set(f"Exported {n:,} results ✓")

    # ------------------------------------------------------------------
    # watch + settings
    # ------------------------------------------------------------------
    def _toggle_watch(self) -> None:
        enabled = self.auto_watch_var.get()
        self.cfg.patch(watch_enabled=enabled)
        self.cfg_store.save(self.cfg)
        if enabled:
            self._start_watcher()
            self.var_status.set("Auto-watch enabled.")
        else:
            self._stop_watcher()
            self.var_status.set("Auto-watch disabled.")

    def _start_watcher(self) -> None:
        if self.watcher and self.watcher.is_alive():
            return
        self.watcher = FolderWatcher(
            self.db, self.cfg, on_changed=lambda root: self._enqueue({"kind": "watch_changed", "root": root}),
        )
        self.watcher.start()
        self._log("info", "Auto-watch started.")

    def _stop_watcher(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None

    def _on_watched_changed(self, root: str) -> None:
        if self.scanner.is_busy():
            return
        self._log("info", f"Change detected in {root} — rescanning.")
        self.scanner.scan_roots([root])

    def open_settings(self) -> None:
        def on_saved(cfg):
            self.cfg = cfg
            if self._resolve_db_path(cfg) != self.db_path:
                self._reopen_db()
                self._log("info", "Data directory changed — library reopened from the new location.")
            self.scanner.config = cfg
            if cfg.get("watch_enabled") != bool(self.auto_watch_var.get()):
                self.auto_watch_var.set(bool(cfg.get("watch_enabled")))
            if cfg.get("watch_enabled"):
                self._start_watcher()
            else:
                self._stop_watcher()
            self.refresh_stats()
            self._log("info", "Settings saved.")

        SettingsDialog(self.master, self.cfg_store, on_saved)

    def export_catalogue_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")], initialfile="droplens-catalogue.csv")
        if not path:
            return
        n = export_catalogue(self.db, path)
        self._log("info", f"Exported catalogue ({n:,} files) → {path}")
        self.var_status.set(f"Exported {n:,} files ✓")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        self._stop_watcher()
        try:
            self.db.close()
        finally:
            self.master.destroy()