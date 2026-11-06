"""Enforce the read-only guarantee mechanically.

DropLens tells users — and their security reviewers — that it never modifies,
moves, renames or deletes a scanned file. That promise holds today because no
write path exists, not because a policy says so. This script turns the property
into a build gate so it cannot erode one convenient helper at a time.

    python scripts/check_readonly.py            # human readable, exit 1 on a hit
    python scripts/check_readonly.py --json     # for CI logs / dashboards

Allowed by design: ``engine/export.py`` writes the file the user explicitly chose
as an export destination. Nothing else under ``engine/`` may open a path for
writing, delete, rename, move or copy it.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "dropLens", "engine")
PACKAGE = os.path.join(ROOT, "dropLens")

# A file allowed to write, and why.
ALLOWED_WRITERS = {
    "export.py": "writes only the CSV/TXT destination the user picked",
}

# (regex, explanation). Kept deliberately narrow so the gate stays trustworthy.
MUTATION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bos\.remove\s*\("), "os.remove"),
    (re.compile(r"\bos\.unlink\s*\("), "os.unlink"),
    (re.compile(r"\bos\.rename\s*\("), "os.rename"),
    (re.compile(r"\bos\.replace\s*\("), "os.replace"),
    (re.compile(r"\bos\.rmdir\s*\("), "os.rmdir"),
    (re.compile(r"\bos\.truncate\s*\("), "os.truncate"),
    (re.compile(r"\bshutil\.(move|rmtree|copy|copy2|copyfile|copytree)\s*\("), "shutil mutation"),
    (re.compile(r"\.write_text\s*\(|\.write_bytes\s*\("), "pathlib write"),
    (re.compile(r"\bPath\([^)]*\)\.unlink\s*\("), "pathlib unlink"),
]

WRITE_MODE = re.compile(r"""open\s*\((?P<args>[^)]*)\)""")
MODE_STRING = re.compile(r"""["'](?P<mode>[rwaxbt+]{1,4})["']""")

SHELL_TRUE = re.compile(r"shell\s*=\s*True")


def _write_mode_findings(path: str, text: str) -> list[dict[str, object]]:
    """Report ``open(...)`` calls that request a write mode."""
    findings: list[dict[str, object]] = []
    lines = text.splitlines()
    for match in WRITE_MODE.finditer(text):
        args = match.group("args")
        line_no = text.count("\n", 0, match.start()) + 1
        # single-line call: look for a mode literal in the argument list
        mode = None
        for m in MODE_STRING.finditer(args):
            if any(ch in m.group("mode") for ch in "wax+"):
                mode = m.group("mode")
        if mode is None:
            # multi-line call: inspect the following few lines for the mode
            for probe in lines[line_no: line_no + 4]:
                found = MODE_STRING.search(probe)
                if found and any(ch in found.group("mode") for ch in "wax+"):
                    mode = found.group("mode")
                    break
        if mode:
            findings.append({
                "file": os.path.relpath(path, ROOT),
                "line": line_no,
                "rule": f'open(..., "{mode}")',
                "code": match.group(0).strip().splitlines()[0][:140],
            })
    return findings


def scan() -> dict[str, list[dict[str, object]]]:
    findings: dict[str, list[dict[str, object]]] = {}
    for base, _dirs, files in os.walk(ENGINE):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            rel = os.path.relpath(path, os.path.join(ROOT, "dropLens"))
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            hits: list[dict[str, object]] = []
            if name not in ALLOWED_WRITERS:
                hits.extend(_write_mode_findings(path, text))
                for pattern, rule in MUTATION_PATTERNS:
                    for match in pattern.finditer(text):
                        hits.append({
                            "file": os.path.relpath(path, ROOT),
                            "line": text.count("\n", 0, match.start()) + 1,
                            "rule": rule,
                            "code": match.group(0).strip()[:140],
                        })
            if hits:
                findings[rel] = hits
    # shell=True anywhere in the package is a command-injection foot-gun
    for base, _dirs, files in os.walk(PACKAGE):
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(base, name)
            with open(path, "r", encoding="utf-8") as fh:
                text = fh.read()
            for match in SHELL_TRUE.finditer(text):
                rel = os.path.relpath(path, os.path.join(ROOT, "dropLens"))
                findings.setdefault(rel, []).append({
                    "file": os.path.relpath(path, ROOT),
                    "line": text.count("\n", 0, match.start()) + 1,
                    "rule": "subprocess shell=True",
                    "code": match.group(0),
                })
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify engine/ never mutates user files.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    findings = scan()
    total = sum(len(v) for v in findings.values())
    if args.json:
        print(json.dumps({"ok": total == 0, "findings": findings}, indent=2))
        return 0 if total == 0 else 1

    if not findings:
        print("read-only guarantee: OK — no write, delete, move or rename path under dropLens/engine/")
        for name, why in sorted(ALLOWED_WRITERS.items()):
            print(f"  allowed: engine/{name} — {why}")
        return 0

    print(f"read-only guarantee: {total} finding(s)\n")
    for rel, hits in sorted(findings.items()):
        print(f"  {rel}")
        for hit in hits:
            print(f"    line {hit['line']}: {hit['rule']}  →  {hit['code']}")
    print("\nIf a write is legitimate, add the file to ALLOWED_WRITERS with a reason.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
