"""DropLens application entry point.

Boot sequence:
  1. enable HiDPI, set up logging
  2. show the branded splash window while the workspace loads
  3. open the main window; on a brand-new install show the onboarding wizard
  4. kick off silent preset scans chosen during onboarding

Run from source:    python launcher.py   (or  python -m dropLens)
Run when frozen:    the launcher is the PyInstaller entry script.
"""
from __future__ import annotations

import ctypes
import os
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


def _skip_onboarding() -> bool:
    return os.environ.get("DROPLENS_SKIP_ONBOARD", "") in ("1", "true", "yes")


def main() -> int:
    _enable_dpi_awareness()
    from dropLens import APP_VERSION, resources
    logger = resources.setup_logging()
    logger.info("DropLens %s starting (data dir: %s)", APP_VERSION, resources.data_dir())
    try:
        from tkinterdnd2 import TkinterDnD
        from dropLens.config import ConfigStore
        from dropLens.ui.bootstrap import show_splash
        from dropLens.ui.mainwindow import DropLensApp

        root = TkinterDnD.Tk()
        root.withdraw()

        splash = show_splash(root, on_frame=lambda v: None)
        app = DropLensApp(root)
        app.boot_refresh()

        from dropLens.ui.bootstrap import dismiss_splash, Onboarding
        dismiss_splash(splash)

        root.deiconify()

        store = ConfigStore()
        cfg = store.get()
        skip = _skip_onboarding() or os.environ.get("DROPLENS_TEST", "") == "1"
        if (not cfg.get("onboarded", False)) and not skip:
            preset_paths: list[str] = []

            def on_finish(paths, ai_offline):
                preset_paths.extend(paths)
                if ai_offline:
                    local = cfg.ai_config()
                    # prefer a local provider when the user opted into offline AI
                    for p in local.providers:
                        if p.is_local:
                            local.active = p.name
                            break
                    cfg.set_ai_config(local)
                    store.save(cfg)
                cfg.patch(onboarded=True)
                store.save(cfg)
                app.cfg = store.get()
                app.refresh_ai_badge()
                if preset_paths:
                    app.add_paths(preset_paths)
                root.focus_force()

            Onboarding(root, on_finish)
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