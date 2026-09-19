"""Dialog windows: Settings, Translate, Duplicates, About."""
from __future__ import annotations

import os
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import APP_NAME, APP_TAGLINE, APP_VERSION
from ..config import Config, ConfigStore
from ..engine.discover import human_size
from ..engine.translate import SUPPORTED_LANGS, TranslationError

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # noqa: F401
except ImportError:
    DND_FILES = "DND_FILES"


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, store: ConfigStore, on_saved):
        super().__init__(master)
        self.store = store
        self.on_saved = on_saved
        self.cfg = store.get()
        self.title(f"{APP_NAME} — Settings")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._build()
        self.grab_set()

    def _build(self):
        pad = {"padx": 10, "pady": 4}
        frm = ttk.Frame(self, padding=10)
        frm.grid(sticky="nsew")

        row = 0
        ttk.Label(frm, text="Data location", font=("", 9, "bold")).grid(row=0, column=0, sticky="w", **pad)
        self.var_data_dir = tk.StringVar(value=self.cfg.data_dir)
        e = ttk.Entry(frm, textvariable=self.var_data_dir)
        e.grid(row=1, column=0, sticky="ew", **pad)
        ttk.Button(frm, text="Change…", command=self._pick_dir).grid(row=1, column=1, sticky="ew", **pad)

        ttk.Label(frm, text="Translation", font=("", 9, "bold")).grid(row=2, column=0, sticky="w", **pad)
        langs = {v: k for k, v in SUPPORTED_LANGS.items()}
        self.var_lang = tk.StringVar(value=SUPPORTED_LANGS.get(self.cfg.default_lang, "English"))
        ttk.Label(frm, text="Default language:").grid(row=3, column=0, sticky="w", **pad)
        ttk.Combobox(frm, textvariable=self.var_lang, values=sorted(langs), state="readonly", width=24).grid(
            row=4, column=0, sticky="w", **pad)

        ttk.Label(frm, text="Performance", font=("", 9, "bold")).grid(row=5, column=0, sticky="w", **pad)
        self.var_threads = tk.IntVar(value=self.cfg.threads)
        ttk.Label(frm, text="Worker threads:").grid(row=6, column=0, sticky="w", **pad)
        ttk.Spinbox(frm, from_=1, to=16, textvariable=self.var_threads, width=8).grid(
            row=7, column=0, sticky="w", **pad)

        self.var_dup = tk.BooleanVar(value=bool(self.cfg.get("hash_duplicates", True)))
        ttk.Checkbutton(frm, text="Detect duplicate files (content hash)", variable=self.var_dup).grid(
            row=8, column=0, columnspan=2, sticky="w", **pad)

        self.var_ocr = tk.BooleanVar(value=bool(self.cfg.get("ocr_enabled", True)))
        ttk.Checkbutton(frm, text="OCR images when Tesseract is available", variable=self.var_ocr).grid(
            row=9, column=0, columnspan=2, sticky="w", **pad)
        self.var_tt = tk.StringVar(value=str(self.cfg.get("tesseract_path", "") or ""))
        ttk.Label(frm, text="Tesseract OCR path (optional):").grid(row=10, column=0, sticky="w", **pad)
        t = ttk.Entry(frm, textvariable=self.var_tt)
        t.grid(row=11, column=0, sticky="ew", **pad)
        ttk.Button(frm, text="Browse…", command=self._pick_tesseract).grid(row=11, column=1, sticky="ew", **pad)

        self.var_watch = tk.BooleanVar(value=bool(self.cfg.get("watch_enabled", False)))
        ttk.Checkbutton(frm, text="Auto-rescan when folders change", variable=self.var_watch).grid(
            row=12, column=0, columnspan=2, sticky="w", **pad)
        ttk.Label(frm, text="Watch interval (s):").grid(row=13, column=0, sticky="w", **pad)
        self.var_interval = tk.IntVar(value=int(self.cfg.watch_interval))
        ttk.Spinbox(frm, from_=5, to=600, textvariable=self.var_interval, width=8).grid(
            row=14, column=0, sticky="w", **pad)

        self.var_ignore = tk.StringVar(value=", ".join(self.cfg.ignore_names))
        ttk.Label(frm, text="Ignored names (comma separated):").grid(row=15, column=0, sticky="w", **pad)
        ttk.Entry(frm, textvariable=self.var_ignore).grid(row=16, column=0, sticky="ew", **pad)

        btns = ttk.Frame(frm)
        btns.grid(row=17, column=0, columnspan=2, sticky="e", pady=12)
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")

        frm.columnconfigure(0, weight=1)

    def _pick_dir(self):
        d = filedialog.askdirectory(title="Choose DropLens data directory", initialdir=self.var_data_dir.get())
        if d:
            self.var_data_dir.set(d)

    def _pick_tesseract(self):
        f = filedialog.askopenfilename(title="Locate tesseract.exe", filetypes=[("Tesseract", "tesseract.exe")])
        if f:
            self.var_tt.set(f)

    def _save(self):
        self.cfg.patch(
            data_dir=self.var_data_dir.get().strip() or None,
            threads=int(self.var_threads.get() or 4),
            hash_duplicates=bool(self.var_dup.get()),
            ocr_enabled=bool(self.var_ocr.get()),
            tesseract_path=self.var_tt.get().strip(),
            watch_enabled=bool(self.var_watch.get()),
            watch_interval=int(self.var_interval.get() or 15),
            default_lang=SUPPORTED_LANGS.get(self.var_lang.get(), "en"),
            ignore_names=[s.strip() for s in self.var_ignore.get().split(",") if s.strip()],
        )
        self.store.save(self.cfg)
        self.on_saved(self.cfg)
        self.destroy()


