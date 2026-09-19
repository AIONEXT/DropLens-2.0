"""DropLens AI service — high-level features built on the provider clients.

Every public method is synchronous and meant to be invoked on a worker
thread; results are delivered back through the app's thread-safe queue.
Methods raise ``AINotConfigured`` / ``AIConnectionError`` which the UI maps
to friendly messages.
"""
from __future__ import annotations

import math
import struct
import threading
import time
from typing import Callable, Optional

from ..engine.db import Catalog
from ..engine.search import build_match, tokenize
from .client import AIConnectionError, AINotConfigured, chat, embed
from .providers import AIConfig, AIProvider

SYSTEM_SUMMARY = (
    "You are DropLens, a meticulous data organizer. Write a concise, factual "
    "summary (3-6 short sentences) of the document content. Highlight the "
    "main topic, key entities, dates and numbers. Do not invent facts that "
    "are not in the text."
)
SYSTEM_TAG = (
    "You are DropLens. From the document content, produce a short comma "
    "separated list of 3-7 lowercase English tags (keywords) that describe "
    "this document for organizing purposes. Reply with only the tags, "
    "no prose, no numbering."
)
SYSTEM_ANSWER = (
    "You are DropLens, a deep data assistant. Answer the user question using "
    "only the provided file excerpts. Cite the source file at the end of "
    "each claim as [file: name]. If the excerpts do not contain the answer, "
    "say that you could not find it in the indexed files."
)
SYSTEM_REPORT = (
    "You are DropLens. Produce a structured executive report about this "
    "folder based on the file list and contents. Use headings, bullets and "
    "short paragraphs. Be concrete and useful."
)

MAX_CONTENT = 90_000  # chars given to a model for summaries / answering
TRUNC_NOTE = 4_000


