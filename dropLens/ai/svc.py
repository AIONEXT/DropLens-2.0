"""DropLens AI service — summaries, tags, chunked hybrid retrieval, assistant.

Every public method is synchronous and meant to be invoked on a worker thread;
results are delivered back through the app's thread-safe queue. Methods raise
``AINotConfigured`` / ``AIConnectionError`` which the UI maps to friendly
messages.

Retrieval is *hybrid at chunk granularity*: BM25 over ``chunks_fts`` and cosine
over per-chunk vectors are fused with Reciprocal Rank Fusion, and the assistant
cites stable chunk ids (``[#17]``) that map back to ``(doc_id, page,
start_char)`` — never to a filename guess.
"""
from __future__ import annotations

import logging
import math
import re
import struct
import threading
from dataclasses import dataclass
from typing import Callable, Iterable, Optional, Sequence

from ..engine.chunk import chunk_text
from ..engine.db import Catalog, content_hash, row_get
from ..engine.search import build_match
from . import jobs as jobs_mod
from .client import AIConnectionError, AINotConfigured, chat, embed
from .providers import AIConfig, AIProvider

log = logging.getLogger(__name__)

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
    "only the provided excerpts. Every excerpt starts with its reference id, "
    "for example [#42] contract.pdf · p.3. Cite the excerpt you used at the end "
    "of each claim as [#42]. Never invent reference ids or facts that are not "
    "in the excerpts. If the excerpts do not contain the answer, say that you "
    "could not find it in the indexed files."
)
SYSTEM_REPORT = (
    "You are DropLens. Produce a structured executive report about this "
    "folder based on the file list and contents. Use headings, bullets and "
    "short paragraphs. Be concrete and useful."
)

MAX_CONTENT = 90_000          # chars given to a model for summaries
EMBED_MAX_CHARS = 8_000       # hard cap per chunk sent to an embedding model
RRF_K = 60                    # Reciprocal Rank Fusion constant
CITE_RE = re.compile(r"\[#(\d+)\]")
FILE_CITE_RE = re.compile(r"\[file:\s*([^\]]+)\]")

DEFAULT_SETTINGS = {
    "chunk_chars": 3200,
    "chunk_overlap": 0.15,
    "retrieve_candidates": 60,
    "retrieve_top": 8,
    "embed_batch": 32,
    "ai_retry_limit": 3,
}


@dataclass
class Hit:
    """One retrieved chunk with full provenance."""
    chunk_id: int
    doc_id: int
    name: str
    path: str
    page: int | None
    start_char: int
    end_char: int
    text: str
    score: float = 0.0

    def label(self) -> str:
        where = f" · p.{self.page}" if self.page else ""
        return f"[#{self.chunk_id}] {self.name}{where}"

    def cite(self) -> dict:
        snippet = " ".join(self.text.split())
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "label": self.label(),
            "name": self.name,
            "path": self.path,
            "page": self.page,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "snippet": snippet[:400],
            "score": round(float(self.score), 4),
        }


