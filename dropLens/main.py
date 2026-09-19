"""DropLens application entry point.

Run from source:    python launcher.py   (or  python -m dropLens)
Run when frozen:    the launcher is the PyInstaller entry script.
"""
from __future__ import annotations

import ctypes
import sys
import traceback


def _enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def main() -> int:
    _enable_dpi_awareness()
    from dropLens import APP_VERSION, resources
    logger = resources.setup_logging()
    logger.info("DropLens %s starting (data dir: %s)", APP_VERSION, resources.data_dir())
    try:
        from tkinterdnd2 import TkinterDnD
        from dropLens.ui.mainwindow import DropLensApp

        root = TkinterDnD.Tk()
        root.title("DropLens")
        app = DropLensApp(root)
        root.mainloop()
        logger.info("DropLens exiting cleanly")
        return 0
    except Exception:
        logger.critical("Fatal error:\n%s", traceback.format_exc())
        try:
            from tkinter import messagebox
            messagebox.showerror(
                "DropLens — unexpected error",
                "An unexpected error occurred. Details were written to:\n"
                + resources.log_path() + "\n\n" + traceback.format_exc()[-1500:],
            )
        except Exception:
            print(traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())