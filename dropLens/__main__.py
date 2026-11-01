"""Entry point: ``python -m dropLens``.

No arguments (or GUI flags) start the desktop app; any sub-command is handled
by the headless CLI in :mod:`dropLens.cli`.
"""
import sys

_GUI_FLAGS = {"", "--gui", "-g", "--app"}


def _run() -> int:
    argv = sys.argv[1:]
    first = argv[0] if argv else ""
    if first in _GUI_FLAGS:
        from .main import main as gui_main
        return int(gui_main() or 0)
    from .cli import main as cli_main
    return int(cli_main(argv) or 0)


if __name__ == "__main__":
    sys.exit(_run())
