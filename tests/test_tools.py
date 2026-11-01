"""Tools, the toolbox and the tool-calling agent loop.

The model is stubbed, so nothing leaves the machine. What is verified:

  * the built-in tools read the catalogue and return citable chunk ids,
  * a tool fault becomes an error payload, never an exception,
  * the agent executes tool calls, feeds results back and stops,
  * it always ends with prose: the final call is made without tools,
  * citations are resolved from real chunk ids (page, offsets, snippet),
  * the provider converters emit the right shape for OpenAI, Anthropic, Gemini.
"""
import json
import os
import sys
import tempfile

os.environ["DROPLENS_DIR"] = tempfile.mkdtemp(prefix="droplens_tools_test_")
sys.path.insert(0, os.getcwd())

import dropLens.ai.agent as agent_mod  # noqa: E402
import dropLens.ai.client as client_mod  # noqa: E402
import dropLens.ai.svc as svc_mod  # noqa: E402
from dropLens import extensions  # noqa: E402
from dropLens.ai.providers import AIConfig, AIProvider  # noqa: E402
from dropLens.engine.chunk import PAGE_MARK, chunk_text  # noqa: E402
from dropLens.engine.db import Catalog  # noqa: E402
from dropLens.tools import Tool, Toolbox, library_tools  # noqa: E402

DATA = os.environ["DROPLENS_DIR"]
db = Catalog(os.path.join(DATA, "catalog.db"))
db.add_root(DATA)

content = PAGE_MARK.join([
    "Quarterly revenue increased across every region this year. " * 4,
    "The warehouse budget was approved by the board on Tuesday. " * 4,
])
db.execute("INSERT INTO files(id, kind, name, path, size, mtime, ext, category, status, hash, root) "
           "VALUES (1, 'file', 'board-pack.pdf', ?, 4000, 0, '.pdf', 'document', 'indexed', 'h1', ?)",
           (os.path.join(DATA, "board-pack.pdf"), DATA))
db.commit()
db.save_doc(1, "board-pack.pdf", os.path.join(DATA, "board-pack.pdf"), content)
chunks = chunk_text(content, target_chars=300)
chunk_ids = db.replace_chunks(1, chunks)
db.commit()
assert chunk_ids, "fixture: no chunks were created"
chunk_rows = db.chunks_for_doc(1)
assert len(chunk_rows) == len(chunks)
budget_chunk = next(r for r in chunk_rows if "warehouse budget" in r["text"])
budget_id = int(budget_chunk["id"])

tools = {t.name: t for t in library_tools(db)}
assert set(tools) == {"search_library", "read_document", "library_stats", "list_documents"}
for tool in tools.values():
    spec = tool.spec()
    assert set(spec) == {"name", "description", "parameters"}
    assert spec["parameters"]["type"] == "object"

# ---------------------------------------------------------------------------
# 1. search_library: chunk granularity with a citable id
# ---------------------------------------------------------------------------
body, parsed = tools["search_library"].call({"query": "warehouse budget"})
assert parsed and "error" not in parsed, body
assert parsed["granularity"] == "chunk", parsed
hit = parsed["results"][0]
assert hit["chunk_id"] == budget_id, (hit, budget_id)
assert "warehouse" in hit["snippet"]
assert hit["path"] == os.path.join(DATA, "board-pack.pdf")
assert hit["page"] == 2, hit["page"]

# an empty query is answered, not raised
empty = tools["search_library"].call({"query": "  "})[1]
assert empty == {"results": [], "note": "the query contained no searchable tokens"}, empty
# a missing term is still a well-formed payload
assert tools["search_library"].call({"query": "zzzznothing"})[1]["results"] == []

# ---------------------------------------------------------------------------
# 2. read_document: window, page and continuation
# ---------------------------------------------------------------------------
full = tools["read_document"].call({"doc_id": 1, "length": 200})[1]
assert "error" not in full, full
assert full["doc_id"] == 1 and full["total_chars"] == len(content)
assert full["text"] == content[:full["end_char"]]
assert full["end_char"] < len(content), "fixture too small to test continuation"
assert full["next_start"] == full["end_char"]
tail = tools["read_document"].call({"doc_id": 1, "start": full["end_char"],
                                    "length": 600})[1]
assert tail["text"] == content[tail["start_char"]:tail["end_char"]]
assert tail["start_char"] == full["end_char"] and tail["next_start"] is None
assert tail["page"] >= full["page"]
assert tools["read_document"].call({"doc_id": 999})[1]["error"]
# by name, which is how a model refers to a file it saw in an answer
by_name = tools["read_document"].call({"name": "board-pack.pdf"})[1]
assert by_name and by_name.get("doc_id") == 1, by_name