class AIService:
    def __init__(self, catalog: Catalog, config: Callable[[], AIConfig], name: str = "ai",
                 settings: Optional[Callable[[], object]] = None):
        self.catalog = catalog
        self._config = config
        self._settings = settings
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

    # --- settings ---------------------------------------------------------
    def setting(self, key: str):
        source = self._settings() if self._settings else None
        if source is None:
            return DEFAULT_SETTINGS.get(key)
        try:
            value = source.get(key, DEFAULT_SETTINGS.get(key))  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - config objects vary
            value = getattr(source, key, DEFAULT_SETTINGS.get(key))
        return DEFAULT_SETTINGS.get(key) if value is None else value

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
        name = str(row_get(row, "name", ""))
        content = self.clip(str(row_get(row, "content", "") or "")) or (
            f"[File {name} has no extractable text. It is {row_get(row, 'ext', '') or ''} of "
            f"{row_get(row, 'size', 0) or 0} bytes.]"
        )
        return name, content, str(row_get(row, "path", ""))

    def content_hash(self, doc_id: int) -> str:
        """Fingerprint of the document text currently in the index."""
        row = self.catalog.get(doc_id)
        if not row:
            return ""
        return content_hash(str(row_get(row, "content", "") or ""),
                            str(row_get(row, "name", "")),
                            int(row_get(row, "size", 0) or 0))

    def _is_fresh(self, meta, digest: str, field: str) -> bool:
        if not meta or not row_get(meta, field):
            return False
        stored = row_get(meta, "content_hash")
        return bool(stored) and stored == digest

    # --- feature methods --------------------------------------------------
    def summarize_doc(self, doc_id: int, force: bool = False) -> str:
        def run() -> str:
            digest = self.content_hash(doc_id)
            meta = self.catalog.get_meta(doc_id)
            if not force and self._is_fresh(meta, digest, "summary"):
                return str(row_get(meta, "summary", ""))
            name, content, _ = self._doc_context(doc_id)
            p = self.provider()
            summary = chat(p, SYSTEM_SUMMARY,
                           f"Document: {name}\n\nContent:\n{content}",
                           temperature=0.2, max_tokens=600)
            self.catalog.upsert_meta(doc_id, summary=summary, summary_lang="en",
                                     ai_status="summarized", content_hash=digest)
            return summary
        return self._busy_guard(f"sum:{doc_id}", run)

    def auto_tag(self, doc_id: int, force: bool = False) -> list[str]:
        def run() -> list[str]:
            digest = self.content_hash(doc_id)
            meta = self.catalog.get_meta(doc_id)
            if not force and self._is_fresh(meta, digest, "tags"):
                return [t.strip() for t in str(row_get(meta, "tags", "")).split(",") if t.strip()]
            _, content, _ = self._doc_context(doc_id)
            p = self.provider()
            raw = chat(p, SYSTEM_TAG, f"Document content:\n{self.clip(content, 22000)}",
                       temperature=0.0, max_tokens=120)
            tags = [t.strip().lstrip("#").lower()
                    for t in raw.replace("\n", ",").split(",") if t.strip()]
            tags = list(dict.fromkeys(tags))[:7]
            if tags:
                self.catalog.upsert_meta(doc_id, tags=",".join(tags), ai_status="tagged",
                                         content_hash=digest)
            return tags
        return self._busy_guard(f"tag:{doc_id}", run)

    # --- embeddings -------------------------------------------------------
    def embed_doc(self, doc_id: int, force: bool = False) -> bool:
        """Chunk the document and embed every chunk (batched), then store a doc vector."""
        def run() -> str:
            digest = self.content_hash(doc_id)
            meta = self.catalog.get_meta(doc_id)
            if (not force and self._is_fresh(meta, digest, "embedding")
                    and self._has_chunk_vectors(doc_id)):
                return "ok"
            name, content, _ = self._doc_context(doc_id)
            provider = self.provider()
            self._require_embeddings(provider)
            chunks = chunk_text(content,
                                target_chars=int(self.setting("chunk_chars") or 3200),
                                overlap=float(self.setting("chunk_overlap") or 0.15))
            if not chunks:
                raise AIConnectionError("This document has no extractable text to embed.")
            chunk_ids = self.catalog.replace_chunks(doc_id, chunks)
            self.catalog.commit()

            batch_size = max(1, int(self.setting("embed_batch") or 32))
            model = provider.embedding_model
            vectors: list[list[float]] = []
            for start in range(0, len(chunk_ids), batch_size):
                part = list(zip(chunk_ids[start:start + batch_size], chunks[start:start + batch_size]))
                texts = [self._embed_text(name, c.text) for _cid, c in part]
                out = embed(provider, texts)
                if len(out) != len(part):
                    raise AIConnectionError(
                        f"{provider.name} returned {len(out)} vectors for {len(part)} chunks.")
                for (chunk_id, _chunk), vec in zip(part, out):
                    if not vec:
                        continue
                    self.catalog.set_chunk_vector(chunk_id, doc_id, _pack(vec), model, len(vec))
                    vectors.append(vec)
                self.catalog.commit()

            if not vectors:
                raise AIConnectionError("The embedding model returned no vectors.")
            mean = _mean_normalized(vectors)
            self.catalog.upsert_meta(doc_id, embedding=_pack(mean), embed_model=model,
                                     embed_dim=len(mean), content_hash=digest,
                                     chunk_hash=digest, ai_status="embedded")
            return "ok"

        self._busy_guard(f"emb:{doc_id}", run)
        return True

    def _has_chunk_vectors(self, doc_id: int) -> bool:
        try:
            row = self.catalog.execute(
                "SELECT 1 FROM chunk_vectors WHERE doc_id=? LIMIT 1", (doc_id,)
            ).fetchone()
        except Exception:  # noqa: BLE001 - table missing on a very old DB
            return False
        return row is not None

    @staticmethod
    def _embed_text(name: str, text: str) -> str:
        body = (text or "")[:EMBED_MAX_CHARS]
        return f"{name}\n{body}" if name else body

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

    def semantic_search(self, query: str, top_k: int = 12,
                        category: str = "") -> list[tuple[int, float]]:
        """Document-level semantic search (best-matching chunk per document).

        ``category`` restricts candidates to files of that category, so the
        filter the keyword channel applies in SQL applies here too (H2).
        """
        p = self.provider()
        self._require_embeddings(p)
        allowed = self.catalog.file_ids(category)
        q = self.embed_query(query)
        rows = self.catalog.chunk_vector_rows()
        best: dict[int, float] = {}
        for _chunk_id, doc_id, score in _score_vectors(q, rows):
            if allowed is not None and doc_id not in allowed:
                continue
            if score > best.get(doc_id, -2.0):
                best[doc_id] = score
        if not best:
            for doc_id, blob, _name in self.catalog.embedding_rows():
                if allowed is not None and doc_id not in allowed:
                    continue
                vec = _unpack(blob)
                if len(vec) != len(q):
                    continue
                score = _cosine(q, vec)
                if not math.isnan(score):
                    best[doc_id] = max(best.get(doc_id, -2.0), score)
        ranked = sorted(best.items(), key=lambda kv: kv[1], reverse=True)
        return ranked[:top_k]

    def related(self, doc_id: int, top_k: int = 8) -> list[tuple[int, float]]:
        """Find the most similar indexed files using stored document vectors."""
        meta = self.catalog.get_meta(doc_id)
        if not meta or not row_get(meta, "embedding"):
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

    # --- retrieval --------------------------------------------------------
    def retrieve(self, question: str, top_k: int | None = None,
                 category: str = "", root: str = "") -> list[Hit]:
        """Hybrid chunk retrieval: BM25 + vector, fused with RRF."""
        limit = int(top_k or self.setting("retrieve_top") or 8)
        candidates = max(limit, int(self.setting("retrieve_candidates") or 60))
        # the FTS channel filters by category/root in SQL; the vector channel
        # loads raw embeddings, so it applies the same filter here (H2)
        allowed = self.catalog.file_ids(category, root)
        fts_rank: list[int] = []
        by_id: dict[int, dict] = {}

        expr = build_match(question)
        if expr:
            for row in self.catalog.search_chunks(expr, limit=candidates,
                                                  category=category, root=root):
                cid = int(row["chunk_id"])
                fts_rank.append(cid)
                by_id[cid] = {
                    "doc_id": int(row["doc_id"]), "name": str(row["name"]),
                    "path": str(row["path"]), "page": row["page"],
                    "start_char": int(row["start_char"]), "end_char": int(row["end_char"]),
                    "text": str(row["text"]),
                }

        vector_rank: list[int] = []
        try:
            p = self.provider()
            if p.has_embeddings:
                q = self.embed_query(question)
                rows = self.catalog.chunk_vector_rows()
                vector_rank = [cid for cid, doc, _s in _score_vectors(q, rows)
                               if allowed is None or doc in allowed][:candidates]
        except (AIConnectionError, AINotConfigured) as exc:
            log.info("vector channel unavailable: %s", exc)

        fused = _rrf([fts_rank, vector_rank])
        ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)[:limit]
        missing = [cid for cid, _s in ordered if cid not in by_id]
        if missing:
            for cid, row in self.catalog.chunks_by_ids(missing).items():
                by_id[cid] = {
                    "doc_id": int(row["doc_id"]), "name": str(row["name"]),
                    "path": str(row["path"]), "page": row["page"],
                    "start_char": int(row["start_char"]), "end_char": int(row["end_char"]),
                    "text": str(row["text"]),
                }
        hits: list[Hit] = []
        for cid, score in ordered:
            data = by_id.get(cid)
            if not data:
                continue
            hits.append(Hit(chunk_id=cid, score=score, **data))
        return hits

    # kept for callers that used the old private name
    _retrieve = retrieve

    def ask(self, question: str) -> tuple[str, list[dict]]:
        """Answer from the library, citing chunk ids that map back to offsets."""
        cited: list[dict] = []

        def run() -> str:
            provider = self.provider()
            hits = self.retrieve(question)
            if not hits:
                answer = chat(provider, SYSTEM_ANSWER,
                              f"Question: {question}\n\n(no matching files found in the index)",
                              temperature=0.2, max_tokens=900)
                return answer + "\n\n(No indexed files matched this question.)"
            budget = getattr(provider, "prompt_char_budget", 24_000)
            per_chunk = max(800, min(6_000, int(budget) // max(1, len(hits))))
            blocks = "\n\n---\n\n".join(
                f"{hit.label()}\n{self.clip(hit.text, per_chunk)}" for hit in hits
            )
            raw = chat(provider, SYSTEM_ANSWER,
                       f"Question: {question}\n\nIndexed file excerpts:\n{blocks}",
                       temperature=0.2, max_tokens=1200)
            if not raw:
                raise AIConnectionError("Empty model answer.")
            for cite in self._cites_from_answer(raw, hits):
                cited.append(cite)
            return raw

        answer = self._busy_guard(f"ask:{hash(question)}", run)
        return answer, cited

    # --- agentic answers (tool calling) -----------------------------------
    def toolbox(self, extra_tools: Optional[Iterable] = None):
        """Built-in catalogue tools plus whatever extensions registered."""
        from ..extensions import default as extensions
        from ..tools import Toolbox, library_tools
        box = Toolbox(library_tools(self.catalog))
        box.add(extensions().tools())
        if extra_tools:
            box.add(extra_tools)
        return box

    def ask_agent(self, question: str, extra_tools: Optional[Iterable] = None,
                  max_steps: int = 4) -> tuple[str, list[dict], list[dict]]:
        """Answer by letting the model call tools, then resolve its citations."""
        from . import agent

        found: list[dict] = []
        steps: list[dict] = []

        def run() -> str:
            provider = self.provider()
            result = agent.run(question, self.toolbox(extra_tools), provider,
                               max_steps=max_steps)
            steps.extend(result.steps)
            found.extend(self._cites_for_ids(result.chunk_ids, result.answer))
            return result.answer

        answer = self._busy_guard(f"agent:{hash(question)}", run)
        return answer, found, steps

    def _cites_for_ids(self, ids: Iterable[int], answer: str) -> list[dict]:
        """Turn chunk ids collected by tools into the same cite dicts ``ask`` returns."""
        clean = sorted({int(i) for i in ids if str(i).strip().lstrip("-").isdigit()})
        if not clean:
            return []
        rows = self.catalog.chunks_by_ids(clean)
        if not rows:
            return []
        cited = [int(m) for m in CITE_RE.findall(answer or "")]
        order = [cid for cid in dict.fromkeys(cited) if cid in rows] or sorted(rows)
        out: list[dict] = []
        for cid in order:
            row = rows[cid]
            page = row["page"]
            text = str(row["text"] or "")
            out.append({
                "chunk_id": cid,
                "doc_id": int(row["doc_id"]),
                "label": f"[#{cid}] {row['name']}" + (f" · p.{page}" if page else ""),
                "name": str(row["name"]),
                "path": str(row["path"]),
                "page": page,
                "start_char": int(row["start_char"]),
                "end_char": int(row["end_char"]),
                "snippet": " ".join(text.split())[:400],
                "score": 0.0,
            })
        return out

    def _cites_from_answer(self, answer: str, hits: Sequence[Hit]) -> list[dict]:
        found: dict[int, dict] = {}
        for match in CITE_RE.finditer(answer):
            cid = int(match.group(1))
            for hit in hits:
                if hit.chunk_id == cid and cid not in found:
                    found[cid] = hit.cite()
                    break
        if not found:  # models sometimes fall back to filenames — resolve without guessing ids
            for match in FILE_CITE_RE.finditer(answer):
                name = match.group(1).strip()
                for hit in hits:
                    if hit.name == name and hit.chunk_id not in found:
                        found[hit.chunk_id] = hit.cite()
                        break
        return list(found.values())

    def _path_for(self, name: str) -> str:
        row = self.catalog.by_name(name)
        return str(row_get(row, "path", ""))

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

    # --- enrichment queue -------------------------------------------------
    def index_ai(self, progress: Callable[[int, int], None],
                 stop: Callable[[], bool] = lambda: False,
                 limit: int | None = None) -> str:
        """Queue and run enrichment for the whole library (resumable)."""
        def run() -> str:
            jobs_mod.enqueue(self.catalog)
            return jobs_mod.run_queue(
                self, progress=progress, stop=stop, limit=limit,
                retry_limit=int(self.setting("ai_retry_limit") or 3),
            )
        return self._busy_guard("index-ai", run)

    def run_jobs(self, progress: Callable[[int, int], None] = lambda d, t: None,
                 stop: Callable[[], bool] = lambda: False,
                 limit: int | None = None) -> str:
        """Drain the existing queue without re-queueing the whole library."""
        return jobs_mod.run_queue(
            self, progress=progress, stop=stop, limit=limit,
            retry_limit=int(self.setting("ai_retry_limit") or 3),
        )


# ---------------------------------------------------------------------------
# vector maths + fusion
# ---------------------------------------------------------------------------
def _score_vectors(q: Sequence[float], rows: Iterable[tuple[int, int, bytes, int]]
                   ) -> list[tuple[int, int, float]]:
    """(chunk_id, doc_id, cosine) sorted best-first; numpy fast path when available."""
    dim = len(q)
    if dim == 0:
        return []
    materialized = list(rows)
    if not materialized:
        return []
    qn = math.sqrt(sum(x * x for x in q)) or 1.0
    try:
        import numpy as np  # optional: a 50k-chunk library needs it to stay fast
    except Exception:  # noqa: BLE001 - pure-python fallback
        np = None  # type: ignore[assignment]

    if np is not None:
        ids: list[int] = []
        docs: list[int] = []
        mats = []
        for chunk_id, doc_id, blob, vdim in materialized:
            if vdim != dim or len(blob) != 4 * dim:
                continue
            ids.append(chunk_id)
            docs.append(doc_id)
            mats.append(np.frombuffer(blob, dtype="<f4"))
        if not ids:
            return []
        mat = np.vstack(mats).astype("float32")
        norms = np.linalg.norm(mat, axis=1)
        norms[norms == 0] = 1.0
        sims = mat.dot(np.asarray(q, dtype="float32")) / (norms * qn)
        order = np.argsort(-sims)
        return [(ids[int(i)], docs[int(i)], float(sims[int(i)])) for i in order]

    scored: list[tuple[int, int, float]] = []
    for chunk_id, doc_id, blob, vdim in materialized:
        if vdim != dim or len(blob) != 4 * dim:
            continue
        scored.append((chunk_id, doc_id, _cosine(q, _unpack(blob))))
    scored.sort(key=lambda r: r[2], reverse=True)
    return scored


def _rrf(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> dict[int, float]:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank)."""
    fused: dict[int, float] = {}
    for ranking in rankings:
        seen: set[int] = set()
        for rank, item in enumerate(ranking, 1):
            if item in seen:
                continue
            seen.add(item)
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank)
    return fused


def _mean_normalized(vectors: Sequence[Sequence[float]]) -> list[float]:
    dim = len(vectors[0])
    totals = [0.0] * dim
    count = 0
    for vec in vectors:
        if len(vec) != dim:
            continue
        for i, value in enumerate(vec):
            totals[i] += value
        count += 1
    if not count:
        return list(vectors[0])
    return [v / count for v in totals]


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
