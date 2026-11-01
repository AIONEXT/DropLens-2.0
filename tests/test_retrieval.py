"""Chunked hybrid retrieval: chunker, RRF fusion, provenance and invalidation.

The network client is stubbed, so nothing leaves the machine. What is verified:

  * the chunker keeps page numbers and exact character offsets,
  * retrieval fuses BM25 and vector channels instead of falling back from one
    to the other,
  * an answer cites chunk ids that map back to (doc, page, start_char),
  * a changed document invalidates its summary/tags/vectors,
  * the durable job queue resumes, backs off and cancels.
"""
import os
import re
import sys
import tempfile

os.environ["DROPLENS_DIR"] = tempfile.mkdtemp(prefix="droplens_retrieval_test_")
sys.path.insert(0, os.getcwd())

import dropLens.ai.svc as svc_mod  # noqa: E402
from dropLens.ai.providers import AIConfig, AIProvider  # noqa: E402
from dropLens.config import Config  # noqa: E402
from dropLens.engine.chunk import chunk_text, PAGE_MARK  # noqa: E402
from dropLens.engine.db import Catalog  # noqa: E402

# ---------------------------------------------------------------------------
# 1. chunker: pages, offsets, sizes, overlap
# ---------------------------------------------------------------------------
pages = [
    "Quarterly revenue increased across every region this year. " * 18,
    "Customer churn decreased in the north. Support tickets fell as well. " * 18,
    "The warehouse budget was approved by the board on Tuesday. " * 18,
]
content = PAGE_MARK.join(pages)
chunks = chunk_text(content, target_chars=400, overlap=0.2)
assert len(chunks) >= 6, len(chunks)
assert [c.ord for c in chunks] == list(range(len(chunks)))
assert {c.page for c in chunks} == {1, 2, 3}, {c.page for c in chunks}
budget = 400 + int(400 * 0.2)
for c in chunks:
    assert content[c.start_char:c.end_char].strip() == c.text
    assert len(c.text) <= budget, (len(c.text), budget)
# overlap: the second chunk starts before the first one ends
assert chunks[1].start_char < chunks[0].end_char, (chunks[0], chunks[1])
# every page keeps its own words exactly once in the page's first chunk
assert any("warehouse budget" in c.text for c in chunks if c.page == 3)
assert chunk_text("") == []
assert chunk_text(None) == []
long_para = "word " * 500
assert len(chunk_text(long_para, target_chars=400)) >= 2

# ---------------------------------------------------------------------------
# 2. stubs: deterministic embeddings + citing chat
# ---------------------------------------------------------------------------
BUCKETS = {
    "warehouse": 0, "logistics": 0, "budget": 1, "revenue": 1, "invoice": 1,
    "lipid": 2, "nanoparticle": 2, "vaccine": 2, "immunisation": 2,
    "churn": 3, "customer": 3,
}


def fake_embed(provider, texts):
    out = []
    for text in texts:
        vec = [0.0] * 4
        for word, idx in BUCKETS.items():
            if word in (text or "").lower():
                vec[idx] = 1.0
        vec.append(0.01)  # never a zero vector
        out.append(vec)
    return out


def fake_chat(provider, system, user, temperature=0.3, max_tokens=2048, **_):
    if "reference id" in system:
        ids = re.findall(r"\[#(\d+)\]", user)
        if ids:
            return f"The warehouse budget was approved [#{ids[0]}]."
        return "I could not find that in the indexed files."
    if "tags" in system.lower():
        return "finance, warehouse"
    if "summary" in system.lower():
        return "SUM:" + " ".join(user.split())[-60:]
    return "ok"


svc_mod.chat = fake_chat
svc_mod.embed = fake_embed

# ---------------------------------------------------------------------------
# 3. wire up a catalogue with one multi-page document
# ---------------------------------------------------------------------------
db = Catalog(os.path.join(os.environ["DROPLENS_DIR"], "catalog.db"))
db.add_root(os.environ["DROPLENS_DIR"])
db.execute("INSERT INTO files(id, kind, name, path, size, mtime, ext, category, status, hash, root) "
           "VALUES (1, 'file', 'board-pack.pdf', 'C:/a/board-pack.pdf', 4000, 0, '.pdf', "
           "'document', 'indexed', 'h1', 'C:/a')")