# ---------------------------------------------------------------------------
# 3. stats and listing
# ---------------------------------------------------------------------------
stats = tools["library_stats"].call({})[1]
assert stats["files"] == 1 and stats["chunks"] >= 2, stats
assert stats["categories"][0]["category"] == "document"
listing = tools["list_documents"].call({"limit": 5})[1]
assert listing["count"] == 1 and listing["documents"][0]["doc_id"] == 1, listing
assert tools["list_documents"].call({"category": "nope"})[1]["count"] == 0

# ---------------------------------------------------------------------------
# 4. a tool fault is data
# ---------------------------------------------------------------------------
def explode(_args):
    raise ValueError("handler exploded")


text, parsed = Tool(name="boom", description="", parameters={}, handler=explode).call({})
assert parsed is None and "handler exploded" in text, (text, parsed)
assert json.loads(text)["error"].startswith("ValueError"), text

# ---------------------------------------------------------------------------
# 5. toolbox: order kept, names unique
# ---------------------------------------------------------------------------
box = Toolbox(library_tools(db))
assert len(box) == 4 and "search_library" in box
assert box.register(Tool(name="search_library", description="dup", parameters={},
                         handler=lambda a: a)) is False
assert len(box) == 4
custom = Tool(name="custom", description="x", parameters={}, handler=lambda a: {"ok": True},
              source="test")
assert box.register(custom) is True and box.get("custom") is custom
assert "custom" in [s["name"] for s in box.specs()]

# ---------------------------------------------------------------------------
# 6. the agent loop
# ---------------------------------------------------------------------------
provider = AIProvider(name="Stub", kind="openai", base_url="http://localhost:1/v1",
                      model="stub", embedding_model="")
calls = []


def scripted(turns):
    """A chat_tools stand-in that replays *turns* and records every request."""
    played: list[int] = []

    def fake(provider, messages, tools, temperature=0.2, max_tokens=1200):
        played.append(1)
        calls.append({"messages": [dict(m) for m in messages], "tools": list(tools)})
        return turns[min(len(played) - 1, len(turns) - 1)]

    return fake


first = {"content": "", "tool_calls": [{"id": "c1", "name": "search_library",
                                        "arguments": {"query": "warehouse budget"}}]}
answer_text = f"The board approved the warehouse budget on Tuesday [#{budget_id}]."
run = agent_mod.run("Who approved the warehouse budget?", box, provider,
                    chat_fn=scripted([first, {"content": answer_text, "tool_calls": []}]))
assert run.answer == answer_text, run.answer
assert len(run.steps) == 1 and run.tool_calls == 1
step = run.steps[0]
assert step["tool"] == "search_library" and step["ok"] is True
assert step["arguments"] == {"query": "warehouse budget"}
assert run.chunk_ids == [budget_id], run.chunk_ids
# the request carried the tool specs, then the tool result went back to the model
assert calls[0]["tools"] and calls[0]["tools"][0]["name"] == "search_library"
second = calls[1]["messages"]
assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["name"] == "search_library"
assert second[-1]["role"] == "tool" and second[-1]["tool_call_id"] == "c1"
assert str(budget_id) in second[-1]["content"]
assert calls[1]["tools"], "the follow-up turn should still offer tools"

# a model that only ever calls tools is cut off and asked for prose — without tools
calls.clear()
greedy = {"content": "", "tool_calls": [{"id": "x", "name": "library_stats", "arguments": {}}]}
run = agent_mod.run("anything", box, provider, max_steps=2,
                    chat_fn=scripted([greedy, greedy, {"content": "final prose", "tool_calls": []}]))
assert run.answer == "final prose", run.answer
assert run.tool_calls == 2, run.tool_calls
assert calls[-1]["tools"] == [], "the final call must be made without tools"
assert calls[-1]["messages"][-1]["content"].startswith("Now answer")

# an unknown tool is reported to the model, not raised
calls.clear()
unknown = {"content": "", "tool_calls": [{"id": "u1", "name": "not_a_tool", "arguments": {}}]}
run = agent_mod.run("q", box, provider,
                    chat_fn=scripted([unknown, {"content": "recovered", "tool_calls": []}]))
assert run.answer == "recovered"
assert run.steps[0]["ok"] is False and run.steps[0]["tool"] == "not_a_tool"
assert "Unknown tool" in calls[1]["messages"][-1]["content"]

# an empty answer from a stubbed model is an error, not an empty string
try:
    agent_mod.run("q", box, provider, chat_fn=scripted([{"content": "", "tool_calls": []}]))
    raise AssertionError("an empty answer must raise AIConnectionError")
except client_mod.AIConnectionError:
    pass

