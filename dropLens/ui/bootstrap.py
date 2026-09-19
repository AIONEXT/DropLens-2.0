"""Splash + first-run onboarding wizard."""
from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from . import icon
from .theme import (ACCENT, ACCENT2, BG, CARD, FAINT, MUTED, OK, PANEL, RAISED,
                    TEXT, apply_ttk, font, make_button)


def show_splash(root: tk.Tk, on_frame: Callable[[int], None]) -> tk.Toplevel:
    """Big branded splash shown while the app boots; returns the splash window."""
    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    splash.configure(bg=BG)
    splash.attributes("-topmost", True)
    width, height = 470, 320
    x = splash.winfo_screenwidth() // 2 - width // 2
    y = splash.winfo_screenheight() // 2 - height // 2
    splash.geometry(f"{width}x{height}+{x}+{y}")

    img = icon.app_icon(150)
    try:
        from PIL import ImageTk
        photo = ImageTk.PhotoImage(img)
    except Exception:
        photo = None
    if photo:
        pic = tk.Label(splash, image=photo, bg=BG)
        pic.image = photo
        pic.pack(pady=(38, 6))
    tk.Label(splash, text="DropLens", bg=BG, fg=TEXT, font=("Segoe UI", 26, "bold")).pack()
    tk.Label(splash, text="Drop anything. Scan everything. Find it instantly.",
             bg=BG, fg=MUTED, font=font(10)).pack()
    bar = ttk.Progressbar(splash, mode="determinate", length=300, maximum=100,
                          style="Accent.Horizontal.TProgressbar")
    bar.pack(pady=(26, 0))

    def update(value: int) -> None:
        try:
            bar["value"] = value
        except tk.TclError:
            pass
        on_frame(value)

    splash.update()
    return splash


def dismiss_splash(splash: Optional[tk.Toplevel]) -> None:
    if splash is not None:
        try:
            splash.destroy()
        except tk.TclError:
            pass


PRESETS = [
    ("My Documents", lambda: os.path.expanduser("~/Documents")),
    ("Downloads", lambda: os.path.expanduser("~/Downloads")),
    ("Desktop", lambda: os.path.expanduser("~/Desktop")),
    ("Pictures", lambda: os.path.expanduser("~/Pictures")),
    ("Music", lambda: os.path.expanduser("~/Music")),
    ("Videos", lambda: os.path.expanduser("~/Videos")),
]


class Onboarding(tk.Toplevel):
    """First-run wizard: big icon intro, silent scan presets, AI opt-in.

    Emits ``on_finish(preset_paths: list[str], ai_offline: bool)``.
    """

    def __init__(self, parent: tk.Tk, on_finish: Callable[[list[str], bool], None]):
        super().__init__(parent)
        self.on_finish = on_finish
        self.selected: dict[str, tk.BooleanVar] = {}
        self.ai_offline = tk.BooleanVar(value=True)

        self.title("Welcome to DropLens")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        w, h = 620, 560
        self.geometry(f"{w}x{h}+{parent.winfo_screenwidth()//2 - w//2}"
                      f"+{parent.winfo_screenheight()//2 - h//2}")
        self.grab_set()

        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=34, pady=(30, 6))
        img = icon.app_icon(72)
        try:
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(img)
        except Exception:
            photo = None
        if photo:
            pic = tk.Label(header, image=photo, bg=BG)
            pic.image = photo
            pic.pack(side="left")
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left", padx=18)
        tk.Label(titles, text="Welcome to DropLens", bg=BG, fg=TEXT,
                 font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(titles, text="Step 1 of 2 — choose what to organize silently in the background",
                 bg=BG, fg=MUTED, font=font(9)).pack(anchor="w")

        body = tk.Frame(self, bg=PANEL, highlightbackground=RAISED, highlightthickness=1)
        body.pack(fill="both", expand=True, padx=34, pady=18)
        tk.Label(body, text="Computers best suited for silent scans", bg=PANEL, fg=MUTED,
                 font=font(9, True)).pack(anchor="w", padx=18, pady=(16, 6))
        for label, fn in PRESETS:
            var = tk.BooleanVar(value=os.path.isdir(fn()))
            self.selected[label] = var
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", padx=18, pady=3)
            tk.Checkbutton(row, text=label, variable=var, bg=PANEL, fg=TEXT,
                           activebackground=PANEL, activeforeground=TEXT,
                           selectcolor=CARD, font=font(11), anchor="w",
                           highlightthickness=0).pack(side="left", fill="x", expand=True)
            sub = tk.Label(row, text=fn() if os.path.isdir(fn()) else "not present on this PC",
                           bg=PANEL, fg=FAINT, font=font(8))
            sub.pack(side="right")

        ai_sec = tk.Frame(body, bg=PANEL)
        ai_sec.pack(fill="x", padx=18, pady=(10, 6))
        tk.Checkbutton(ai_sec, text="I want to use a local AI model (Ollama/LM Studio) — "
                                    "100% offline, no account needed",
                       variable=self.ai_offline, bg=PANEL, fg=TEXT,
                       activebackground=PANEL, activeforeground=TEXT,
                       selectcolor=CARD, font=font(10), highlightthickness=0).pack(anchor="w")
        tk.Label(body, text=("You can connect any cloud model or OpenAI-compatible API later "
                             "under Settings → AI. Nothing is sent unless you turn it on."),
                 bg=PANEL, fg=FAINT, font=font(8)).pack(anchor="w", padx=34)

        foot = tk.Frame(self, bg=BG)
        foot.pack(fill="x", padx=34, pady=(6, 24))
        tk.Label(foot, text="Your files are never modified. DropLens only reads, indexes and enriches.",
                 bg=BG, fg=MUTED, font=font(9)).pack(side="left")
        make_button(foot, "Start organizing  \u2192", self._finish,
                    "Accent.TButton").pack(side="right")

    def _finish(self) -> None:
        paths = [fn() for _label, fn in PRESETS if self.selected[_label].get()
                 and os.path.isdir(fn())]
        self.on_finish(paths, self.ai_offline.get())
        self.destroy()


def list_preset_homes() -> list[str]:
    return [fn() for _label, fn in PRESETS if os.path.isdir(fn())]