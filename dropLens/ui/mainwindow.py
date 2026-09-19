"""DropLens v2 main window.

Layout: brand sidebar (navigation rail) + page area + status bar.
Pages: Library, Search (keyword + semantic), AI Assistant, Duplicates, Activity.
Cross-thread contract is unchanged: workers only enqueue dicts on ``self.q``;
the main thread drains them in ``_poll``.
"""
from __future__ import annotations

import os
import queue
import re as _re
import threading
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
    AISettingsDialog,
    DuplicatesDialog,
    SettingsDialog,
    TranslateDialog,
    open_containing_folder,
    open_path,
)
from .icon import app_icon
from .tray import Tray
from .theme import apply_ttk, card_outer, font, make_button
from . import theme as T

try:
    from tkinterdnd2 import DND_FILES
except ImportError:  # pragma: no cover
    DND_FILES = "DND_FILES"

MODES = {"Library": "\u2302", "Search": "\u2315", "AI Assistant": "\u2726",
         "Duplicates": "\u229e", "Activity": "\u270e"}


def _fmt_size(n) -> str:
    return human_size(n)


class DropLensApp:
    def __init__(self, master: tk.Misc):
        self.master = master
        self.master.title(f"{APP_NAME}  ·  {APP_TAGLINE}")
        self.master.geometry("1300x800")
        self.master.minsize(1080, 660)
        self.master.configure(bg=T.BG)
        apply_ttk(master)

        self.ready = False
        self.cfg_store = ConfigStore()
        self.cfg: Config = self.cfg_store.get()
        self.db_path = self._resolve_db_path(self.cfg)
        self.db = Catalog(self.db_path)
        self.scanner = ScanManager(self.db, self.cfg, on_progress=self._enqueue)
        self.translator = Translator(self.db)
        self.watcher: FolderWatcher | None = None
        self.q: "queue.Queue[dict]" = queue.Queue()

        self._results_meta: dict = {"offset": 0}
        self._results_items: dict[str, int] = {}

        # AI
        from ..ai.svc import AIService
        self.ai = AIService(self.db, config=lambda: self.cfg.ai_config())

        self._build_layout()
        self._bind_drops()

        self.ready = True

    # ------------------------------------------------------------------
    # boot-level plumbing
    # ------------------------------------------------------------------
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
        self.ai.catalog = self.db
        self.refresh_all()

    def boot_refresh(self) -> None:
        self.refresh_all()
        self.master.after(80, self._poll)
        if self.cfg.get("tray_enabled", True):
            self._start_tray()
        if self.cfg.get("watch_enabled"):
            self._start_watcher()

    def refresh_all(self) -> None:
        self.refresh_roots()
        self.refresh_stats()
        self.refresh_duplicates()
        self.refresh_tags()
        self.refresh_ai_badge()
        self._refresh_favorites()
        self.results_empty_state()

    # ------------------------------------------------------------------
    # layout
    # ------------------------------------------------------------------
    def _build_layout(self) -> None:
        outer = tk.Frame(self.master, bg=T.BG)
        outer.pack(fill="both", expand=True)

        # sidebar
        side = tk.Frame(outer, bg=T.PANEL, width=196, highlightbackground=T.RAISED,
                        highlightthickness=1)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        img = app_icon(40)
        try:
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(img)
        except Exception:
            photo = None
        brand = tk.Frame(side, bg=T.PANEL)
        brand.pack(fill="x", pady=(18, 10))
        if photo:
            pic = tk.Label(brand, image=photo, bg=T.PANEL)
            pic.image = photo
            pic.pack(side="left", padx=(18, 8))
        btext = tk.Frame(brand, bg=T.PANEL)
        btext.pack(side="left")
        tk.Label(btext, text="DropLens", bg=T.PANEL, fg=T.TEXT,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(btext, text=f"v{APP_VERSION}", bg=T.PANEL, fg=T.FAINT,
                 font=font(7)).pack(anchor="w")

        self.nav_buttons: dict[str, tk.Button] = {}
        nav = tk.Frame(side, bg=T.PANEL)
        nav.pack(fill="x", pady=6)
        for page in MODES:
            b = tk.Button(nav, text=f"  {MODES[page]}  {page}", command=lambda p=page: self.show_page(p),
                          bg=T.PANEL, fg=T.MUTED, relief="flat", anchor="w",
                          font=("Segoe UI", 10), padx=14, pady=9, activebackground=T.CARD,
                          activeforeground=T.TEXT, highlightthickness=0, bd=0)
            b.pack(fill="x", padx=8, pady=2)
            self.nav_buttons[page] = b

        tk.Label(side, text="", bg=T.PANEL).pack(expand=True)

        ai_pill = tk.Frame(side, bg=T.CARD, highlightbackground=T.RAISED, highlightthickness=1)
        ai_pill.pack(fill="x", padx=10, pady=(0, 4))
        self.var_ai_badge = tk.StringVar(value="AI: local / not configured")
        tk.Label(ai_pill, bg=T.CARD, textvariable=self.var_ai_badge,
                 fg=T.LINK if False else T.MUTED, font=font(8), pady=6).pack(fill="x")
        foot = tk.Frame(side, bg=T.PANEL)
        foot.pack(fill="x", pady=10)
        make_button(foot, "Settings", self.open_settings, "Ghost.TButton").pack(
            fill="x", padx=10, pady=2)
        make_button(foot, "About", lambda: AboutDialog(self.master), "Ghost.TButton").pack(
            fill="x", padx=10, pady=2)

        # main content
        content = tk.Frame(outer, bg=T.BG)
        content.pack(side="left", fill="both", expand=True)

        # header
        head = tk.Frame(content, bg=T.PANEL, highlightbackground=T.RAISED, highlightthickness=1)
        head.pack(fill="x")
        self.var_page = tk.StringVar(value="Library")
        tk.Label(head, textvariable=self.var_page, bg=T.PANEL, fg=T.TEXT,
                 font=("Segoe UI", 13, "bold")).pack(side="left", padx=18, pady=12)
        self.var_ai_state = tk.StringVar(value="")
        tk.Label(head, textvariable=self.var_ai_state, bg=T.PANEL, fg=T.MUTED,
                 font=font(9)).pack(side="left", padx=4)

        # pages container
        self.pages: dict[str, tk.Frame] = {}
        holder = tk.Frame(content, bg=T.BG)
        holder.pack(fill="both", expand=True)
        for name in MODES:
            f = tk.Frame(holder, bg=T.BG)
            f.grid(row=0, column=0, sticky="nsew")
            self.pages[name] = f
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)

        self._build_library_page(self.pages["Library"])
        self._build_search_page(self.pages["Search"])
        self._build_ai_page(self.pages["AI Assistant"])
        self._build_duplicates_page(self.pages["Duplicates"])
        self._build_activity_page(self.pages["Activity"])

        # status bar
        status = tk.Frame(content, bg=T.PANEL, highlightbackground=T.RAISED, highlightthickness=1)
        status.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(status, mode="indeterminate", length=180,
                                        style="Accent.Horizontal.TProgressbar")
        self.progress.pack(side="left", padx=10, pady=8)
        self.var_status = tk.StringVar(value="Ready. Drop files or folders to begin.")
        tk.Label(status, textvariable=self.var_status, bg=T.PANEL, fg=T.TEXT,
                 anchor="w", font=font(9)).pack(side="left", fill="x", expand=True, padx=10)
        self.var_stats = tk.StringVar(value="")
        tk.Label(status, textvariable=self.var_stats, bg=T.PANEL, fg=T.MUTED,
                 anchor="e", font=font(9)).pack(side="right", padx=10)

        # window/tray protocol
        self.master.protocol("WM_DELETE_WINDOW", self._on_close)

        self.show_page("Library")

    def show_page(self, name: str) -> None:
        for p, f in self.pages.items():
            f.tkraise() if p == name else None
        self.var_page.set(name)
        for p, b in self.nav_buttons.items():
            active = p == name
            b.configure(bg=T.ACCENT_DARK if active else T.PANEL,
                        fg="#ffffff" if active else T.MUTED)
        if name == "Search":
            self.refresh_empty_if_needed()
        if name == "AI Assistant":
            self._ai_on_page_show()

    # ------------------------------------------------------------------
    # library page
    # ------------------------------------------------------------------
    def _build_library_page(self, t: tk.Frame) -> None:
        # hero drop zone
        hero = tk.Frame(t, bg=T.CARD, highlightbackground=T.RAISED, highlightthickness=1,
                        cursor="hand2", padx=24, pady=22)
        hero.pack(fill="x", padx=14, pady=(14, 6))
        self.drop = hero
        img = app_icon(56)
        try:
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(img)
        except Exception:
            photo = None
        row = tk.Frame(hero, bg=T.CARD)
        row.pack()
        if photo:
            pic = tk.Label(row, image=photo, bg=T.CARD)
            pic.image = photo
            pic.grid(row=0, column=0, rowspan=2, padx=(0, 16))
        lbl = tk.Frame(row, bg=T.CARD)
        lbl.grid(row=0, column=1, rowspan=2)
        tk.Label(lbl, text="Drop any file or folder here", bg=T.CARD, fg=T.TEXT,
                 font=("Segoe UI", 16, "bold")).pack(anchor="w")
        tk.Label(lbl, text="Silently scanned, extracted, organized and searchable — your originals are never modified.",
                 bg=T.CARD, fg=T.MUTED, font=font(9)).pack(anchor="w")
        actions = tk.Frame(hero, bg=T.CARD)
        actions.pack(pady=(14, 0))
        make_button(actions, "＋  Add Folders", self.add_folders, "Accent.TButton").pack(
            side="left", padx=4)
        make_button(actions, "＋  Add Files", self.add_files).pack(side="left", padx=4)
        make_button(actions, "⟳  Scan all", self.rescan_all).pack(side="left", padx=4)
        make_button(actions, "■  Stop", self.stop_scan, "Danger.TButton").pack(side="left", padx=4)

        self.hero_hint = photo
        hero.bind("<Enter>", lambda e: hero.configure(bg=T.CARD_HOVER))
        hero.bind("<Leave>", lambda e: hero.configure(bg=T.CARD))
        for child in (row, lbl):
            child.bind("<Enter>", lambda e: hero.configure(bg=T.CARD_HOVER))
            child.bind("<Leave>", lambda e: hero.configure(bg=T.CARD))

        # stat cards
        cards = tk.Frame(t, bg=T.BG)
        cards.pack(fill="x", padx=14, pady=6)
        self.stat_labels: dict[str, tk.StringVar] = {}
        names = [("files", "Files indexed"), ("folders", "Folders"),
                 ("size", "Size indexed"), ("dups", "Duplicates"),
                 ("tags", "Smart tags"), ("ai", "AI enriched")]
        for i, (k, caption) in enumerate(names):
            c = card_outer(cards)
            c.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            cards.columnconfigure(i, weight=1)
            inner = tk.Frame(c, bg=T.CARD)
            inner.pack(fill="both", expand=True, padx=12, pady=10)
            tk.Label(inner, text=caption, bg=T.CARD, fg=T.MUTED, font=font(8)).pack(anchor="w")
            v = tk.StringVar(value="—")
            tk.Label(inner, textvariable=v, bg=T.CARD, fg=T.TEXT,
                     font=("Segoe UI", 15, "bold")).pack(anchor="w")
            self.stat_labels[k] = v

        # roots table
        mid = tk.Frame(t, bg=T.BG)
        mid.pack(fill="both", expand=True, padx=14, pady=(2, 6))
        head = tk.Frame(mid, bg=T.BG)
        head.pack(fill="x")
        tk.Label(head, text="Monitored folders", bg=T.BG, fg=T.TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        self.auto_watch_var = tk.BooleanVar(value=bool(self.cfg.get("watch_enabled", False)))
        ttk.Checkbutton(head, text="Auto-watch", variable=self.auto_watch_var,
                        command=self._toggle_watch).pack(side="right", padx=4)

        cols = ("Folder", "Files", "Folders", "Last scan", "Status")
        tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, (300, 90, 80, 150, 110)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w" if c == "Folder" else "center", stretch=(c == "Folder"))
        vsb = ttk.Scrollbar(mid, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y", padx=(4, 0))
        self.root_tree = tree

        menu = tk.Menu(tree, tearoff=0, bg=T.CARD, fg=T.TEXT, activebackground=T.ACCENT)
        menu.add_command(label="Rescan", command=self._menu_rescan)
        menu.add_command(label="Full rebuild (re-extract)", command=self._menu_full_rebuild)
        menu.add_command(label="Open in Explorer", command=self._menu_open_explorer)
        menu.add_separator()
        menu.add_command(label="Remove from Library", command=self._menu_remove_root)
        tree.bind("<Button-3>", lambda e: self._root_menu(e, menu))
        tree.bind("<Double-1>", lambda e: self._root_double_click())

    # ------------------------------------------------------------------
    # search page
    # ------------------------------------------------------------------
    def _build_search_page(self, t: tk.Frame) -> None:
        bar = tk.Frame(t, bg=T.BG)
        bar.pack(fill="x", padx=14, pady=12)
        self.term_var = tk.StringVar()
        entry = tk.Entry(bar, textvariable=self.term_var, bg=T.CARD, fg=T.TEXT,
                         insertbackground=T.TEXT, relief="flat", highlightthickness=1,
                         highlightbackground=T.RAISED, highlightcolor=T.ACCENT,
                         font=("Segoe UI", 12), width=44)
        entry.pack(side="left", ipady=7, padx=(0, 8))
        self.term_entry = entry
        entry.bind("<Return>", lambda e: self.do_search())

        self.mode_var = tk.StringVar(value="All")
        modes = ttk.Combobox(bar, textvariable=self.mode_var, state="readonly", width=9,
                             values=("All", "Name", "Path", "Content"))
        modes.pack(side="left", padx=(0, 6))
        self.cat_var = tk.StringVar(value="All types")
        cats = ["All types"] + [category_label(c) for c in all_categories()]
        ttk.Combobox(bar, textvariable=self.cat_var, state="readonly", width=15, values=cats).pack(
            side="left", padx=(0, 6))
        self.semantic_var = tk.BooleanVar(value=bool(self.cfg.get("semantic_default", False)))
        ttk.Checkbutton(bar, text="Semantic (AI meaning)", variable=self.semantic_var,
                        command=self._semantic_toggle).pack(side="left", padx=4)
        make_button(bar, "Search", self.do_search, "Accent.TButton").pack(side="left", padx=4)
        make_button(bar, "Clear", self.clear_search).pack(side="left")

        body = tk.Frame(t, bg=T.BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=T.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.results_label = tk.StringVar(value="Results")
        tk.Label(left, textvariable=self.results_label, bg=T.BG, fg=T.TEXT,
                 font=font(10, True)).pack(anchor="w", pady=(0, 4))
        self.tag_chips = tk.Frame(left, bg=T.BG)
        self.tag_chips.pack(fill="x", pady=(0, 4))

        cols = ("Name", "Category", "Size", "Modified", "Path")
        tree_holder = tk.Frame(left, bg=T.BG)
        tree_holder.pack(fill="both", expand=True)
        tree = ttk.Treeview(tree_holder, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, (230, 100, 80, 140, 380)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w" if c in ("Name", "Path") else "center",
                        stretch=(c == "Path"))
        vsb = ttk.Scrollbar(tree_holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y", padx=(4, 0))
        tree.bind("<<TreeviewSelect>>", self._on_result_select)
        tree.bind("<Double-1>", lambda e: self._open_selected())
        self.results_tree = tree

        actions = tk.Frame(left, bg=T.BG)
        actions.pack(fill="x", pady=(6, 0))
        make_button(actions, "Open", self._open_selected).pack(side="left", padx=(0, 4))
        make_button(actions, "Folder", self._reveal_selected).pack(side="left", padx=(0, 4))
        make_button(actions, "★ Favorite", self._toggle_fav).pack(side="left", padx=(0, 4))
        make_button(actions, "Translate…", self._translate_selected).pack(side="left", padx=(0, 4))
        make_button(actions, "Copy Path", self._copy_path).pack(side="left", padx=(0, 4))
        make_button(actions, "Export…", self._export_results).pack(side="left", padx=(0, 4))
        make_button(actions, "Load more", self._load_more).pack(side="right")

        # insights panel
        right = tk.Frame(body, bg=T.CARD, highlightbackground=T.RAISED, highlightthickness=1)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        inner = tk.Frame(right, bg=T.CARD)
        inner.pack(fill="both", expand=True, padx=12, pady=10)

        self.var_insight_name = tk.StringVar(value="Select a result to inspect")
        tk.Label(inner, textvariable=self.var_insight_name, bg=T.CARD, fg=T.TEXT,
                 font=("Segoe UI", 11, "bold"), wraplength=360, justify="left").pack(anchor="w")

        self.var_insight_sub = tk.StringVar(value="")
        tk.Label(inner, textvariable=self.var_insight_sub, bg=T.CARD, fg=T.MUTED,
                 font=font(8), wraplength=360, justify="left").pack(anchor="w", pady=(2, 0))

        ins_actions = tk.Frame(inner, bg=T.CARD)
        ins_actions.pack(fill="x", pady=6)
        make_button(ins_actions, "AI summary", self._ai_summary).pack(side="left", padx=(0, 4))
        make_button(ins_actions, "Notes…", self._edit_notes).pack(side="left", padx=(0, 4))
        make_button(ins_actions, "Related", self._show_related).pack(side="left")
        make_button(ins_actions, "Report", self._folder_report_selected).pack(side="right")

        self.insight_text = tk.Frame(inner, bg=T.CARD)
        self.insight_text.pack(fill="both", expand=True)
        self.preview = tk.Text(self.insight_text, wrap="word", state="disabled",
                               bg=T.CARD, fg=T.TEXT, relief="flat",
                               highlightthickness=0, font=("Segoe UI", 10),
                               padx=8, pady=6)
        pvsb = ttk.Scrollbar(self.insight_text, orient="vertical", command=self.preview.yview)
        self.preview.configure(yscrollcommand=pvsb.set)
        self.preview.pack(side="left", fill="both", expand=True)
        pvsb.pack(side="right", fill="y")
        self.preview.tag_configure("hl", background=T.WARN, foreground="#111")
        self.preview.tag_configure("head", font=("Segoe UI", 9, "bold"), foreground=T.ACCENT2)
        self.preview.tag_configure("dim", foreground=T.FAINT)
        self.preview.tag_configure("good", foreground=T.OK)

        # favorite list drawer (populated on demand)
        pass

    # ------------------------------------------------------------------
    # AI assistant page
    # ------------------------------------------------------------------
    def _build_ai_page(self, t: tk.Frame) -> None:
        bar = tk.Frame(t, bg=T.BG)
        bar.pack(fill="x", padx=14, pady=12)
        tk.Label(bar, text="Ask anything about your library", bg=T.BG, fg=T.TEXT,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        make_button(bar, "AI-enrich library", self._ai_enrich_all, "Accent.TButton").pack(
            side="right", padx=4)
        make_button(bar, "Folder report…", self._folder_report_dialog).pack(side="right", padx=4)

        body = tk.Frame(t, bg=T.BG)
        body.pack(fill="both", expand=True, padx=14, pady=(0, 10))
        body.rowconfigure(0, weight=1)
        body.rowconfigure(1, weight=0)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=1)

        chat_panel = tk.Frame(body, bg=T.CARD, highlightbackground=T.RAISED, highlightthickness=1)
        chat_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.chat = tk.Text(chat_panel, wrap="word", state="disabled", bg=T.CARD, fg=T.TEXT,
                            font=("Segoe UI", 10), padx=12, pady=10, relief="flat",
                            highlightthickness=0)
        cvsb = ttk.Scrollbar(chat_panel, orient="vertical", command=self.chat.yview)
        self.chat.configure(yscrollcommand=cvsb.set)
        self.chat.pack(side="left", fill="both", expand=True)
        cvsb.pack(side="right", fill="y")
        self.chat.tag_configure("q", background=T.PANEL, foreground=T.TEXT,
                                font=("Segoe UI", 10, "bold"), lmargin1=12, lmargin2=12,
                                spacing1=10, spacing3=10)
        self.chat.tag_configure("a", foreground=T.TEXT, lmargin1=12, lmargin2=12, spacing2=4)
        self.chat.tag_configure("cite", foreground=T.LINK)
        self.chat.tag_configure("meta", foreground=T.FAINT, font=("Segoe UI", 8))

        input_row = tk.Frame(body, bg=T.BG)
        input_row.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.ask_var = tk.StringVar()
        ask_entry = tk.Entry(input_row, textvariable=self.ask_var, bg=T.CARD, fg=T.TEXT,
                             insertbackground=T.TEXT, relief="flat", highlightthickness=1,
                             highlightbackground=T.RAISED, highlightcolor=T.ACCENT,
                             font=("Segoe UI", 11))
        ask_entry.pack(side="left", fill="x", expand=True, ipady=8)
        ask_entry.bind("<Return>", lambda e: self._ask())
        make_button(input_row, "Ask", self._ask, "Accent.TButton").pack(side="left", padx=(8, 0))

        self.ai_progress = ttk.Progressbar(body, mode="determinate", maximum=100, style="Accent.Horizontal.TProgressbar")
        self.ai_progress.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        # citations panel
        cites = tk.Frame(body, bg=T.CARD, highlightbackground=T.RAISED, highlightthickness=1)
        cites.grid(row=0, column=1, sticky="nsew", padx=(6, 0), rowspan=3)
        tk.Label(cites, text="Cited files", bg=T.CARD, fg=T.MUTED,
                 font=font(9, True)).pack(anchor="w", padx=10, pady=(10, 4))
        self.cite_tree = ttk.Treeview(cites, columns=("f",), show="tree", height=10)
        self.cite_tree.heading("#0", text="")
        self.cite_tree.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.cite_tree.bind("<Double-1>", lambda e: self._open_selected_cite())

    def _ai_on_page_show(self) -> None:
        if self.chat.get("1.0", "end").strip():
            return
        self._ai_append("meta",
                        "Tip: ask things like “What handles GDPR?” or “Summarize my invoices”. "
                        "Answers cite the exact files. Configure any local or cloud model under Settings → AI.")
        try:
            from ..ai.client import AINotConfigured, AIConnectionError
            p = self.ai.provider()
            state = f"Active model: {p.name} ({p.model})"
            if p.is_local:
                state += " · offline"
            self._ai_append("meta", state)
        except (AINotConfigured, AIConnectionError) as exc:
            self._ai_append("meta", "No AI provider configured yet — open Settings → AI Models.")

    def _ai_append(self, tag: str, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert("end", text + "\n", tag)
        self.chat.see("end")
        self.chat.configure(state="disabled")

    def _ask(self) -> None:
        question = self.ask_var.get().strip()
        if not question:
            return
        if getattr(self, "_ask_busy", False):
            return
        self.ask_var.set("")
        self._ai_append("q", "❯ " + question)
        self._ai_append("meta", "Thinking…")
        self.ai_progress.configure(mode="indeterminate")
        self.ai_progress.start(12)
        self._ask_busy = True

        def work():
            try:
                answer, cites = self.ai.ask(question)
                self._enqueue({"kind": "ai_answer", "text": answer, "cites": cites})
            except Exception as exc:
                self._enqueue({"kind": "ai_fail", "msg": str(exc)})
            finally:
                self._ask_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _ai_enrich_all(self) -> None:
        from ..ai.client import AIConnectionError, AINotConfigured
        try:
            self.ai.provider()
        except (AINotConfigured, AIConnectionError) as exc:
            messagebox.showinfo("DropLens AI", f"Configure an AI model first.\n\n{exc}")
            return
        if not messagebox.askyesno(
                "DropLens AI",
                "Generate AI summaries, smart tags and embeddings for every indexed file?\n\n"
                "Smaller files first; you can stop at any time."):
            return

        def work():
            def progress(done, total):
                self._enqueue({"kind": "ai_progress", "done": done, "total": total,
                               "pct": int(100 * done / max(1, total))})
            try:
                result = self.ai.index_ai(progress, stop=lambda: self._ai_stop_flag)
                self._enqueue({"kind": "ai_enrich_done", "text": result})
            except Exception as exc:
                self._enqueue({"kind": "ai_fail", "msg": str(exc)})

        self._ai_stop_flag = False
        self.master.after(60, lambda: self._enqueue({"kind": "ai_progress", "done": 0, "total": 1, "pct": 0}))
        threading.Thread(target=work, daemon=True).start()

    def _ai_stop(self) -> None:
        self._ai_stop_flag = True
        self.var_ai_state.set("Stopping AI enrichment…")

    def _folder_report_dialog(self) -> None:
        d = filedialog.askdirectory(title="Generate an AI folder report")
        if not d:
            return
        self._ai_append("q", f"❯ Report for: {d}")

        def work():
            try:
                report = self.ai.folder_report(d)
                self._enqueue({"kind": "ai_answer", "text": f"[folder report]\n\n{report}", "cites": []})
            except Exception as exc:
                self._enqueue({"kind": "ai_fail", "msg": str(exc)})

        threading.Thread(target=work, daemon=True).start()

    def _open_selected_cite(self) -> None:
        sel = self.cite_tree.selection()
        for iid in sel:
            path = str(self.cite_tree.item(iid, "values")[0])
            if path and os.path.exists(path):
                open_path(path)

    # ------------------------------------------------------------------
    # duplicates + activity
    # ------------------------------------------------------------------
    def _build_duplicates_page(self, t: tk.Frame) -> None:
        row = tk.Frame(t, bg=T.BG)
        row.pack(fill="x", padx=14, pady=10)
        tk.Label(row, text="Duplicate groups share identical bytes — candidates for cleanup.",
                 bg=T.BG, fg=T.MUTED, font=font(9)).pack(side="left")
        make_button(row, "Refresh", self.refresh_duplicates).pack(side="right")
        make_button(row, "Details…", lambda: self._dup_detail()).pack(side="right", padx=6)
        cols = ("Hash", "Copies", "Wasted", "Example path")
        tree = ttk.Treeview(t, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (130, 80, 100, 460)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(t, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True, padx=(14, 0), pady=(0, 10))
        vsb.pack(side="right", fill="y", padx=(0, 14), pady=(0, 10))
        tree.bind("<Double-1>", lambda e: self._dup_detail())
        self.dup_tree = tree

    def _build_activity_page(self, t: tk.Frame) -> None:
        top = tk.Frame(t, bg=T.BG)
        top.pack(fill="x", padx=14, pady=10)
        tk.Label(top, text="Session activity", bg=T.BG, fg=T.TEXT,
                 font=("Segoe UI", 11, "bold")).pack(side="left")
        make_button(top, "Clear log", self._clear_log).pack(side="right")
        self.log = tk.Text(t, wrap="word", bg=T.PANEL, fg="#d8e2ec", state="disabled",
                           font=("Consolas", 9), padx=10, pady=8, relief="flat", highlightthickness=0)
        lv = ttk.Scrollbar(t, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=lv.set)
        self.log.pack(side="left", fill="both", expand=True, padx=(14, 0), pady=(0, 10))
        lv.pack(side="right", fill="y", padx=(0, 14), pady=(0, 10))
        self.log.tag_configure("info", foreground=T.TEXT)
        self.log.tag_configure("warning", foreground=T.WARN)
        self.log.tag_configure("error", foreground=T.DANGER)
        self.log.tag_configure("ok", foreground=T.OK)
        self._log("info", f"{APP_NAME} {APP_VERSION} started — {APP_TAGLINE}")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _log(self, level: str, text: str) -> None:
        tag = level if level in ("warning", "error", "ok") else "info"
        self.log.configure(state="normal")
        stamp = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{stamp}] {text}\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    # ------------------------------------------------------------------
    # drops
    # ------------------------------------------------------------------
    def _bind_drops(self) -> None:
        try:
            for w in (self.master, self.drop):
                w.drop_target_register(DND_FILES)
                w.dnd_bind("<<Drop>>", self._on_drop_formatted)
                w.dnd_bind("<<DropEnter>>", lambda e: self.drop.configure(
                    highlightbackground=T.ACCENT, bg=T.CARD_HOVER))
                w.dnd_bind("<<DropLeave>>", lambda e: self.drop.configure(
                    highlightbackground=T.RAISED, bg=T.CARD))
        except Exception:
            pass

    def _on_drop_formatted(self, event) -> None:
        try:
            paths = self.master.tk.splitlist(event.data)
        except Exception:
            paths = event.data.split("} ")
        self.drop.configure(highlightbackground=T.RAISED, bg=T.CARD)
        self.add_paths(list(paths))

    # ------------------------------------------------------------------
    # thread-safe message bridge
    # ------------------------------------------------------------------
    def _enqueue(self, msg: dict) -> None:
        self.q.put(msg)

    def _poll(self) -> None:
        drained = False
        while True:
            try:
                msg = self.q.get_nowait()
            except queue.Empty:
                break
            drained = True
            self._handle(msg)
        self.master.after(80, self._poll)

    def _handle(self, msg: dict) -> None:
        kind = msg.get("kind")
        if kind == "state":
            phase = msg.get("phase")
            if phase == "scanning":
                if not self.scanner.is_busy():
                    self.progress.start(12)
                self.var_status.set("Scanning silently…")
            else:
                self.progress.stop()
                self.var_status.set("Ready")
        elif kind == "file":
            self.var_status.set(f"Scanning … {msg.get('count', 0):,} entries")
        elif kind == "done":
            res = msg.get("result")
            if res:
                self._log("ok", f"✓ {res.root}: {res.indexed:,} indexed · {res.skipped:,} unchanged · "
                                f"{res.deleted} removed · {res.errors} errors · {res.duration:.1f}s")
                if self.cfg.get("notify_scan_done", True):
                    self._notify("Scan complete", f"{res.indexed:,} files indexed in {res.root}")
                if self.cfg.get("ai_auto_enrich", False):
                    self._auto_ai_enrich()
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
        elif kind == "ai_answer":
            self.ai_progress.stop()
            self._ai_append("a", msg.get("text", ""))
            self._render_cites(msg.get("cites", []))
            self.var_ai_state.set("")
        elif kind == "ai_fail":
            self.ai_progress.stop()
            self._ai_append("meta", f"AI error: {msg.get('msg')}")
            self.var_ai_state.set("")
        elif kind == "ai_progress":
            self.ai_progress.configure(mode="determinate", maximum=100)
            self.ai_progress["value"] = msg.get("pct", 0)
            self.var_ai_state.set(f"AI enrichment {msg.get('done', 0):,}/{msg.get('total', 0):,}")
        elif kind == "ai_enrich_done":
            self.ai_progress.stop()
            self.var_ai_state.set("")
            self._ai_append("a", msg.get("text", ""))
            self._log("ok", msg.get("text", ""))
            self.refresh_stats()
            self.refresh_tags()
            if self.cfg.get("notify_scan_done", True):
                self._notify("AI enrichment", "Library AI-metadata refreshed")
        elif kind == "ai_sum_done":
            self._ai_show_summary(msg.get("doc_id"), msg.get("text"))
        elif kind == "sem_result":
            self._results_meta = {"offset": 0}
            self._results_items.clear()
            for iid in self.results_tree.get_children():
                self.results_tree.delete(iid)
            self._fill(msg.get("rows", []))
            self.results_label.set(f"{len(msg.get('rows', []))} semantic matches for “{msg.get('term', '')}”")
            self.var_ai_state.set("")
        elif kind == "related_done":
            self._show_related_list(msg.get("rows", []))

    def _notify(self, title: str, body: str) -> None:
        tray = getattr(self, "tray", None)
        if tray and tray.visible:
            tray.notify(title, body)

    def _auto_ai_enrich(self) -> None:
        def work():

            def progress(done, total):
                self._enqueue({"kind": "ai_progress", "done": done, "total": total,
                               "pct": int(100 * done / max(1, total))})
            try:
                result = self.ai.index_ai(progress, stop=lambda: False)
                self._enqueue({"kind": "ai_enrich_done", "text": result})
            except Exception as exc:
                self._enqueue({"kind": "ai_fail", "msg": str(exc)})

        threading.Thread(target=work, daemon=True).start()

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
                    self.db.add_root(p)
                    self._log("info", f"Added to library: {p}")
                    roots_to_scan.append(p)
            else:
                parent = os.path.dirname(p) or os.getcwd()
                existing = [r["path"].lower() for r in self.db.list_roots()]
                if parent.lower() not in existing:
                    self.db.add_root(parent)
                    self._log("info", f"Added containing folder: {parent}")
                roots_to_scan.append(parent)
        if roots_to_scan:
            self.refresh_roots()
            self.scanner.scan_roots(roots_to_scan)
        self.show_page("Library")

    def _selected_roots(self) -> list[str]:
        items = self.root_tree.selection()
        if not items:
            return []
        out = []
        for iid in items:
            vals = self.root_tree.item(iid, "values")
            if vals:
                out.append(str(vals[0]))
        return out

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
        if not messagebox.askyesno(APP_NAME, "Remove from library and delete the local index of:\n\n" +
                                           "\n".join(roots) + "\n\n(Your original files are never touched.)"):
            return
        for r in roots:
            self.db.remove_root(r)
            if self.watcher:
                self.watcher.forget(r)
        self.refresh_roots()
        self.refresh_stats()
        self.refresh_duplicates()

    def _menu_open_explorer(self) -> None:
        for r in self._selected_roots():
            open_containing_folder(r)

    def _root_double_click(self) -> None:
        roots = self._selected_roots()
        if roots:
            open_containing_folder(roots[0])

    def _root_menu(self, event, menu) -> None:
        try:
            item = self.root_tree.identify_row(event.y)
            if item:
                self.root_tree.selection_set(item)
                menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def refresh_roots(self) -> None:
        if not hasattr(self, "root_tree"):
            return
        for iid in self.root_tree.get_children():
            self.root_tree.delete(iid)
        for r in self.db.list_roots():
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_scan"])) if r["last_scan"] else "never"
            files = max(0, int(r["files"]) - int(r["folders"]))
            self.root_tree.insert("", "end", iid=str(r["path"]),
                                  values=(r["path"], f"{files:,}", f"{r['folders']:,}", last, r["status"]))

    def refresh_stats(self) -> None:
        if not hasattr(self, "stat_labels"):
            return
        s = self.db.stats()
        ai_n = self._ai_enriched_count()
        self.stat_labels["files"].set(f"{s['files']:,}")
        self.stat_labels["folders"].set(f"{s['folders']:,}")
        self.stat_labels["size"].set(_fmt_size(s["bytes"]))
        self.stat_labels["dups"].set(f"{s['dup_groups']:,} groups")
        tags = self.db.list_tags()
        self.stat_labels["tags"].set(f"{len(tags):,}")
        self.stat_labels["ai"].set(f"{ai_n:,} files")
        dups = f" · ⧉ {s['dup_groups']:,} dup groups" if s["dup_groups"] else ""
        self.var_stats.set(f"{s['files']:,} files · {s['folders']:,} folders · "
                           f"{_fmt_size(s['bytes'])}{dups}")

    def _ai_enriched_count(self) -> int:
        try:
            row = self.db.execute("SELECT COUNT(*) AS n FROM docmeta WHERE summary IS NOT NULL AND summary != ''").fetchone()
            return int(row["n"]) if row else 0
        except Exception:
            return 0

    def refresh_tags(self) -> None:
        if not hasattr(self, "tag_chips"):
            return
        for w in self.tag_chips.winfo_children():
            w.destroy()
        tags = self.db.list_tags()[:14]
        if not tags:
            tk.Label(self.tag_chips, text="no smart tags yet — ask the AI Assistant to tag files",
                     bg=T.BG, fg=T.FAINT, font=font(8)).pack(side="left")
            return
        tk.Label(self.tag_chips, text="Tags: ", bg=T.BG, fg=T.MUTED, font=font(8)).pack(side="left")
        for tag, n in tags:
            b = tk.Label(self.tag_chips, text=f"#{tag} ({n})", bg=T.BADGE_BG, fg=T.TEXT,
                         font=font(8), padx=6, pady=2, cursor="hand2")
            b.pack(side="left", padx=2)
            b.bind("<Button-1>", lambda e, t=tag: self._filter_by_tag(t))

    def _filter_by_tag(self, tag: str) -> None:
        rows = self.db.files_with_tag(tag, 500)
        tree = self.results_tree
        for iid in tree.get_children():
            tree.delete(iid)
        self._results_items.clear()
        self._results_meta = {"offset": 0}
        self._fill(list(rows))
        self.results_label.set(f"{len(rows):,} files tagged “{tag}” (semantic tagging courtesy of your AI model)")
        self.show_page("Search")

    def refresh_duplicates(self) -> None:
        if not hasattr(self, "dup_tree"):
            return
        tree = self.dup_tree
        for iid in tree.get_children():
            tree.delete(iid)
        for g in self.db.duplicate_groups():
            wasted = human_size(g["bytes"] * (g["n"] - 1))
            tree.insert("", "end", values=((g["hash"] or "")[:10] + "…", g["n"], wasted, g["example"]))

    def refresh_ai_badge(self) -> None:
        if not hasattr(self, "var_ai_badge"):
            return
        try:
            p = self.ai.provider()
            who = p.name
            if p.is_local:
                who += " · offline"
            self.var_ai_badge.set(f"AI: {who}")
        except Exception:
            self.var_ai_badge.set("AI: not configured")

    def _dup_detail(self) -> None:
        if self.dup_tree.selection():
            DuplicatesDialog(self.master, self.db)

    def _refresh_favorites(self) -> None:
        pass

    def _ai_enrich_all_gone(self) -> None:
        pass

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------
    def _semantic_toggle(self) -> None:
        on = self.semantic_var.get()
        if on and not self.ai.provider().has_embeddings:
            self.semantic_var.set(False)
            messagebox.showwarning(
                "Semantic search",
                "The active AI model has no embedding model configured.\n"
                "Enable one in Settings → AI Models (e.g. nomic-embed-text for Ollama,\n"
                "or text-embedding-3-small for OpenAI).")
            return
        self.cfg.patch(semantic_default=on)
        self.cfg_store.save(self.cfg)
        if self.term_var.get().strip():
            self.do_search()

    def refresh_empty_if_needed(self) -> None:
        if not self.term_var.get().strip():
            self.results_empty_state()

    def do_search(self) -> None:
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
        return {"All": "all", "Name": "name", "Path": "path", "Content": "content"}.get(
            self.mode_var.get(), "all")

    def _run_search(self, reset: bool) -> None:
        term = self.term_var.get().strip()
        mode = self._mode()
        category = self._category()
        if not term:
            self.results_empty_state()
            return
        if self.semantic_var.get():
            self._run_semantic_search(term, category)
            return
        expr = build_match(term, mode)
        if expr is None:
            self.results_empty_state("Type at least one character to search.")
            return
        try:
            self._do_fts_search(expr, term, mode, category, reset=reset)
        except Exception as exc:
            self._log("error", f"Search failed: {exc}")

    def _run_semantic_search(self, term: str, category: str) -> None:
        self.var_status.set("Embedding your query…")
        tree = self.results_tree

        def work():
            try:
                pairs = self.ai.semantic_search(term, top_k=30)
                rows = []
                for doc_id, score in pairs:
                    row = self.db.get(doc_id)
                    if row:
                        d = dict(row)
                        d["__score"] = score
                        rows.append(d)
                self._enqueue({"kind": "sem_result", "rows": rows, "term": term})
            except Exception as exc:
                self._enqueue({"kind": "ai_fail", "msg": f"Semantic search: {exc}"})

        threading.Thread(target=work, daemon=True).start()

    def _do_fts_search(self, expr, term, mode, category, reset=True, limit_page=300) -> None:
        tree = self.results_tree
        if reset:
            for iid in tree.get_children():
                tree.delete(iid)
            self._results_meta = {"offset": 0, "expr": expr, "term": term, "mode": mode,
                                  "category": category, "total": 0}
            name_results = self.db.name_search(term, category=category, limit=limit_page, offset=0) \
                if mode in ("all", "name") else []
            fts_results = self.db.search(expr, category=category, limit=limit_page, offset=0)
            merged = self._merge_results(name_results, fts_results)
            self._results_meta["total"] = len(merged)
            self._results_meta["offset"] = max(0, len(merged) - 1)
            self._fill(merged)
        else:
            offset = self._results_meta.get("offset", 0) + 1
            more = self.db.search(expr, category=category, limit=limit_page, offset=offset)
            self._results_meta["offset"] += len(more)
            self._fill(more)

    @staticmethod
    def _merge_results(name_results, fts_results):
        seen: set[int] = set()
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
            if len(tree.get_children()) >= 5000:
                break
            meta = None
            try:
                meta = self.db.get_meta(int(r["id"]))
            except Exception:
                meta = None
            star = ""
            if meta and meta["favorite"]:
                star = "★ "
            if "__score" in r:
                name_col = f"{star}{r['name']}  ({float(r['__score']):.2f})"
            else:
                name_col = f"{star}{r['name']}"
            tree.insert("", "end", iid=iid,
                        values=(name_col, category_label(r["category"]), human_size(r["size"]),
                                time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"])),
                                r["path"]))
        total = self._results_meta.get("total", 0)
        self.results_label.set(
            f"{len(tree.get_children()):,} result{'s' if len(tree.get_children()) != 1 else ''} "
            f"for “{self.term_var.get()}”")
        if len(tree.get_children()) >= 300:
            self.results_label.set(self.results_label.get() + "  (use Load more for additional matches)")

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
            meta = None
            try:
                meta = self.db.get_meta(int(r["id"]))
            except Exception:
                meta = None
            star = "★ " if (meta and meta["favorite"]) else ""
            tree.insert("", "end", iid=iid,
                        values=(star + r["name"], category_label(r["category"]), human_size(r["size"]),
                                time.strftime("%Y-%m-%d %H:%M", time.localtime(r["mtime"])), r["path"]))
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
    # result interactions + insights
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
        meta = self.db.get_meta(doc_id) or {}
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.var_insight_name.set("" if not row else str(row["name"]))
        sub = ""
        if row:
            sub = (f"{category_label(row['category'])} · {human_size(row['size'])} · "
                   f"modified {time.strftime('%Y-%m-%d %H:%M', time.localtime(row['mtime']))}")
            if row["dup_id"]:
                sub += " · DUPLICATE"
            if meta:
                tags = [t for t in str(meta.get("tags") or "").split(",") if t.strip()]
                if tags:
                    sub += "\nTags: " + ", ".join(f"#{t}" for t in tags[:10])
                if meta.get("favorite"):
                    sub += "\n★ favorite"
        self.var_insight_sub.set(sub)
        if not row:
            self.preview.configure(state="disabled")
            return
        terms = []
        if self.term_var.get().strip():
            terms = _re.findall(r"[\w@.\-\u0080-\uffff]+", self.term_var.get().strip())
        content = (row["content"] or "").strip()
        if not content:
            if row["status"] == "no_content" or row["status"] == "ocr_unavailable":
                self.preview.insert("end", "No extractable text (indexed by name/size only).", "dim")
                if row["status"] == "ocr_unavailable":
                    self.preview.insert("end", "\n\nTip: install Tesseract OCR to read image text.", "dim")
                self.preview.insert("end", "\n\n" + row["path"], "head")
            else:
                self.preview.insert("end", "(folder)", "dim")
                listing = row["content"] or ""
                if listing:
                    self.preview.insert("end", listing)
        else:
            if meta and meta.get("summary"):
                self.preview.insert("end", "⟢ AI SUMMARY\n", "head")
                self.preview.insert("end", str(meta["summary"]) + "\n\n", "good")
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

    def _toggle_fav(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        for fid in ids:
            self.db.toggle_favorite(fid)
        self._log("info", "Marked favorite" if self.db.get_meta(ids[0]).get("favorite") else "Unmarked favorite")
        if self.term_var.get().strip():
            self.do_search()
        else:
            self.results_empty_state()

    def _translate_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        fid = ids[0]
        row = self.db.get(fid)
        if not row or not row.get("content"):
            messagebox.showinfo(APP_NAME, "This item has no extractable text to translate.")
            return
        TranslateDialog(self.master, self.translator, self.db, fid, row["content"],
                        default_lang=self.cfg.default_lang, msg_q=self.q)

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

    # AI insights
    def _ai_summary(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        doc_id = ids[0]
        self.var_ai_state.set("Writing summary…")

        def work():
            try:
                summary = self.ai.summarize_doc(doc_id)
                self._enqueue({"kind": "ai_sum_done", "doc_id": doc_id, "text": summary})
            except Exception as exc:
                self._enqueue({"kind": "ai_fail", "msg": f"Summary: {exc}"})

        threading.Thread(target=work, daemon=True).start()

    def _ai_show_summary(self, doc_id: int, summary: str) -> None:
        self.var_ai_state.set("")
        self._log("ok", "AI summary generated")
        self._show_preview(doc_id)

    def _show_related_list(self, rows) -> None:
        if not rows:
            self.var_ai_state.set("No embeddings available — run AI enrichment or configure an embedding model.")
            return
        dlg = tk.Toplevel(self.master)
        dlg.title("Related files (semantic similarity)")
        dlg.geometry("680x380")
        dlg.configure(bg=T.CARD)
        dlg.transient(self.master)
        cols = ("File", "Similarity", "Path")
        tree = ttk.Treeview(dlg, columns=cols, show="headings", selectmode="browse")
        for c, w in zip(cols, (240, 90, 280)):
            tree.heading(c, text=c)
            tree.column(c, width=w, anchor="w", stretch=(c == "Path"))
        tree.pack(fill="both", expand=True, padx=10, pady=10)
        for d in rows:
            tree.insert("", "end", values=(d["name"], f"{float(d['__score']):.2f}", d["path"]))
        tree.bind("<Double-1>", lambda e: self._related_open(tree))

        def close_dlg():
            dlg.destroy()
        make_button(dlg, "Close", close_dlg).pack(pady=(0, 10))
        dlg.grab_set()

    def _related_open(self, tree) -> None:
        sel = tree.selection()
        if not sel:
            return
        path = str(tree.item(sel[0], "values")[2])
        if os.path.exists(path):
            open_path(path)

    def _edit_notes(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        doc_id = ids[0]
        meta = self.db.get_meta(doc_id) or {}
        dlg = tk.Toplevel(self.master)
        dlg.title("Notes")
        dlg.geometry("520x300")
        dlg.configure(bg=T.CARD)
        dlg.transient(self.master)
        txt = tk.Text(dlg, bg=T.PANEL, fg=T.TEXT, insertbackground=T.TEXT,
                      font=("Segoe UI", 10), relief="flat", highlightthickness=0)
        txt.pack(fill="both", expand=True, padx=10, pady=10)
        txt.insert("1.0", str(meta.get("notes") or ""))

        def save():
            self.db.upsert_meta(doc_id, notes=txt.get("1.0", "end").strip())
            dlg.destroy()
            self._log("info", "Notes saved.")

        make_button(dlg, "Save notes", save, "Accent.TButton").pack(pady=(0, 10))
        dlg.grab_set()

    def _show_related(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        doc_id = ids[0]

        def work():
            try:
                pairs = self.ai.related(doc_id, top_k=8)
                rows = []
                for rid, score in pairs:
                    row = self.db.get(rid)
                    if row:
                        d = dict(row)
                        d["__score"] = score
                        rows.append(d)
                self._enqueue({"kind": "related_done", "rows": rows})
            except Exception as exc:
                self._enqueue({"kind": "ai_fail", "msg": f"Related: {exc}"})

        threading.Thread(target=work, daemon=True).start()

    def _folder_report_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        row = self.db.get(ids[0])
        if not row:
            return
        root = os.path.dirname(str(row["path"]))

        def work():
            try:
                report = self.ai.folder_report(root)
                self._enqueue({"kind": "ai_answer", "text": f"Report for {root}\n\n{report}", "cites": []})
            except Exception as exc:
                self._enqueue({"kind": "ai_fail", "msg": f"Report: {exc}"})

        threading.Thread(target=work, daemon=True).start()

    def _render_cites(self, cites: list) -> None:
        for iid in self.cite_tree.get_children():
            self.cite_tree.delete(iid)
        for name, path in cites:
            self.cite_tree.insert("", "end", iid=path or name, values=(path or name,))

    # ------------------------------------------------------------------
    # watch + settings + tray
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
            self.db, self.cfg, on_changed=lambda root: self._enqueue({"kind": "watch_changed", "root": root}))
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

    def _start_tray(self) -> None:
        if getattr(self, "tray", None) and self.tray.visible:
            return
        from .tray import tray_available
        if not tray_available():
            return
        self.tray = Tray(self._tray_open, self._tray_scan, self._tray_exit)
        if not self.tray.start():
            self.tray = None
        else:
            self._log("info", "Tray icon active — DropLens keeps running silently.")

    def _tray_open(self) -> None:
        if self.master.state() == "withdrawn":
            self.master.deiconify()
        self.master.lift()
        self.master.focus_force()

    def _tray_scan(self) -> None:
        roots = self._roots_all()
        if roots:
            self.scanner.scan_roots(roots, incremental=True)

    def _tray_exit(self) -> None:
        self._real_close()

    def open_settings(self) -> None:
        def on_saved(cfg):
            self.cfg = cfg
            if self._resolve_db_path(cfg) != self.db_path:
                self._reopen_db()
                self._log("info", "Data directory changed — library reopened.")
            self.scanner.config = cfg
            self.auto_watch_var.set(bool(cfg.get("watch_enabled")))
            if cfg.get("watch_enabled"):
                self._start_watcher()
            else:
                self._stop_watcher()
            if cfg.get("tray_enabled", True) and not (self.tray and self.tray.visible):
                self._start_tray()
            self.refresh_stats()
            self.refresh_ai_badge()
            self._log("info", "Settings saved.")

        SettingsDialog(self.master, self.cfg_store, on_saved)

    def open_ai_settings(self) -> None:
        def saved():
            self.cfg_store.save(self.cfg)
            self.refresh_ai_badge()

        AISettingsDialog(self.master, self.cfg, saved)

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
        tray = getattr(self, "tray", None)
        if self.cfg.get("close_to_tray", False) and tray and tray.visible:
            self.master.withdraw()
            self._notify("DropLens", "Still running in the background — double-click the tray icon to reopen.")
            return
        self._real_close()

    def _real_close(self) -> None:
        self._stop_watcher()
        if getattr(self, "tray", None):
            self.tray.stop()
        try:
            self.db.close()
        finally:
            self.master.destroy()