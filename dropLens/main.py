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


def _set_app_user_model_id() -> None:
    """Give Windows a stable identity so the taskbar groups the app correctly.

    Without this the window inherits the interpreter's identity (or none at
    all when frozen), which is why unscheduled apps get a generic taskbar entry
    and lose their jump list between launches.
    """
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "AIONEXT.DropLens.Desktop"
        )
    except Exception:  # pragma: no cover - non-Windows or restricted shell
        pass


def _skip_onboarding() -> bool:
    return os.environ.get("DROPLENS_SKIP_ONBOARD", "") in ("1", "true", "yes")


def _warmup_requested() -> bool:
    return "--warmup" in sys.argv[1:]


def _warmup() -> int:
    """Pre-import the heavy modules and open the catalogue, then exit.

    The installer launches the app once with ``--warmup`` so the first real
    start is fast and antivirus has already scanned the payload. Doing the work
    here (instead of starting a hidden full window, which is what the flag used
    to trigger) makes the flag honest and cheap.
    """
    from dropLens import resources

    logger = resources.setup_logging()
    try:
        # The imports are the point: these are the modules whose first import
        # dominates a cold start, and running them once lets the OS cache the
        # DLLs and lets real-time AV finish scanning the payload.
        from dropLens.config import ConfigStore
        from dropLens.engine.db import Catalog
        from dropLens.engine.extract import extract_content  # noqa: F401

        cfg = ConfigStore().get()
        custom = cfg.get("data_dir")
        db_path = os.path.join(custom, "catalog.db") if custom else resources.db_path()
        catalog = Catalog(db_path)
        schema_version = catalog.schema_version
        catalog.close()
    except Exception:
        logger.warning("warmup failed (the app will still start):\n%s", traceback.format_exc())
        return 0

    # The UI toolkit is warmed separately: on a machine without Tk the core work
    # above is still worth keeping, and a missing display must never turn the
    # post-install step into an error dialog.
    try:
        from dropLens.ui import mainwindow  # noqa: F401 - heaviest first import
    except Exception:
        logger.info("warmup complete (schema v%s); UI toolkit not loadable here", schema_version)
        return 0
    logger.info("warmup complete (schema v%s)", schema_version)
    return 0


def main() -> int:
    _enable_dpi_awareness()
    _set_app_user_model_id()
    from dropLens import APP_VERSION, resources
    logger = resources.setup_logging()
    if _warmup_requested():
        return _warmup()
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
        from dropLens.ai import gateway as ai_gateway
        ai_gateway.stop()  # stop the embedded loopback gateway, if it started
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