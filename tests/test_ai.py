"""AI layer smoke test: summaries, tags, embeddings, semantic search, related, ask.

The network client is stubbed — nothing leaves the machine. This verifies the
DropLens AI service logic (caching, storage, cosine ranking, citation wiring).
"""
import os
import sys
import tempfile

os.environ["DROPLENS_DIR"] = tempfile.mkdtemp(prefix="droplens_ai_test_")
sys.path.insert(0, os.getcwd())

import dropLens.ai.client as client_mod  # noqa: E402
import dropLens.ai.svc as svc_mod  # noqa: E402
from dropLens.ai.providers import AIConfig, AIProvider  # noqa: E402
from dropLens.ai.svc import _cosine  # noqa: E402
from dropLens.config import ConfigStore  # noqa: E402
from dropLens.engine.db import Catalog  # noqa: E402


def fake_chat(provider, system, user, temperature=0.3, max_tokens=2048, **_):
    if "summary" in system.lower():
        return "This document discusses quarterly warehouse metrics and budget allocation."
    if "tags" in system.lower():
        return "finance, warehouse, quarterly"
    if "answer" in system.lower():
        return "The budget allocation is covered in report.pdf.\n[file: report.pdf]"
    if "report" in system.lower():
        return "# Folder report\nFiles: 2."
    return "ok"


def fake_chat_messages(provider, messages, temperature=0.3, max_tokens=2048, **_):
    return fake_chat(provider, "", messages[-1]["content"])


def fake_embed(provider, texts):
    # deterministic pseudo-embedding (no salted hash()): keyword-prefix score
    out = []
    for t in texts:
        feats = []
        for kw in ("warehouse", "report", "invoice", "budget", "quarterly", "customer"):
            feats.append(1.0 if kw in t.lower() else 0.0)
        feats.extend([float((i * 37) % 100) / 100.0 for i in range(16 - len(feats))])
        out.append(feats)
    return out


svc_mod.chat = fake_chat
svc_mod.embed = fake_embed
svc_mod.client_mod = client_mod

store = ConfigStore()
cfg = store.get()
provider = AIProvider(name="Stub", kind="openai", base_url="http://localhost:1/v1",
                      model="stub", embedding_model="stub-embed")
ai_cfg = AIConfig({})
ai_cfg.providers = [provider]
ai_cfg.active = "Stub"
cfg.set_ai_config(ai_cfg)
store.save(cfg)

db = Catalog(os.path.join(os.environ["DROPLENS_DIR"], "catalog.db"))
svc = svc_mod.AIService(db, config=lambda: store.get().ai_config())

# seed two docs
db.add_root(os.environ["DROPLENS_DIR"])
db.execute("INSERT OR REPLACE INTO files(id, kind, name, path, size, mtime, ext, category, status, hash, root) "
           "VALUES (1, 'file', 'report.pdf', 'C:/a/report.pdf', 100, 0, '.pdf', 'doc', 'indexed', 'h1', 'C:/a')")
db.execute("INSERT INTO docs(doc_id, name, path, content) "
           "VALUES (1, 'report.pdf', 'C:/a/report.pdf', 'quarterly warehouse budget')")
db.execute("INSERT OR REPLACE INTO files(id, kind, name, path, size, mtime, ext, category, status, hash, root) "
           "VALUES (2, 'file', 'invoice.xlsx', 'C:/b/invoice.xlsx', 200, 0, '.xlsx', 'sheet', 'indexed', 'h2', 'C:/b')")
db.execute("INSERT INTO docs(doc_id, name, path, content) "
           "VALUES (2, 'invoice.xlsx', 'C:/b/invoice.xlsx', 'customer invoice for warehouse supplies')")
db.commit()

# summary + tag + embedding for doc 1 (cached)
s1 = svc.summarize_doc(1)
assert "warehouse" in s1, s1
s1b = svc.summarize_doc(1)  # served from cache
assert "budget" in s1b
tags = svc.auto_tag(1)
assert tags == ["finance", "warehouse", "quarterly"], tags
svc.embed_doc(1)
svc.embed_doc(2)

# metadata persists
meta1 = db.get_meta(1)
assert meta1["summary"] and meta1["tags"] and meta1["embedding"], "meta row not complete"

# semantic search ranks the true match first
pairs = svc.semantic_search("warehouse budget report", top_k=5)
assert pairs and pairs[0][0] == 1, pairs

# favorites
assert db.toggle_favorite(1) == 1
assert db.toggle_favorite(1) == 0

# tags index + files_with_tag
taglist = dict(db.list_tags())
assert taglist.get("warehouse") == 1, taglist
assert taglist.get("finance") == 1, taglist
rows = db.files_with_tag("warehouse")
assert len(rows) == 1

# related (both docs similar to each other)
rel = svc.related(1, top_k=3)
assert rel and rel[0][0] == 2, rel

# ask: cites in answer resolved back
answer, cites = svc.ask("Where is the budget covered?")
assert "report.pdf" in answer, answer
assert any("report.pdf" in name for name, path in cites), cites

# prefs round trip
db.pref_set("onboarded", "1")
assert db.pref_get("onboarded") == "1"

# embeddings helper sanity
assert abs(_cosine([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-9
assert abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9

print("AI OK — summary, tags, embeddings, semantic rank, related, ask, favorites, prefs")
db.close()