db.execute("INSERT INTO docs(doc_id, name, path, content) VALUES (1, 'board-pack.pdf', "
           "'C:/a/board-pack.pdf', ?)", (content,))
db.execute("INSERT INTO files(id, kind, name, path, size, mtime, ext, category, status, hash, root) "
           "VALUES (2, 'file', 'lab-notes.txt', 'C:/b/lab-notes.txt', 900, 0, '.txt', "
           "'document', 'indexed', 'h2', 'C:/b')")
db.execute("INSERT INTO docs(doc_id, name, path, content) VALUES (2, 'lab-notes.txt', "
           "'C:/b/lab-notes.txt', ?)",
           ("The lipid nanoparticle formulation was stable.\n\nAnimal vaccine trials start in May.",))
db.commit()

provider = AIProvider(name="Stub", kind="openai", base_url="http://localhost:1/v1",
                      model="stub", embedding_model="stub-embed")
ai_cfg = AIConfig({})
ai_cfg.providers = [provider]
ai_cfg.active = "Stub"

settings = Config()
settings.patch(chunk_chars=220, chunk_overlap=0.2, embed_batch=2,
               retrieve_candidates=20, retrieve_top=4)
svc = svc_mod.AIService(db, config=lambda: ai_cfg, settings=lambda: settings)

# ---------------------------------------------------------------------------
# 4. embedding produces many chunks, not one vector for the whole file
# ---------------------------------------------------------------------------
svc.embed_doc(1)
svc.embed_doc(2)
rows = db.chunks_for_doc(1)
assert len(rows) >= 3, f"expected chunk-level vectors, got {len(rows)} chunks"
assert db.chunk_vector_count() >= 4
meta = db.get_meta(1)
assert meta["embedding"] and meta["content_hash"], dict(meta)

# ---------------------------------------------------------------------------
# 5. hybrid retrieval: keyword channel, vector channel, provenance
# ---------------------------------------------------------------------------
hits = svc.retrieve("warehouse budget")
assert hits, "no hits for an exact phrase"
assert hits[0].page == 3, hits[0].page
assert "warehouse" in hits[0].text
assert hits[0].start_char < hits[0].end_char
assert hits[0].path == "C:/a/board-pack.pdf"

# semantic-only: "immunisation" appears in no stored text, so BM25 cannot find
# it — only the vector channel can surface the lipid-nanoparticle passage.
semantic_only = svc.retrieve("immunisation")
assert semantic_only, "vector channel produced nothing (retrieval is not hybrid)"
assert any("lipid" in h.text for h in semantic_only), [h.text[:40] for h in semantic_only]
assert any(h.doc_id == 2 for h in semantic_only), "wrong document surfaced"

# and with embeddings unavailable the same query finds nothing, while the exact
# keyword query still works: neither channel is a hard dependency
svc_no_vec = svc_mod.AIService(db, config=lambda: ai_cfg, settings=lambda: settings)
svc_no_vec.provider = lambda: AIProvider(name="NoEmb", kind="openai",
                                         base_url="http://localhost:1/v1", model="stub")
assert svc_no_vec.retrieve("immunisation") == []
kw_hits = svc_no_vec.retrieve("warehouse budget")
assert kw_hits and kw_hits[0].page == 3

# ---------------------------------------------------------------------------
# 5b. the category filter reaches both channels (H2)
# ---------------------------------------------------------------------------
db.execute("UPDATE files SET category='sheet' WHERE id=2")
db.commit()
assert db.file_ids("document") == {1}, db.file_ids("document")
assert db.file_ids("sheet") == {2}
assert db.file_ids() is None, "no filter must mean no restriction"

# "immunisation" matches no stored text, so hits can only come from the
# vector channel — filtering it is exactly what H2 was about
unfiltered = svc.retrieve("immunisation")
assert 2 in {h.doc_id for h in unfiltered}, "vector channel lost the true match"

doc_only = svc.retrieve("immunisation", category="document")
assert all(h.doc_id == 1 for h in doc_only), [h.doc_id for h in doc_only]
sheet_only = svc.retrieve("immunisation", category="sheet")
assert sheet_only and all(h.doc_id == 2 for h in sheet_only), [h.doc_id for h in sheet_only]

