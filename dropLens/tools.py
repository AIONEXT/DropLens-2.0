"""Tool surface: JSON-schema functions the assistant (or any client) may call.

A tool is a name, a description, a JSON schema for its arguments, and a handler
that receives the parsed arguments. Tools are what turn the assistant from a
single-shot summariser into something that can look things up, open a document
and check its own work.

The built-ins are strictly read-only views over the catalogue: an assistant can
never mutate the library through a tool. Extensions add their own with
``api.tool(...)`` — see :mod:`dropLens.extensions`.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Optional

from .engine.chunk import PAGE_MARK
from .engine.search import build_match

MAX_RESULT_CHARS = 8_000     # one tool result handed back to the model
MAX_READ_CHARS = 6_000       # one read_document window
MAX_SNIPPET = 400

_EMPTY_SCHEMA: dict[str, Any] = {
    "type": "object", "properties": {}, "additionalProperties": False,
}


def _schema(properties: dict[str, Any], required: Optional[list[str]] = None) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object", "properties": properties, "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _int(args: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        value = int(args.get(key, default))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _snippet(text: Any, limit: int = MAX_SNIPPET) -> str:
    return " ".join(str(text or "").split())[:limit]


def _row_keys(row: Any) -> set[str]:
    try:
        return set(row.keys())
    except AttributeError:  # pragma: no cover - dict-like fallback
        return set(row or {})


@dataclass
class Tool:
    """One callable exposed to a model."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    source: str = "builtin"

    def spec(self) -> dict[str, Any]:
        """Provider-neutral function schema."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters or dict(_EMPTY_SCHEMA),
        }

    def call(self, arguments: Any) -> tuple[str, Any]:
        """Run the handler. Returns ``(text_for_model, parsed_result_or_None)``.

        A failing tool is reported to the model as an error payload instead of
        raising: the loop must be able to recover and try something else.
        """
        args = arguments
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}
        try:
            result = self.handler(args)
        except Exception as exc:  # noqa: BLE001 - tool faults are data, not crashes
            result = {"error": f"{type(exc).__name__}: {exc}"}
            text = json.dumps(result, ensure_ascii=False)
            return text[:MAX_RESULT_CHARS], None
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        return text[:MAX_RESULT_CHARS], result

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tool {self.name} from {self.source}>"


class Toolbox:
    """An ordered, de-duplicated set of tools."""

    def __init__(self, tools: Iterable[Tool] = ()):
        self._tools: dict[str, Tool] = {}
        self.add(tools)

    def register(self, tool: Tool) -> bool:
        if not isinstance(tool, Tool) or not tool.name:
            return False
        if tool.name in self._tools:
            return False
        self._tools[tool.name] = tool
        return True

    def add(self, tools: Iterable[Tool]) -> int:
        count = 0
        for tool in tools or ():
            count += 1 if self.register(tool) else 0
        return count

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def tools(self) -> list[Tool]:
        return list(self._tools.values())

    def specs(self) -> list[dict[str, Any]]:
        return [t.spec() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __iter__(self) -> Iterator[Tool]:
        return iter(self._tools.values())

    def __contains__(self, name: object) -> bool:
        return name in self._tools


# ---------------------------------------------------------------------------
# Built-in read-only tools
# ---------------------------------------------------------------------------
def library_tools(catalog: Any) -> list[Tool]:
    """Tools backed by the catalogue. Safe for any caller, including plugins."""

    def search_library(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "")).strip()
        limit = _int(args, "limit", 8, 1, 25)
        category = str(args.get("category", "") or "")
        expr = build_match(query)
        if not expr:
            return {"results": [], "note": "the query contained no searchable tokens"}
        hits: list[dict[str, Any]] = []
        try:
            rows = catalog.search_chunks(expr, limit=limit, category=category)
        except Exception:  # noqa: BLE001 - chunks are optional (library not embedded)
            rows = []
        for row in rows:
            hits.append({
                "chunk_id": int(row["chunk_id"]),
                "doc_id": int(row["doc_id"]),
                "name": str(row["name"]),
                "path": str(row["path"]),
                "page": row["page"],
                "snippet": _snippet(row["text"]),
            })
        if hits:
            return {"results": hits, "granularity": "chunk",
                    "cite_as": "[#chunk_id]"}
        files: list[dict[str, Any]] = []
        for row in catalog.search(expr, category=category, limit=limit):
            keys = _row_keys(row)
            files.append({
                "doc_id": int(row["id"]),
                "name": str(row["name"]),
                "path": str(row["path"]),
                "category": str(row["category"] or ""),
                "size": int(row["size"] or 0),
                "snippet": _snippet(row["content"]) if "content" in keys else "",
            })
        return {"results": files, "granularity": "file",
                "note": "no chunk index yet for these files; snippets may be empty"}

    def read_document(args: dict[str, Any]) -> dict[str, Any]:
        row = _resolve_document(catalog, args)
        if row is None:
            return {"error": "document not found in the index"}
        content = str(row["content"] if "content" in _row_keys(row) else "")
        start = _int(args, "start", 0, 0, max(0, len(content)))
        length = _int(args, "length", MAX_READ_CHARS, 200, MAX_READ_CHARS)
        end = min(len(content), start + length)
        page = content.count(PAGE_MARK, 0, start) + 1
        return {
            "doc_id": int(row["id"]),
            "name": str(row["name"]),
            "path": str(row["path"]),
            "page": page,
            "start_char": start,
            "end_char": end,
            "total_chars": len(content),
            "next_start": end if end < len(content) else None,
            "text": content[start:end],
        }

    def library_stats(_args: dict[str, Any]) -> dict[str, Any]:
        stats = catalog.stats()
        counts = catalog.job_counts()
        return {
            "files": stats.get("files", 0),
            "folders": stats.get("folders", 0),
            "bytes": stats.get("bytes", 0),
            "duplicate_groups": stats.get("dup_groups", 0),
            "categories": [{"category": c, "files": n, "bytes": b}
                           for c, n, b in stats.get("by_category", [])],
            "chunks": catalog.chunk_count(),
            "chunk_vectors": catalog.chunk_vector_count(),
            "ai_enriched": catalog.ai_enriched_count(),
            "ai_jobs": counts,
        }

    def list_documents(args: dict[str, Any]) -> dict[str, Any]:
        limit = _int(args, "limit", 20, 1, 100)
        category = str(args.get("category", "") or "")
        folder = str(args.get("folder", "") or "")
        rows = (catalog.files_under(folder, limit=limit) if folder
                else catalog.recent_files(limit=limit * 4))
        out: list[dict[str, Any]] = []
        for row in rows:
            cat = str(row["category"] or "")
            if category and cat.lower() != category.lower():
                continue
            out.append({
                "doc_id": int(row["id"]), "name": str(row["name"]),
                "path": str(row["path"]), "category": cat,
                "size": int(row["size"] or 0),
                "modified": row["mtime"] if "mtime" in _row_keys(row) else None,
            })
            if len(out) >= limit:
                break
        return {"documents": out, "count": len(out)}

    return [
        Tool(
            name="search_library",
            description=("Search the indexed library. Prefer this before answering. "
                         "Returns matching passages with a chunk_id to cite as [#id], "
                         "or whole files when the library has no passage index yet."),
            parameters=_schema({
                "query": {"type": "string", "description": "keywords, names or a question"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                "category": {"type": "string", "description": "optional category filter"},
            }, ["query"]),
            handler=search_library,
        ),
        Tool(
            name="read_document",
            description=("Read a window of one document's extracted text. Use after "
                         "search_library when the snippet is not enough."),
            parameters=_schema({
                "doc_id": {"type": "integer", "description": "id returned by search_library"},
                "path": {"type": "string", "description": "absolute path, if the id is unknown"},
                "name": {"type": "string", "description": "file name, if unique in the library"},
                "start": {"type": "integer", "minimum": 0, "description": "character offset"},
                "length": {"type": "integer", "minimum": 200, "maximum": MAX_READ_CHARS},
            }),
            handler=read_document,
        ),
        Tool(
            name="library_stats",
            description="Catalogue totals, categories and AI queue state.",
            parameters=_schema({}),
            handler=library_stats,
        ),
        Tool(
            name="list_documents",
            description="List recently indexed documents, optionally filtered by folder or category.",
            parameters=_schema({
                "folder": {"type": "string"},
                "category": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            }),
            handler=list_documents,
        ),
    ]


def _resolve_document(catalog: Any, args: dict[str, Any]) -> Optional[Any]:
    """Find one file row from doc_id, path or name — in that order of trust."""
    doc_id = args.get("doc_id")
    if doc_id not in (None, ""):
        try:
            row = catalog.get(int(doc_id))
        except (TypeError, ValueError):
            row = None
        if row is not None:
            return row
    path = str(args.get("path", "") or "").strip()
    if path:
        row = catalog.path_meta(path) or catalog.path_meta(os.path.abspath(path))
        if row is not None:
            return row
        resolved = catalog.resolve_id(path)
        if resolved:
            return catalog.get(int(resolved))
    name = str(args.get("name", "") or "").strip()
    if name:
        return catalog.by_name(os.path.basename(name))
    return None
