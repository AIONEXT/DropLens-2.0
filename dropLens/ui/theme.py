"""Visual identity for DropLens — dark 'deep-space' theme and widget kit.

Shared palette + small helpers so the whole UI stays consistent and can be
retuned in one place.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

BG          = "#0d1117"
PANEL       = "#11161f"
CARD        = "#1a2130"
CARD_HOVER  = "#222b3d"
RAISED      = "#263045"
TEXT        = "#e6edf3"
MUTED       = "#8b98a9"
FAINT       = "#5b6777"
ACCENT      = "#3b82f6"
ACCENT_DARK = "#2563eb"
ACCENT2     = "#22d3ee"
LINK        = "#60a5fa"
OK          = "#34d399"
WARN        = "#fbbf24"
DANGER      = "#f87171"
BADGE_BG    = "#26324a"

FONT = "Segoe UI"


def font(size: int, bold: bool = False) -> tuple:
    return (FONT, size, "bold" if bold else "normal")


def apply_ttk(root: tk.Tk) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("TButton", font=font(10), padding=(14, 8),
                    background=RAISED, foreground=TEXT, borderwidth=0)
    style.map("TButton", background=[("active", CARD_HOVER), ("pressed", ACCENT_DARK)])
    style.configure("Accent.TButton", font=font(10, True), padding=(18, 9),
                    background=ACCENT, foreground="#ffffff", borderwidth=0)
    style.map("Accent.TButton", background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)])
    style.configure("Danger.TButton", font=font(10), padding=(14, 8),
                    background=DANGER, foreground="#ffffff", borderwidth=0)
    style.map("Danger.TButton", background=[("active", "#ef4444")])
    style.configure("Ghost.TButton", font=font(10), padding=(12, 8),
                    background=PANEL, foreground=TEXT, borderwidth=1, relief="solid")
    style.map("Ghost.TButton", background=[("active", CARD)])
    style.configure("TLabel", background=PANEL, foreground=TEXT, font=font(10))
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=font(9))
    style.configure("Card.TLabel", background=CARD, foreground=TEXT, font=font(10))
    style.configure("TLabelframe", background=PANEL, foreground=TEXT, borderwidth=0)
    style.configure("TLabelframe.Label", background=PANEL, foreground=MUTED, font=font(9, True))
    style.configure("TEntry", fieldbackground=CARD, foreground=TEXT, insertcolor=TEXT,
                    bordercolor=RAISED, lightcolor=RAISED, darkcolor=RAISED, padding=8)
    style.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=TEXT,
                    arrowcolor=TEXT, bordercolor=RAISED, padding=6)
    style.map("TCombobox", fieldbackground=[("readonly", CARD)])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(16, 8))
    style.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", TEXT)])
    style.configure("Vertical.TScrollbar", background=RAISED, troughcolor=PANEL,
                    arrowcolor=TEXT, bordercolor=PANEL, borderwidth=0)
    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT,
                    rowheight=28, borderwidth=0)
    style.map("Treeview", background=[("selected", ACCENT_DARK)], foreground=[("selected", "#ffffff")])
    style.configure("Accent.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=PANEL, bordercolor=PANEL, lightcolor=ACCENT,
                    darkcolor=ACCENT2)
    style.configure("TProgressbar", background=ACCENT, troughcolor=PANEL,
                    bordercolor=PANEL)
    root.option_add("*Font", font(10))


def make_button(parent: tk.Widget, text: str, command: Optional[Callable] = None,
                kind: str = "TButton") -> ttk.Button:
    return ttk.Button(parent, text=text,
                      command=command if command is not None else lambda: None,
                      style=kind)


def card(parent: tk.Widget, pad: int = 16) -> tk.Frame:
    f = tk.Frame(parent, bg=CARD, highlightbackground=RAISED, highlightthickness=1)
    inner = tk.Frame(f, bg=CARD)
    inner.pack(fill="both", expand=True, padx=pad, pady=pad)
    return inner


def card_outer(parent: tk.Widget) -> tk.Frame:
    return tk.Frame(parent, bg=CARD, highlightbackground=RAISED, highlightthickness=1)


def badge(parent: tk.Widget, text: str, color: str = ACCENT, fg: str = "#ffffff") -> tk.Label:
    lbl = tk.Label(parent, text=text, bg=color, fg=fg, font=font(8, True),
                   padx=8, pady=2)
    return lbl


def hline(parent: tk.Widget) -> tk.Frame:
    return tk.Frame(parent, bg=RAISED, height=1)