class AIService:
    def __init__(self, catalog: Catalog, config: Callable[[], AIConfig], name: str = "ai"):
        self.catalog = catalog
        self._config = config
        self.name = name
        self._lock = threading.RLock()
        self._busy: set[str] = set()

    # --- provider ---------------------------------------------------------
    def provider(self) -> AIProvider:
        cfg = self._config()
        p = cfg.active_provider()
        if not p:
            raise AINotConfigured("No AI provider configured.")
        return p

    def active_name(self) -> str:
        cfg = self._config()
        p = cfg.active_provider()
        return p.name if p else ""

    def is_configured(self) -> bool:
        try:
            return bool(self.provider().model)
        except AINotConfigured:
            return False

    # --- helpers ----------------------------------------------------------
    def _busy_guard(self, key: str, fn: Callable[[], str]) -> str:
        with self._lock:
            if key in self._busy:
                return f"[{self.name}] task already running…"
            self._busy.add(key)
        try:
            return fn()
        finally:
            with self._lock:
                self._busy.discard(key)

    @staticmethod
    def clip(text: Optional[str], limit: int = MAX_CONTENT) -> str:
        t = text or ""
        if len(t) <= limit:
            return t
        tail = t[:limit]
        cut = max(tail.rfind(" "), tail.rfind("\n"))
        return tail[: cut if cut > limit // 2 else limit]

    def _doc_context(self, doc_id: int) -> tuple[str, str, str]:
        row = self.catalog.get(doc_id)
        if not row:
            raise AIConnectionError("Document no longer in the index.")
        name = str(row["name"]) if "name" in row.keys() else ""
        content = self.clip(str(row["content"] or "") if "content" in row.keys() else "") or (
            f"[File {name} has no extractable text. It is {row['ext'] or ''} of "
            f"{row['size'] or 0} bytes.]"
        )
        return name, content, str(row["path"] or "")

    # --- feature methods ------------------------------------------------
    def summarize_doc(self, doc_id: int) -> str:
        def run() -> str:
            meta = self.catalog.get_meta(doc_id)
            if meta and meta["summary"]:
                return str(meta["summary"])
            name, content, _ = self._doc_context(doc_id)
            p = self.provider()
            summary = chat(p, SYSTEM_SUMMARY,
                           f"Document: {name}\n\nContent:\n{content}",
                           temperature=0.2, max_tokens=600)
            self.catalog.upsert_meta(doc_id, summary=summary,
                                     summary_lang="en", ai_status="summarized")
            return summary
        return self._busy_guard(f"sum:{doc_id}", run)

    def auto_tag(self, doc_id: int) -> list[str]:
        def run() -> list[str]:
            meta = self.catalog.get_meta(doc_id)
            if meta and meta["tags"]:
                return [t.strip() for t in str(meta["tags"]).split(",") if t.strip()]
            _, content, _ = self._doc_context(doc_id)
            p = self.provider()
            raw = chat(p, SYSTEM_TAG, f"Document content:\n{self.clip(content, 22000)}",
                       temperature=0.0, max_tokens=120)
            tags = [t.strip().lstrip("#").lower()
                    for t in raw.replace("\n", ",").split(",") if t.strip()]
            tags = list(dict.fromkeys(tags))[:7]
            if tags:
                self.catalog.upsert_meta(doc_id, tags=",".join(tags), ai_status="tagged")
            return tags
        return self._busy_guard(f"tag:{doc_id}", run)

    def embed_doc(self, doc_id: int) -> bool:
        def run() -> str:
            meta = self.catalog.get_meta(doc_id)
            if meta and meta["embedding"]:
                return "ok"
            name, content, _ = self._doc_context(doc_id)
            self._embed_store(doc_id, self.clip(f"{name}\n{content}", MAX_CONTENT // 2))
            return "ok"
        self._busy_guard(f"emb:{doc_id}", run)
        return True

    def _embed_store(self, doc_id: int, text: str) -> None:
        vec = embed(self.provider(), [text])[0]
        self.catalog.upsert_meta(doc_id, embedding=_pack(vec))

    @staticmethod
    def _require_embeddings(provider: AIProvider) -> None:
        if not provider.has_embeddings:
            raise AIConnectionError(
                f"{provider.name} has no embedding model configured — semantic search "
                "is unavailable. Pick an embedding model or use a local embedding model."
            )

    def embed_query(self, text: str) -> list[float]:
        p = self.provider()
        self._require_embeddings(p)
        return embed(p, [text])[0]

    def semantic_search(self, query: str, top_k: int = 12) -> list[tuple[int, float]]:
        """Return [(doc_id, score), ...] sorted by cosine similarity."""
        p = self.provider()
        self._require_embeddings(p)
        q = self.embed_query(query)
        rows = self.catalog.embedding_rows()
        scored: list[tuple[float, int]] = []
        for doc_id, blob, _name in rows:
            vec = _unpack(blob)
            if len(vec) != len(q):
                continue
            s = _cosine(q, vec)
            if not math.isnan(s):
                scored.append((s, doc_id))
        scored.sort(reverse=True)
        return [(d, s) for s, d in scored[:top_k]]

    def related(self, doc_id: int, top_k: int = 8) -> list[tuple[int, float]]:
        """Find the most similar indexed files using stored embeddings."""
        meta = self.catalog.get_meta(doc_id)
        if not meta or not meta["embedding"]:
            return []
        base = _unpack(meta["embedding"])
        scored: list[tuple[float, int]] = []
        for other_id, blob, _name in self.catalog.embedding_rows():
            if other_id == doc_id:
                continue
            vec = _unpack(blob)
            if len(vec) == len(base):
                scored.append((_cosine(base, vec), other_id))
        scored.sort(reverse=True)
        return [(d, s) for s, d in scored[:top_k]]

    def ask(self, question: str) -> tuple[str, list[tuple[str, str]]]:
        """RAG-style assistant: answers from the library, citing files."""
        cited_out: list[tuple[str, str]] = []

        def run() -> str:
            excerpts = self._retrieve(question)[:6]
            if not excerpts:
                answer = chat(self.provider(), SYSTEM_ANSWER,
                              f"Question: {question}\n\n(no matching files found in the index)",
                              temperature=0.2, max_tokens=900)
                return answer + "\n\n(No indexed files matched this question.)"
            blocks = "\n\n---\n\n".join(
                f"[file: {name}]\n{self.clip(chunk, MAX_CONTENT // 6)}"
                for name, chunk in excerpts
            )
            raw = chat(self.provider(), SYSTEM_ANSWER,
                       f"Question: {question}\n\nIndexed file excerpts:\n{blocks}",
                       temperature=0.2, max_tokens=1200)
            if not raw:
                raise AIConnectionError("Empty model answer.")
            for name, _chunk in excerpts:
                if f"[file: {name}]" in raw:
                    cited_out.append((name, self._path_for(name)))
            return raw

        answer = self._busy_guard(f"ask:{hash(question)}", run)
        return answer, cited_out

    def _path_for(self, name: str) -> str:
        row = self.catalog.by_name(name)
        return str(row["path"]) if row else ""

    def _retrieve(self, question: str) -> list[tuple[str, str]]:
        """Hybrid retrieval: embeddings (preferred) + keyword FTS."""
        hits: list[tuple[str, str]] = []
        seen: set[int] = set()

        def push(doc_id: int) -> None:
            row = self.catalog.get(doc_id)
            if row and doc_id not in seen:
                seen.add(doc_id)
                hits.append((str(row["name"]), str(row["content"] or "") or ""))

        try:
            for doc_id, _score in self.semantic_search(question, top_k=8):
                push(doc_id)
        except AIConnectionError:
            pass
        if len(hits) < 3:
            query = build_match(question)
            for row in self.catalog.search(query, limit=8):
                push(int(row["id"]))
        return hits

    def folder_report(self, root: str) -> str:
        def run() -> str:
            limit = 60
            rows = self.catalog.files_under(root, limit=limit)
            if not rows:
                return "No indexed files found in this folder — run a scan first."
            total = sum(int(r["size"] or 0) for r in rows)
            cats: dict[str, int] = {}
            for r in rows:
                c = str(r["category"] or "Other")
                cats[c] = cats.get(c, 0) + 1
            listing = "\n".join(
                f"  {r['name']} ({_human(r['size'] or 0)}) [{r['category']}]" for r in rows[:limit]
            )
            text = f"Folder: {root}\nFiles indexed: {len(rows)}  Total size: {_human(total)}\n" \
                   f"Categories: {', '.join(f'{k}={v}' for k, v in sorted(cats.items()))}\n\n{listing}"
            return chat(self.provider(), SYSTEM_REPORT, text, temperature=0.3, max_tokens=1000)
        return self._busy_guard(f"rep:{root}", run)

    def index_ai(self, progress: Callable[[int, int], None],
                 stop: Callable[[], bool] = lambda: False) -> str:
        """Generate summaries/tags/embeddings for every indexed file."""
        def run() -> str:
            rows = self.catalog.files_for_ai()
            total = len(rows)
            done = 0
            summary_n = tag_n = emb_n = 0
            for r in rows:
                if stop():
                    return "Stopped by user."
                doc_id = int(r["id"])
                done += 1
                if done % 5 == 0 or done == total:
                    progress(done, total)
                try:
                    has_sum = bool((self.catalog.get_meta(doc_id) or {}).get("summary"))
                    if not has_sum:
                        self.summarize_doc(doc_id)
                        summary_n += 1
                    try:
                        if not (self.catalog.get_meta(doc_id) or {}).get("tags"):
                            self.auto_tag(doc_id)
                            tag_n += 1
                    except AIConnectionError:
                        pass
                    try:
                        self.embed_doc(doc_id)
                        emb_n += 1
                    except AIConnectionError:
                        pass
                except AIConnectionError as exc:
                    return f"AI index interrupted: {exc}"
            progress(done, total)
            return (f"AI enrichment complete: {summary_n} summaries, {tag_n} tagged, "
                    f"{emb_n} embedded across {total} files.")
        return self._busy_guard(f"index-ai", run)


def _human(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return dot / (na * nb)