# ---------------------------------------------------------------------------
# Reflect translated text
# ---------------------------------------------------------------------------
class TranslateDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, translator, catalog, doc_id, text, default_lang="en", msg_q=None):
        super().__init__(master)
        self.translator = translator
        self.catalog = catalog
        self.doc_id = doc_id
        self.result = ""
        self.msg_q = msg_q
        self.title(f"{APP_NAME} — Translate content")
        self.geometry("760x560")
        self.transient(master)

        pad = {"padx": 8, "pady": 4}
        top = ttk.Frame(self); top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Target language:").pack(side="left")
        self.lang = ttk.Combobox(
            top, values=sorted(SUPPORTED_LANGS.values()), state="readonly", width=26,
        )
        self.lang.set(SUPPORTED_LANGS.get(default_lang, "English"))
        self.lang.pack(side="left", padx=6)
        ttk.Button(top, text="Translate", command=self.go).pack(side="left", padx=6)
        ttk.Button(top, text="Copy", command=self.copy).pack(side="left", padx=6)
        ttk.Button(top, text="Save .txt", command=self.save).pack(side="left", padx=6)

        panes = ttk.PanedWindow(self, orient="vertical")
        panes.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        orig = ttk.LabelFrame(panes, text="Original (index copy)", padding=6)
        panes.add(orig, weight=1)
        self.orig_txt = tk.Text(orig, wrap="word", height=12)
        self.orig_txt.pack(fill="both", expand=True)
        self.orig_txt.insert("1.0", text[:20000] if text else "(no extractable text)")

        tr = ttk.LabelFrame(panes, text="Translation", padding=6)
        panes.add(tr, weight=1)
        self.tr_txt = tk.Text(tr, wrap="word", state="disabled")
        self.tr_txt.pack(fill="both", expand=True)

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self, textvariable=self.status).pack(fill="x", padx=8, pady=(0, 6))

        self.grab_set()

    def go(self):
        target = None
        for code, name in SUPPORTED_LANGS.items():
            if name == self.lang.get():
                target = code
                break
        if not target:
            messagebox.showerror("DropLens", "Choose a target language.", parent=self)
            return
        text = self.orig_txt.get("1.0", "end").strip()
        if not text:
            self.status.set("No content to translate.")
            return
        self.status.set("Translating…")
        self.tr_txt.configure(state="normal")
        self.tr_txt.delete("1.0", "end")

        def work():
            try:
                res = self.translator.translate_to_db(self.doc_id, text, target)
                if self.msg_q is not None:
                    self.msg_q.put({"kind": "tr_done", "dlg": self, "text": res, "target": target})
                else:
                    self.after(0, lambda: self._done(res, target))
            except TranslationError as exc:
                if self.msg_q is not None:
                    self.msg_q.put({"kind": "tr_fail", "dlg": self, "msg": str(exc)})
                else:
                    self.after(0, lambda: self._fail(str(exc)))

        threading.Thread(target=work, daemon=True).start()

    def _done(self, res, target):
        self.result = res
        self.tr_txt.insert("1.0", res)
        self.tr_txt.configure(state="disabled")
        self.status.set(f"Translated into {target} · cached ✓")

    def _fail(self, msg):
        self.tr_txt.configure(state="normal")
        self.tr_txt.insert("1.0", "Translation unavailable. Check your internet connection.\n\n" + msg)
        self.tr_txt.configure(state="disabled")
        self.status.set("Translation failed")

    def copy(self):
        if self.result:
            self.clipboard_clear()
            self.clipboard_append(self.result)
            self.status.set("Copied to clipboard ✓")

    def save(self):
        if not self.result:
            return
        where = filedialog.asksaveasfilename(
            parent=self, defaultextension=".txt",
            filetypes=[("Text file", "*.txt")],
            initialfile="translation.txt",
        )
        if where:
            with open(where, "w", encoding="utf-8") as fh:
                fh.write(self.result)
            self.status.set(f"Saved → {where}")


