"""Example DropLens plugin: index CSV/TSV with their header row, and add a tool.

Copy this file into ``<data_dir>/plugins/`` to try it. It demonstrates all
three extension points:

  * an extractor that replaces the plain-text dump for ``.csv``/``.tsv`` so the
    header is repeated on every row — search and embeddings then see
    "region=north revenue=42" instead of a bare comma line;
  * a tool the assistant can call to list a file's columns;
  * a hook that records how many rows were indexed.

Standard library only. No network, no writing outside the data directory.
"""
from __future__ import annotations

import csv
import os

NAME = "CSV tables"
VERSION = "1.0"
DESCRIPTION = "Indexes CSV/TSV with column names attached to every row"

MAX_ROWS = 20_000
MAX_CELL = 200

_HEADERS: dict[str, list[str]] = {}   # path -> column names, reused by the tool


def _dialect_for(path: str) -> dict:
    """csv.reader keyword arguments — a mapping, not the Sniffer's dialect object."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tsv":
        return {"delimiter": "\t"}
    try:  # sniff ; vs , once, and fall back to a comma
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            sample = fh.read(4096)
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        return {"delimiter": getattr(dialect, "delimiter", ",")}
    except (csv.Error, OSError):
        return {"delimiter": ","}


def extract(path: str) -> str | None:
    """Return one ``column=value`` line per row, headed by the column names."""
    dialect = _dialect_for(path)
    try:
        with open(path, "r", encoding="utf-8", errors="replace", newline="") as fh:
            rows = csv.reader(fh, **dialect)
            header = next(rows, None)
            if not header:
                return None
            header = [(h or f"column{i + 1}").strip()[:MAX_CELL] for i, h in enumerate(header)]
            _HEADERS[path] = header
            lines = [f"Table {os.path.basename(path)} — columns: " + ", ".join(header)]
            for index, row in enumerate(rows, 1):
                if index > MAX_ROWS:
                    lines.append(f"[truncated after {MAX_ROWS} rows]")
                    break
                cells = []
                for name, value in zip(header, row):
                    text = (value or "").strip()
                    if text:
                        cells.append(f"{name}={text[:MAX_CELL]}")
                if cells:
                    lines.append(f"row {index}: " + " | ".join(cells))
            return "\n".join(lines) if len(lines) > 1 else None
    except (OSError, csv.Error, UnicodeError):
        return None


def register(api) -> None:
    api.register_extractor(("csv", "tsv"), extract, replace=True)

    @api.tool("csv_columns", description="List the column names of an indexed CSV/TSV file")
    def csv_columns(args):
        path = str(args.get("path", "") or "")
        columns = _HEADERS.get(path)
        if columns is None and path and os.path.isfile(path):
            extract(path)          # fills the cache
            columns = _HEADERS.get(path)
        if not columns:
            return {"error": "no columns recorded for that path"}
        return {"path": path, "columns": columns, "count": len(columns)}

    @api.on("document_indexed")
    def after_index(doc):
        # Hooks should be quick and harmless: this one only logs to the app.
        import logging
        logging.getLogger("droplens.plugin.csv").debug(
            "indexed %s (%s chars)", doc.get("name"), doc.get("chars"))
