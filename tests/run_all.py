"""Run the DropLens test suite with one command and one exit status.

The suites are plain assert scripts (no pytest dependency) so they also run on a
machine where nothing but the runtime requirements are installed. This runner
gives CI and release builds a single, predictable gate:

    python tests/run_all.py                # every suite, GUI included
    python tests/run_all.py --no-gui       # skip test_gui (needs a display)
    python tests/run_all.py --verbose      # stream each suite's output
    python tests/run_all.py --filter retrieval
    python tests/run_all.py --fail-fast

Exit codes: 0 all selected suites passed, 1 at least one failed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# Ordered cheapest-first so a broken change fails fast.
SUITES: list[tuple[str, str, bool]] = [
    # (module, what it proves, needs a GUI/display)
    ("test_engine", "scan, search, dedup, extraction, pagination, CSV export", False),
    ("test_export", "CSV/TXT export paths + cascade delete", False),
    ("test_retrieval", "chunking, hybrid RRF, citations, invalidation, AI queue", False),
    ("test_ai", "summaries, tags, embeddings, RAG answers (stubbed provider)", False),
    ("test_extensions", "plugin discovery, extractor precedence, hooks, isolation", False),
    ("test_tools", "read-only tools, agent loop bound, provider converters", False),
    ("test_gateway", "embedded Respan Gateway over loopback", False),
    ("test_gui", "window boots, indexes, searches, closes", True),
]


def _display_available() -> bool:
    if sys.platform in ("win32", "darwin"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _run(module: str, verbose: bool, timeout: float | None) -> tuple[bool, float, str]:
    path = os.path.join(HERE, f"{module}.py")
    if not os.path.exists(path):
        return False, 0.0, f"missing suite file: {path}"
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    started = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", path],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, time.time() - started, f"timed out after {timeout}s"
    elapsed = time.time() - started
    output = (proc.stdout or "") + (proc.stderr or "")
    if verbose or proc.returncode != 0:
        print(output.rstrip())
    return proc.returncode == 0, elapsed, output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the DropLens test suites.")
    parser.add_argument("--no-gui", action="store_true", help="skip the suites that need a display")
    parser.add_argument("--only-gui", action="store_true", help="run only the display suites")
    parser.add_argument("--verbose", "-v", action="store_true", help="stream suite output")
    parser.add_argument("--fail-fast", "-x", action="store_true", help="stop at the first failure")
    parser.add_argument("--filter", default="", help="substring matched against the suite name")
    parser.add_argument("--timeout", type=float, default=600.0, help="per-suite timeout in seconds")
    args = parser.parse_args(argv)

    selected = SUITES
    if args.filter:
        selected = [s for s in selected if args.filter.lower() in s[0].lower()]
    if args.only_gui:
        selected = [s for s in selected if s[2]]
    elif args.no_gui:
        selected = [s for s in selected if not s[2]]

    display = _display_available()
    skipped: list[str] = []
    runnable: list[tuple[str, str]] = []
    for module, what, needs_display in selected:
        if needs_display and not display:
            skipped.append(module)
            continue
        runnable.append((module, what))

    if not runnable and not skipped:
        print("No suites matched.")
        return 1

    print(f"DropLens test run — {len(runnable)} suite(s), python {sys.version.split()[0]}")
    print("-" * 72)

    failures: list[tuple[str, str]] = []
    total = 0.0
    for module, what in runnable:
        ok, elapsed, output = _run(module, args.verbose, args.timeout)
        total += elapsed
        status = "PASS" if ok else "FAIL"
        print(f"  {status}  {module:<18} {elapsed:6.2f}s  {what}")
        if not ok:
            failures.append((module, output))
            if args.fail_fast:
                break

    print("-" * 72)
    for module in skipped:
        print(f"  SKIP  {module:<18}          (no display — run on Windows or with DISPLAY set)")
    print(f"Total {total:.2f}s · {len(runnable) - len(failures)} passed · "
          f"{len(failures)} failed · {len(skipped)} skipped")

    if failures:
        print("\nFailures:")
        for module, output in failures:
            print(f"\n=== {module} ===")
            print((output or "(no output)").rstrip()[-4000:])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
