"""DropLens launcher — entry point for both source runs and the frozen .exe."""
import sys


def _main() -> int:
    from dropLens.main import main
    return main()


if __name__ == "__main__":
    sys.exit(_main())