# document-level semantic search applies the same filter
both = svc.semantic_search("immunisation lipid", top_k=5)
assert {d for d, _ in both} == {1, 2}, both
only_sheet = svc.semantic_search("immunisation lipid", top_k=5, category="sheet")
assert only_sheet and {d for d, _ in only_sheet} == {2}, only_sheet
only_doc = svc.semantic_search("immunisation lipid", top_k=5, category="document")
assert only_doc and {d for d, _ in only_doc} == {1}, only_doc

# ---------------------------------------------------------------------------
# 6. the assistant cites chunk ids that resolve to page + offsets
# ---------------------------------------------------------------------------
answer, cites = svc.ask("Where was the warehouse budget approved?")
assert "[#" in answer, answer
assert cites, "no structured citations"
cite = cites[0]
for key in ("chunk_id", "doc_id", "name", "path", "page", "start_char", "end_char", "snippet"):
    assert key in cite, (key, cite)
assert cite["page"] == 3
assert cite["doc_id"] == 1
assert content[cite["start_char"]:cite["end_char"]].strip() == cite["snippet"] or \
    " ".join(content[cite["start_char"]:cite["end_char"]].split()) == cite["snippet"]

# ---------------------------------------------------------------------------
# 7. invalidation: editing the document drops the stale summary and vectors
# ---------------------------------------------------------------------------
first = svc.summarize_doc(1)
assert first.startswith("SUM:")
svc.auto_tag(1)
db.execute("DELETE FROM docs WHERE doc_id=1")
db.execute("INSERT INTO docs(doc_id, name, path, content) VALUES (1, 'board-pack.pdf', "
           "'C:/a/board-pack.pdf', ?)", ("Completely rewritten document about solar panels.",))
db.commit()
changed = db.save_doc(1, "board-pack.pdf", "C:/a/board-pack.pdf",
                      "Completely rewritten document about solar panels.")
assert changed is True
assert db.get_meta(1) is None or not db.get_meta(1)["summary"], "stale summary survived an edit"
assert db.chunks_for_doc(1) == [], "stale chunks survived an edit"
second = svc.summarize_doc(1)
assert second != first, "summary was served from cache after the content changed"

# ---------------------------------------------------------------------------
# 8. durable queue: enqueue, run, backoff, cancel
# ---------------------------------------------------------------------------
db.execute("UPDATE docmeta SET summary=NULL, tags=NULL, embedding=NULL")
db.execute("DELETE FROM chunk_vectors")
db.commit()
queued = db.enqueue_ai_jobs()
assert queued >= 3, queued
assert set(db.job_counts()) <= {"queued", "done", "failed", "skipped"}
summary = svc.run_jobs()
assert "AI enrichment" in summary, summary
assert db.job_counts().get("queued", 0) == 0, db.job_counts()
assert db.get_meta(1)["summary"], "queue did not produce a summary"

# cancellation leaves work queued rather than losing it
db.execute("UPDATE docmeta SET summary=NULL")
db.commit()
db.enqueue_ai_jobs(kinds=("summary",))
svc.run_jobs(stop=lambda: True)
assert db.job_counts().get("queued", 0) >= 1

# a failing provider retries with backoff instead of dropping the job
calls = {"n": 0}


def flaky_chat(provider, system, user, temperature=0.3, max_tokens=2048, **_):
    calls["n"] += 1
    raise svc_mod.AIConnectionError("429 rate limited")


svc_mod.chat = flaky_chat
svc.run_jobs()
row = db.execute("SELECT state, attempts, next_at, last_error FROM ai_jobs "
                 "WHERE kind='summary' LIMIT 1").fetchone()
assert row["state"] == "queued", dict(row)
assert row["attempts"] == 1, dict(row)
assert row["next_at"] and row["next_at"] > 0, dict(row)
assert "429" in (row["last_error"] or ""), dict(row)
svc_mod.chat = fake_chat

print("RETRIEVAL OK — chunking, hybrid RRF, chunk citations, invalidation, job queue")
db.close()