# a step callback sees each call
seen = []
agent_mod.run("q", box, provider, chat_fn=scripted([first, {"content": "ok", "tool_calls": []}]),
              on_step=seen.append)
assert len(seen) == 1 and seen[0]["tool"] == "search_library"

# ---------------------------------------------------------------------------
# 7. service wiring: plugin tools, citations by chunk id
# ---------------------------------------------------------------------------
extensions.reset()
extensions.default().register_tool(Tool(name="plugin_tool", description="from a plugin",
                                        parameters={}, handler=lambda a: {"ok": 1},
                                        source="test-plugin"))
ai_cfg = AIConfig({})
ai_cfg.providers = [provider]
ai_cfg.active = "Stub"
service = svc_mod.AIService(db, config=lambda: ai_cfg)
names = service.toolbox().names()
assert "plugin_tool" in names and "search_library" in names, names
assert len(service.toolbox().specs()) == 5

cites = service._cites_for_ids([budget_id], answer_text)
assert len(cites) == 1, cites
cite = cites[0]
assert cite["chunk_id"] == budget_id and cite["doc_id"] == 1
assert cite["page"] == 2 and cite["label"].startswith(f"[#{budget_id}]")
assert content[cite["start_char"]:cite["end_char"]].strip() == "".join(cite["snippet"].split()) or \
    " ".join(content[cite["start_char"]:cite["end_char"]].split()) == cite["snippet"]
# ids the model invented are dropped: only chunks the tools returned have a row
assert service._cites_for_ids([999_999], "[#999999]") == []
assert service._cites_for_ids([], "") == []
# the cited id sorts first even when other chunks were also retrieved
both = service._cites_for_ids(list(range(1, 50)) + [budget_id], f"only [#{budget_id}]")
assert both[0]["chunk_id"] == budget_id and len(both) == 1, both

# ask_agent end to end, with the provider client stubbed at the module boundary
real_chat_tools = client_mod.chat_tools
try:
    client_mod.chat_tools = scripted([first, {"content": answer_text, "tool_calls": []}])
    answer, agent_cites, steps = service.ask_agent("Who approved it?")
finally:
    client_mod.chat_tools = real_chat_tools
assert answer == answer_text, answer
assert len(steps) == 1 and steps[0]["tool"] == "search_library"
assert agent_cites and agent_cites[0]["chunk_id"] == budget_id, agent_cites

# without a configured provider the call fails fast with AINotConfigured (the CLI
# maps that to exit code 3) rather than attempting a request
service_no_ai = svc_mod.AIService(db, config=lambda: AIConfig({}))


def no_provider():
    raise client_mod.AINotConfigured("No AI provider configured.")


service_no_ai.provider = no_provider   # type: ignore[method-assign]
try:
    service_no_ai.ask_agent("hello")
    raise AssertionError("ask_agent without a provider must raise AINotConfigured")
except client_mod.AINotConfigured as exc:
    assert "No AI provider" in str(exc), exc

# ---------------------------------------------------------------------------
# 8. provider converters
# ---------------------------------------------------------------------------
messages = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "find the budget"},
    {"role": "assistant", "content": "", "tool_calls": [
        {"id": "c1", "name": "search_library", "arguments": {"query": "budget"}}]},
    {"role": "tool", "tool_call_id": "c1", "name": "search_library",
     "content": '{"results": []}'},
]
openai = client_mod._openai_messages(messages)
assert openai[0] == {"role": "system", "content": "SYS"}
assert openai[2]["tool_calls"][0]["function"]["name"] == "search_library"
assert json.loads(openai[2]["tool_calls"][0]["function"]["arguments"]) == {"query": "budget"}
assert openai[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"results": []}'}

system, anthropic = client_mod._anthropic_messages(messages)
assert system == "SYS"
assert anthropic[0] == {"role": "user", "content": [{"type": "text", "text": "find the budget"}]}
assert anthropic[1]["content"][0]["type"] == "tool_use"
assert anthropic[1]["content"][0]["input"] == {"query": "budget"}
assert anthropic[2]["content"][0]["type"] == "tool_result"
assert anthropic[2]["content"][0]["tool_use_id"] == "c1"

gsystem, gemini = client_mod._gemini_contents(messages)
assert gsystem == "SYS"
flat = [part for turn in gemini for part in turn["parts"]]
assert any("functionCall" in p for p in flat), flat
assert any("functionResponse" in p for p in flat), flat
call_part = next(p["functionCall"] for p in flat if "functionCall" in p)
assert call_part["name"] == "search_library" and call_part["args"] == {"query": "budget"}
assert any(turn["role"] == "model" for turn in gemini), gemini

print("Tools OK — read-only tools, faults, agent loop, citations, converters")
db.close()
