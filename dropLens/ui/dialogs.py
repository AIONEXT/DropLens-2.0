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

        ttk.Label(frm, text="Notifications & UX", font=("", 9, "bold")).grid(row=17, column=0, sticky="w", **pad)
        self.var_tray = tk.BooleanVar(value=bool(self.cfg.get("tray_enabled", True)))
        ttk.Checkbutton(frm, text="Show system tray icon (keep running silently)", variable=self.var_tray).grid(
            row=18, column=0, columnspan=2, sticky="w", **pad)
        self.var_close_tray = tk.BooleanVar(value=bool(self.cfg.get("close_to_tray", False)))
        ttk.Checkbutton(frm, text="Close window to tray instead of exiting", variable=self.var_close_tray).grid(
            row=19, column=0, columnspan=2, sticky="w", **pad)
        self.var_notify = tk.BooleanVar(value=bool(self.cfg.get("notify_scan_done", True)))
        ttk.Checkbutton(frm, text="Tray notification after each scan", variable=self.var_notify).grid(
            row=20, column=0, columnspan=2, sticky="w", **pad)
        self.var_autoai = tk.BooleanVar(value=bool(self.cfg.get("ai_auto_enrich", False)))
        ttk.Checkbutton(frm, text="Run AI enrichment automatically after scans", variable=self.var_autoai).grid(
            row=21, column=0, columnspan=2, sticky="w", **pad)

        btnrow = ttk.Frame(frm)
        btnrow.grid(row=22, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Button(btnrow, text="AI Models…", command=self._open_ai, style="Ghost.TButton").pack(side="left")

        btns = ttk.Frame(frm)
        btns.grid(row=23, column=0, columnspan=2, sticky="e", pady=4)
        ttk.Button(btns, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left")

        frm.columnconfigure(0, weight=1)

    def _open_ai(self):
        def saved():
            self.store.save(self.cfg)
        AISettingsDialog(self, self.cfg, saved)

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
            tray_enabled=bool(self.var_tray.get()),
            close_to_tray=bool(self.var_close_tray.get()),
            notify_scan_done=bool(self.var_notify.get()),
            ai_auto_enrich=bool(self.var_autoai.get()),
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


# ---------------------------------------------------------------------------
# AI provider settings
# ---------------------------------------------------------------------------
class AISettingsDialog(tk.Toplevel):
    """Provider CRUD, active selection, `test connection`, live model listing."""

    def __init__(self, master: tk.Misc, cfg: Config, save_cb):
        super().__init__(master)
        self.cfg = cfg
        self.save_cb = save_cb
        self.ai_cfg = cfg.ai_config()
        self.provider_keys: dict[str, str] = {}

        self.title(f"{APP_NAME} — AI Models")
        self.geometry("880x600")
        self.minsize(760, 480)
        self.transient(master)
        self.configure(bg="#11161f")

        self._build()
        self.grab_set()

    def _build(self):
        pad = {"padx": 8, "pady": 4}
        frm = tk.Frame(self, bg="#11161f"); frm.pack(fill="both", expand=True, padx=10, pady=10)
        frm.columnconfigure(0, weight=1); frm.rowconfigure(0, weight=1)

        panes = ttk.PanedWindow(frm, orient="horizontal")
        panes.grid(row=0, column=0, columnspan=2, sticky="nsew")
        panes.paneconfigure(0, weight=1)

        left = tk.Frame(panes, bg="#0d1117")
        panes.add(left, weight=2)
        tk.Label(left, text="Providers", bg="#0d1117", fg="#e6edf3",
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=6, pady=(4, 2))
        cols = ("Provider", "k")
        tree = ttk.Treeview(left, columns=cols, show="headings", selectmode="browse", height=16)
        for c, w in zip(cols, (230, 60)):
            tree.heading(c, text=c); tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(left, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=8)
        vsb.pack(side="right", fill="y", pady=8)
        tree.bind("<<TreeviewSelect>>", lambda e: self._on_select())
        self.tree = tree

        acts = tk.Frame(frm, bg="#11161f")
        acts.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Button(acts, text="Add preset…", command=self._add_preset).pack(side="left", padx=4)
        ttk.Button(acts, text="Duplicate", command=self._dup).pack(side="left", padx=4)
        ttk.Button(acts, text="Delete", command=self._delete).pack(side="left", padx=4)
        ttk.Button(acts, text="Set active", command=self._set_active).pack(side="left", padx=4)
        ttk.Button(acts, text="Test connection", command=self._test).pack(side="right", padx=4)

        right = tk.Frame(panes, bg="#161b26")
        panes.add(right, weight=3)
        container = tk.Frame(right, bg="#161b26"); container.pack(fill="both", expand=True, padx=10, pady=10)

        def label(text, row):
            tk.Label(container, text=text, bg="#161b26", fg="#8b98a9",
                     font=("Segoe UI", 9, "bold"), anchor="w").grid(row=row, column=0, sticky="we", pady=(8, 0))

        label("NAME", 0)
        self.var_name = tk.StringVar()
        tk.Entry(container, textvariable=self.var_name, bg="#1a2130", fg="#e6edf3",
                 insertbackground="#e6edf3", relief="flat", highlightthickness=1,
                 highlightbackground="#263045").grid(row=1, column=0, sticky="we")

        label("ENDPOINT (base URL)", 2)
        self.var_url = tk.StringVar()
        tk.Entry(container, textvariable=self.var_url, bg="#1a2130", fg="#e6edf3",
                 insertbackground="#e6edf3", relief="flat", highlightthickness=1,
                 highlightbackground="#263045").grid(row=3, column=0, sticky="we")

        label("API KEY (empty for local models)", 4)
        self.var_key = tk.StringVar()
        self.var_key_box = tk.Entry(container, textvariable=self.var_key, show="•", bg="#1a2130",
                                    fg="#e6edf3", insertbackground="#e6edf3", relief="flat",
                                    highlightthickness=1, highlightbackground="#263045")
        self.var_key_box.grid(row=5, column=0, sticky="we")
        self.key_show_var = tk.BooleanVar(value=False)
        tk.Checkbutton(container, text="show key", variable=self.key_show_var, bg="#161b26",
                       activebackground="#161b26", fg="#8b98a9", highlightthickness=0,
                       selectcolor="#1a2130", font=("Segoe UI", 8),
                       command=self._toggle_key).grid(row=6, column=0, sticky="w")

        label("CHAT MODEL", 7)
        self.var_model = tk.StringVar()
        row_model = tk.Frame(container, bg="#161b26")
        row_model.grid(row=8, column=0, sticky="we")
        tk.Entry(row_model, textvariable=self.var_model, bg="#1a2130", fg="#e6edf3",
                 insertbackground="#e6edf3", relief="flat", highlightthickness=1,
                 highlightbackground="#263045").pack(side="left", fill="x", expand=True)
        ttk.Button(row_model, text="List models", command=self._list_models, style="Ghost.TButton").pack(
            side="left", padx=6)

        label("EMBEDDING MODEL (optional · semantic search)", 9)
        self.var_embed = tk.StringVar()
        tk.Entry(container, textvariable=self.var_embed, bg="#1a2130", fg="#e6edf3",
                 insertbackground="#e6edf3", relief="flat", highlightthickness=1,
                 highlightbackground="#263045").grid(row=10, column=0, sticky="we")

        self.var_hint = tk.StringVar(value="")
        tk.Label(container, textvariable=self.var_hint, bg="#161b26", fg="#5b6777",
                 font=("Segoe UI", 8), wraplength=430, justify="left").grid(
            row=12, column=0, sticky="we", pady=(8, 0))

        self.var_test = tk.StringVar(value="")
        tk.Label(container, textvariable=self.var_test, bg="#161b26", fg="#34d399",
                 font=("Segoe UI", 9), wraplength=430).grid(row=13, column=0, sticky="w", pady=(6, 0))

        tk.Label(container, text="Kind", bg="#161b26", fg="#8b98a9", font=("Segoe UI", 9, "bold")).grid(
            row=14, column=0, sticky="w", pady=(12, 0))
        self.var_kind = tk.StringVar(value="openai")
        tk.Combobox(container, textvariable=self.var_kind, state="readonly",
                    values=("openai", "anthropic", "gemini")).grid(row=15, column=0, sticky="w")

        row_save = tk.Frame(container, bg="#161b26")
        row_save.grid(row=16, column=0, sticky="e", pady=14)
        self.var_active = tk.BooleanVar()
        ttk.Checkbutton(row_save, text="Active provider", variable=self.var_active,
                        style="Accent.TButton").pack(side="left", padx=6)
        ttk.Button(row_save, text="Save", command=self._save).pack(side="left", padx=4)
        ttk.Button(row_save, text="Done", command=self.destroy).pack(side="left")

        container.columnconfigure(0, weight=1)
        self.tree = tree
        self._load_tree()
        self._on_select()

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _load_tree(self):
        self.tree.delete(*self.tree.get_children())
        self.provider_keys.clear()
        for i, p in enumerate(self.ai_cfg.providers):
            iid = f"p{i}"
            mark = "● " if p.name == self.ai_cfg.active else "  "
            self.tree.insert("", "end", iid=iid, values=(mark + p.name, p.kind))
            self.provider_keys[iid] = p.name
        if self.ai_cfg.providers:
            self.tree.selection_set(self.tree.get_children()[0])

    def _selected_provider(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.provider_keys.get(sel[0])

    def _on_select(self):
        name = self._selected_provider()
        p = next((x for x in self.ai_cfg.providers if x.name == name), None)
        if not p:
            return
        from .theme import CARD, MUTED
        self.var_name.set(p.name)
        self.var_url.set(p.base_url)
        self.var_key.set(p.api_key)
        self.var_model.set(p.model)
        self.var_embed.set(p.embedding_model)
        self.var_kind.set(p.kind)
        self.var_active.set(p.name == self.ai_cfg.active)
        hint = self._hint_for(p.name)
        self.var_hint.set(hint)
        self.var_test.set("")

    @staticmethod
    def _hint_for(name):
        from dropLens.ai.providers import preset_for
        pr = preset_for(name)
        return pr["hint"] if pr else "Local models run fully offline. Cloud providers need an API key."

    def _collect(self) -> object:
        from dropLens.ai.providers import AIProvider
        return AIProvider(
            name=self.var_name.get().strip() or "Provider",
            kind=self.var_kind.get() or "openai",
            base_url=self.var_url.get().strip(),
            api_key=self.var_key.get().strip(),
            model=self.var_model.get().strip(),
            embedding_model=self.var_embed.get().strip(),
        )

    def _save(self):
        provider = self._collect()
        self.ai_cfg.upsert_provider(provider)
        if self.var_active.get():
            self.ai_cfg.active = provider.name
        else:
            current = self._selected_provider()
            if current and current == self.ai_cfg.active and current != provider.name:
                pass
        self.cfg.set_ai_config(self.ai_cfg)
        self.save_cb()
        self._load_tree()
        self._flash("Saved ✓")

    def _flash(self, text):
        self.var_test.set(text)

    def _add_preset(self):
        from dropLens.ai.providers import PRESETS
        names = list(PRESETS.keys())
        dlg = tk.Toplevel(self)
        dlg.title("Add provider preset")
        dlg.configure(bg="#0d1117")
        dlg.transient(self)
        dlg.resizable(False, False)
        tk.Label(dlg, text="Choose a provider template:", bg="#0d1117", fg="#e6edf3").pack(
            padx=14, pady=(12, 4))
        var = tk.StringVar(value=names[0])
        box = ttk.Combobox(dlg, textvariable=var, values=names, state="readonly", width=30)
        box.pack(padx=14, pady=4)

        def pick():
            self.ai_cfg.upsert_provider(
                __import__("dropLens.ai.providers", fromlist=["AIProvider"]).AIProvider(
                    name=var.get(), **{k: v for k, v in
                                       __import__("dropLens.ai.providers", fromlist=["PRESETS"]).PRESETS[var.get()].items() if k != "hint"}))
            self.cfg.set_ai_config(self.ai_cfg)
            self.save_cb()
            self._load_tree()
            dlg.destroy()

        ttk.Button(dlg, text="Add", command=pick).pack(pady=10)
        dlg.grab_set()

    def _dup(self):
        name = self._selected_provider()
        p = next((x for x in self.ai_cfg.providers if x.name == name), None)
        if not p:
            return
        from copy import deepcopy
        cp = deepcopy(p)
        cp.name = cp.name + " copy"
        self.ai_cfg.upsert_provider(cp)
        self._load_tree()

    def _delete(self):
        name = self._selected_provider()
        if not name or not messagebox.askyesno("DropLens", f"Delete provider “{name}”?"):
            return
        self.ai_cfg.delete_provider(name)
        self._load_tree()

    def _set_active(self):
        name = self._selected_provider()
        if name:
            self.ai_cfg.active = name
            self.cfg.set_ai_config(self.ai_cfg)
            self.save_cb()
            self._load_tree()
            self.var_test.set(f"{name} is now the active provider ✓")
        self._on_select()

    def _test(self):
        provider = self._collect()
        self.var_test.set("Testing connection…")
        self.update_idletasks()

        def work():
            from dropLens.ai.client import AIConnectionError, ping
            try:
                reply = ping(provider)
                ok = reply.strip().lower().startswith("ok")
                self.after(0, lambda: self.var_test.set(
                    f"Connected ✓ ({provider.name} · {provider.model} · reply: {reply[:40]})"
                    if ok else f"Reachable, unexpected reply: {reply[:60]}"))
            except AIConnectionError as exc:
                self.after(0, lambda e=exc: self.var_test.set(f"Failed: {e}"))
            except Exception as exc:
                self.after(0, lambda e=exc: self.var_test.set(f"Failed: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _list_models(self):
        provider = self._collect()
        self.var_test.set("Querying model list…")
        self.update_idletasks()

        def work():
            from dropLens.ai.client import AIConnectionError, list_models
            try:
                models = list_models(provider)
                if not models:
                    self.after(0, lambda: self.var_test.set(
                        "No model list exposed at this endpoint (enter the model name manually)."))
                    return
                self.after(0, lambda: self._show_models(models, provider))
            except AIConnectionError as exc:
                self.after(0, lambda e=exc: self.var_test.set(f"Failed: {e}"))

        threading.Thread(target=work, daemon=True).start()

    def _show_models(self, models, provider):
        dlg = tk.Toplevel(self)
        dlg.title("Available models")
        dlg.geometry("420x460")
        dlg.transient(self)
        tk.Label(dlg, text="Select a chat model:", bg="#0d1117", fg="#e6edf3").pack(
            padx=10, pady=8, anchor="w")
        box = tk.Listbox(dlg, bg="#1a2130", fg="#e6edf3", selectbackground="#2563eb",
                         selectforeground="#ffffff", font=("Segoe UI", 10))
        for m in models:
            box.insert("end", m)
        box.pack(fill="both", expand=True, padx=10)
        emb = [m for m in models if "embed" in m.lower() or "bge" in m.lower()]

        def choose():
            sel = box.curselection()
            if sel:
                self.var_model.set(models[sel[0]])
            dlg.destroy()

        ttk.Button(dlg, text="Use selected", command=choose).pack(pady=8, side="left", padx=24)
        if emb:
            tk.Label(dlg, text="Possible embedding models: " + ", ".join(emb[:6]) + "…",
                     bg="#0d1117", fg="#5b6777", font=("Segoe UI", 8), wraplength=380,
                     justify="left").pack(side="bottom", padx=10, pady=8)
        ttk.Button(dlg, text="Close", command=dlg.destroy).pack(pady=8, side="right", padx=24)
        dlg.grab_set()

    def _toggle_key(self):
        self.var_key_box.configure(show="" if self.key_show_var.get() else "•")