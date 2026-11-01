"""Headless command line for DropLens.

The engine is already UI-free, so exposing it costs almost nothing and turns
the index into something scripts, CI jobs and other tools can use:

    python -m dropLens search "gdpr retention" --json
    python -m dropLens stats
    python -m dropLens scan "C:/Users/me/Documents"
    python -m dropLens ask "what does the contract say about renewal?"
    python -m dropLens ask --tools "compare the two invoices"   # tool-calling agent
    python -m dropLens tools              # built-in + plugin tools
    python -m dropLens extensions         # loaded plugins and their errors
    python -m dropLens export --out catalogue.csv
    python -m dropLens egress            # what left this machine
    python -m dropLens jobs --status

With no arguments the GUI starts (see ``dropLens/__main__.py``).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Optional


def _open_catalog(_args: argparse.Namespace | None = None):
    """Open config + catalogue the same way the GUI does."""
    from . import net, resources
    from .config import ConfigStore
    from .engine.db import Catalog

    store = ConfigStore()
    cfg = store.get()
    custom = cfg.get("data_dir")
    path = os.path.join(custom, "catalog.db") if custom else resources.db_path()
    catalog = Catalog(path)
    net.configure(cfg.offline_only, sink=catalog.log_egress_entry)
    return cfg, store, catalog


def _print_out(text: str = "") -> None:
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.write(text + "\n")
    else:  # pragma: no cover - exotic embedding
        print(text)


def _row_to_dict(row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_search(args: argparse.Namespace) -> int:
    from .engine.search import build_match

    _cfg, _store, catalog = _open_catalog(args)
    term = args.term
    expr = build_match(term, args.mode)
    if expr is None:
        _print_out("No searchable tokens in the query.")
        return 2
    category = args.category or ""
    rows = catalog.search(expr, category=category, limit=args.limit)
    if args.mode in ("all", "name"):
        seen = {int(r["id"]) for r in rows}
        extra = [r for r in catalog.name_search(term, category=category, limit=args.limit)
                 if int(r["id"]) not in seen]
        rows = list(rows) + extra
    if args.json:
        _print_out(json.dumps([_row_to_dict(r) for r in rows], ensure_ascii=False, indent=2,
                              default=str))
    else:
        for r in rows:
            page = f"{r['name']}\t{r['path']}"
            _print_out(page)
        _print_out(f"({len(rows)} result{'s' if len(rows) != 1 else ''})")
    catalog.close()
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    _cfg, _store, catalog = _open_catalog(args)
    stats = catalog.stats()
    jobs = catalog.job_counts()
    payload = {
        **{k: v for k, v in stats.items() if k != "by_category"},
        "categories": [{"category": c, "files": n, "bytes": b} for c, n, b in stats["by_category"]],
        "ai_enriched": catalog.ai_enriched_count(),
        "chunks": catalog.chunk_count(),
        "chunk_vectors": catalog.chunk_vector_count(),
        "ai_jobs": jobs,
        "schema_version": catalog.schema_version,
    }
    if args.json:
        _print_out(json.dumps(payload, indent=2, default=str))
    else:
        _print_out(f"Files    : {payload['files']:,}")
        _print_out(f"Folders  : {payload['folders']:,}")
        _print_out(f"Size     : {payload['bytes']:,} bytes")
        _print_out(f"Duplicate: {payload['dup_groups']:,} groups / {payload['dup_files']:,} files")
        _print_out(f"AI       : {payload['ai_enriched']:,} enriched · "
                   f"{payload['chunks']:,} chunks · {payload['chunk_vectors']:,} vectors")
        if jobs:
            _print_out("Queue    : " + ", ".join(f"{k}={v}" for k, v in sorted(jobs.items())))
    catalog.close()
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    from .config import Config
    from .engine.scan import ScanManager

    cfg, _store, catalog = _open_catalog(args)
    cfg = cfg if isinstance(cfg, Config) else Config()
    paths = [os.path.abspath(p) for p in args.paths]
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        for p in missing:
            _print_out(f"missing: {p}")
        return 2
    for p in paths:
        root = p if os.path.isdir(p) else (os.path.dirname(p) or os.getcwd())
        catalog.add_root(root)
    manager = ScanManager(catalog, cfg, on_progress=_progress_printer(args.quiet))
    manager.scan_roots(paths, incremental=not args.full)
    while manager.is_busy():
        time.sleep(0.1)
    catalog.close()
    return 0


def _progress_printer(quiet: bool):
    last = {"t": 0.0}

    def cb(msg: dict) -> None:
        if quiet:
            return
        kind = msg.get("kind")
        if kind == "log":
            _print_out(f"  {msg.get('level', 'info')}: {msg.get('msg', '')}")
        elif kind == "done":
            res = msg.get("result")
            if res:
                _print_out(f"✓ {res.root}: {res.indexed:,} indexed · {res.skipped:,} unchanged · "
                           f"{res.deleted:,} removed · {res.errors} errors · {res.duration:.1f}s")
        elif kind == "file":
            now = time.time()
            if now - last["t"] > 1.0:
                last["t"] = now
                if not hasattr(sys.stdout, "isatty") or sys.stdout.isatty():
                    sys.stdout.write(f"\r  scanning… {msg.get('count', 0):,} entries")
                    sys.stdout.flush()

    return cb


def cmd_ask(args: argparse.Namespace) -> int:
    from .ai.client import AIConnectionError, AINotConfigured
    from .ai.svc import AIService

    cfg, _store, catalog = _open_catalog(args)
    from .extensions import load_from_config
    load_from_config(cfg)
    service = AIService(catalog, config=lambda: cfg.ai_config(), settings=lambda: cfg)
    steps: list[dict] = []
    try:
        if args.tools:
            answer, cites, steps = service.ask_agent(args.question, max_steps=args.steps)
        else:
            answer, cites = service.ask(args.question)
    except (AINotConfigured, AIConnectionError) as exc:
        _print_out(f"AI unavailable: {exc}")
        catalog.close()
        return 3
    if args.json:
        payload = {"answer": answer, "citations": cites}
        if args.tools:
            payload["tool_calls"] = steps
        _print_out(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        _print_out(answer)
        if cites:
            _print_out("")
            _print_out("Sources:")
            for cite in cites:
                _print_out(f"  {cite['label']}  ({cite['path']})")
    catalog.close()
    return 0


def _tool_rows(cfg, catalog) -> list[Any]:
    from .extensions import default as extensions, load_from_config
    from .tools import Toolbox, library_tools
    load_from_config(cfg)
    box = Toolbox(library_tools(catalog))
    box.add(extensions().tools())
    return list(box)


def cmd_tools(args: argparse.Namespace) -> int:
    cfg, _store, catalog = _open_catalog(args)
    tools = _tool_rows(cfg, catalog)
    if args.json:
        _print_out(json.dumps([t.spec() for t in tools], indent=2, ensure_ascii=False))
    else:
        for tool in tools:
            _print_out(f"{tool.name:<18} {tool.source:<10} {tool.description}")
        _print_out(f"({len(tools)} tools available to the assistant)")
    catalog.close()
    return 0


def cmd_extensions(args: argparse.Namespace) -> int:
    from . import extensions
    from .extensions import load_from_config

    cfg, _store, catalog = _open_catalog(args)
    if args.reload:
        extensions.reset()
    load_from_config(cfg)
    summary = extensions.default().summary()
    if args.json:
        _print_out(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        plugins = summary["plugins"]
        if not plugins:
            _print_out(f"No plugins loaded from {extensions.plugin_dir(cfg.data_dir or '') or '(no data dir)'}")
        for plugin in plugins:
            state = "ok" if plugin["ok"] else f"failed: {plugin['error']}"
            version = f" v{plugin['version']}" if plugin["version"] else ""
            _print_out(f"{plugin['name']}{version} — {state}")
            if plugin["description"]:
                _print_out(f"  {plugin['description']}")
        if summary["extractors"]:
            _print_out("Extractors: " + ", ".join(
                f".{e['extension']} ({e['source']})" for e in summary["extractors"])
            )
        if summary["tools"]:
            _print_out("Tools     : " + ", ".join(
                f"{t['name']} ({t['source']})" for t in summary["tools"]))
        if summary["hooks"]:
            _print_out("Hooks     : " + ", ".join(
                f"{k}={v}" for k, v in sorted(summary["hooks"].items())))
    catalog.close()
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    from .engine.export import export_catalogue

    _cfg, _store, catalog = _open_catalog(args)
    out = os.path.abspath(args.out)
    count = export_catalogue(catalog, out)
    _print_out(f"Exported {count:,} rows → {out}")
    catalog.close()
    return 0


def cmd_egress(args: argparse.Namespace) -> int:
    _cfg, _store, catalog = _open_catalog(args)
    if args.clear:
        catalog.clear_egress()
        _print_out("Egress log cleared.")
        catalog.close()
        return 0
    rows = catalog.egress_rows(limit=args.limit)
    if args.json:
        _print_out(json.dumps([_row_to_dict(r) for r in rows], indent=2, default=str))
    elif not rows:
        _print_out("Nothing has left this machine.")
    else:
        for r in rows:
            stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"]))
            _print_out(f"{stamp}  {r['kind']:<7} {r['chars']:>8} chars  {r['target']}")
    catalog.close()
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    from .ai.svc import AIService

    cfg, _store, catalog = _open_catalog(args)
    if args.queue:
        queued = catalog.enqueue_ai_jobs(retry_failed=args.retry_failed)
        _print_out(f"{queued:,} jobs queued.")
    if args.run:
        service = AIService(catalog, config=lambda: cfg.ai_config(), settings=lambda: cfg)
        _print_out(service.run_jobs(limit=args.limit))
    if args.status or (not args.run and not args.queue):
        counts = catalog.job_counts()
        _print_out(", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "queue is empty")
    catalog.close()
    return 0


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="droplens",
        description="DropLens headless tools — query the local index without the GUI.",
    )
    sub = parser.add_subparsers(dest="command")

    def _sub(name: str, help_text: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name, help=help_text)
        child.add_argument("--json", action="store_true", help="machine readable output")
        return child

    p = _sub("search", "full-text search (BM25 over names, paths, content)")
    p.add_argument("term")
    p.add_argument("--mode", default="all", choices=("all", "name", "path", "content"))
    p.add_argument("--category", default="")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_search)

    p = _sub("stats", "library, chunk and queue statistics")
    p.set_defaults(func=cmd_stats)

    p = _sub("scan", "index folders (incremental unless --full)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--full", action="store_true", help="re-extract every file")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_scan)

    p = _sub("ask", "ask the assistant (cites chunk ids)")
    p.add_argument("question")
    p.add_argument("--tools", action="store_true",
                   help="let the model call catalogue tools before answering")
    p.add_argument("--steps", type=int, default=4, help="max tool rounds (default 4)")
    p.set_defaults(func=cmd_ask)

    p = _sub("tools", "list the tools the assistant can call")
    p.set_defaults(func=cmd_tools)

    p = _sub("extensions", "list loaded plugins, extractors, tools and hooks")
    p.add_argument("--reload", action="store_true", help="re-import plugins from disk")
    p.set_defaults(func=cmd_extensions)

    p = _sub("export", "write the whole catalogue as CSV")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_export)

    p = _sub("egress", "inspect or clear the outbound request log")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--clear", action="store_true")
    p.set_defaults(func=cmd_egress)

    p = _sub("jobs", "inspect / run the AI enrichment queue")
    p.add_argument("--queue", action="store_true", help="queue missing artifacts")
    p.add_argument("--run", action="store_true", help="run queued jobs")
    p.add_argument("--status", action="store_true")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--retry-failed", action="store_true")
    p.set_defaults(func=cmd_jobs)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        parser.print_help()
        return 0
    # allow "droplens --json search q" as well as "droplens search q --json"
    global_json = False
    if argv and argv[0] == "--json":
        global_json = True
        argv = argv[1:]
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    if global_json:
        args.json = True
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:  # pragma: no cover - interactive
        _print_out("interrupted")
        return 130
    except BrokenPipeError:  # pragma: no cover - piping into head
        return 0