# ---------------------------------------------------------------------------
# Duplicate details
# ---------------------------------------------------------------------------
class DuplicatesDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, catalog):
        super().__init__(master)
        self.catalog = catalog
        self.title("Duplicate files")
        self.geometry("640x430")
        self.transient(master)

        top = ttk.Frame(self); top.pack(fill="x", padx=8, pady=6)
        ttk.Label(top, text="Duplicate groups (identical content):").pack(side="left")
        ttk.Button(top, text="Export CSV…", command=self.export).pack(side="right")

        cols = ("Hash", "Copies", "Wasted", "Example", "hash")
        tree = ttk.Treeview(self, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols[:4], (110, 70, 90, 300)):
            tree.heading(c, text=c); tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(self, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(fill="both", expand=True, side="left", padx=(8, 0), pady=(0, 8))
        vsb.pack(fill="y", side="right", padx=(0, 8), pady=(0, 8))
        self.tree = tree
        self._load()

    def _load(self):
        for g in self.catalog.duplicate_groups():
            short = (g["hash"] or "")[:10] + "…"
            wasted = human_size(g["bytes"] * (g["n"] - 1))
            self.tree.insert("", "end", values=(short, g["n"], wasted, g["example"], g["hash"]))

    def export(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="duplicates.csv",
        )
        if not path:
            return
        import csv
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["hash_md5", "copies", "wasted_bytes", "example_path"])
            for g in self.catalog.duplicate_groups(limit=100000):
                w.writerow([g["hash"], g["n"], g["bytes"] * (g["n"] - 1), g["example"]])
        messagebox.showinfo("DropLens", "Duplicates exported.", parent=self)


# ---------------------------------------------------------------------------
# About
# ---------------------------------------------------------------------------
class AboutDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.title(f"About {APP_NAME}")
        self.resizable(False, False)
        self.transient(master)
        body = ttk.Frame(self, padding=18)
        body.pack()
        ttk.Label(body, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack()
        ttk.Label(body, text=f"Version {APP_VERSION}", foreground="#555").pack(pady=(0, 8))
        ttk.Label(body, text=APP_TAGLINE, wraplength=380, justify="center").pack()
        ttk.Label(
            body, text="Drop any file or folder. DropLens catalogues, extracts, "
                       "translates and searches hundreds and thousands of documents "
                       "without ever modifying your originals.",
            wraplength=380, justify="center", foreground="#444",
        ).pack(pady=10)
        ttk.Button(body, text="Close", command=self.destroy).pack(pady=6)
        self.grab_set()


def open_containing_folder(path: str):
    if os.path.isfile(path):
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    else:
        subprocess.Popen(["explorer", os.path.normpath(path)])


def open_path(path: str):
    try:
        if os.path.isfile(path):
            os.startfile(path)  # type: ignore[attr-defined]
        elif os.path.isdir(path):
            os.startfile(path)  # type: ignore[attr-defined]
    except OSError as exc:
        messagebox.showerror("DropLens", f"Cannot open:\n{path}\n\n{exc}")