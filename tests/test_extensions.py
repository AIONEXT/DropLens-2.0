"""Extension system: discovery, extractor precedence, tools, hook isolation.

No network, no GUI. What is verified:

  * a plugin in the data directory is imported once and its metadata is kept,
  * its extractor runs before the built-in parser, and declining falls back,
  * a plugin tool is registered and callable through the same Tool contract,
  * a failing plugin or a failing hook is recorded, never raised,
  * disabling ``plugins_enabled`` loads nothing.
"""
import os
import sys
import tempfile

os.environ["DROPLENS_DIR"] = tempfile.mkdtemp(prefix="droplens_ext_test_")
sys.path.insert(0, os.getcwd())

from dropLens import extensions  # noqa: E402
from dropLens.engine.extract import extract_content  # noqa: E402
from dropLens.tools import Tool  # noqa: E402

DATA = os.environ["DROPLENS_DIR"]
PLUGINS = os.path.join(DATA, "plugins")
os.makedirs(PLUGINS, exist_ok=True)
HOOK_LOG = os.path.join(DATA, "hook.log")


def write(name: str, body: str) -> str:
    path = os.path.join(PLUGINS, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


write("demo.py", f'''
"""A well-behaved plugin."""
NAME = "Demo pack"
VERSION = "1.2"
DESCRIPTION = "adds .emlx and a tool"


def read_emlx(path):
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return "EMLX-BODY " + fh.read()


def register(api):
    api.register_extractor("emlx", read_emlx)

    @api.tool("demo_echo", description="echo a value back")
    def demo_echo(args):
        return {{"echo": args.get("value", "")}}

    @api.on("document_indexed")
    def after_index(doc):
        with open({HOOK_LOG!r}, "w", encoding="utf-8") as fh:
            fh.write(doc.get("name", ""))

    @api.on("scan_complete")
    def boom(_payload):
        raise RuntimeError("hook exploded on purpose")
''')

write("override.py", '''
NAME = "Text override"


def register(api):
    def shout(path):
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return "PLUGIN:" + fh.read()

    api.register_extractor("log", shout, replace=True)

    def decline(_path):
        return None

    # claims a built-in text extension but declines every file it is given
    api.register_extractor("txt", decline)
''')

write("bad_register.py", '''
NAME = "Broken"


def register(api):
    raise RuntimeError("boom")
''')

write("no_register.py", 'NAME = "No entry point"\n')

write("_ignored.py", 'raise RuntimeError("underscore files must never be imported")\n')
write(".hidden.py", 'raise RuntimeError("dot files must never be imported")\n')

# ---------------------------------------------------------------------------
# 1. discovery
# ---------------------------------------------------------------------------
registry = extensions.default()
assert registry is extensions.default(), "default() must be a singleton"
registry.load(PLUGINS)

by_name = {e.name: e for e in registry.extensions}
assert set(by_name) == {"Demo pack", "Text override", "Broken", "No entry point"}, set(by_name)
assert by_name["Demo pack"].ok and by_name["Demo pack"].version == "1.2"
assert by_name["Demo pack"].description
assert not by_name["Broken"].ok and "boom" in by_name["Broken"].error
assert not by_name["No entry point"].ok and "register" in by_name["No entry point"].error
assert "_ignored" not in by_name and ".hidden" not in by_name

# idempotent: loading the same directory again must not duplicate or re-import
before = len(registry.extensions)
registry.load(PLUGINS)
assert len(registry.extensions) == before, "plugins were loaded twice"

# ---------------------------------------------------------------------------
# 2. extractors: plugin first, built-in fallback, unknown format
# ---------------------------------------------------------------------------
emlx = os.path.join(DATA, "note.emlx")
with open(emlx, "w", encoding="utf-8") as fh:
    fh.write("hello from a mail file")

text, status = extract_content(emlx)
assert status == "indexed", status
assert text.startswith("EMLX-BODY hello from"), text

# a plugin claiming a built-in extension wins…
log_file = os.path.join(DATA, "app.log")
with open(log_file, "w", encoding="utf-8") as fh:
    fh.write("plain log line")
text, status = extract_content(log_file)
assert status == "indexed" and text.startswith("PLUGIN:"), (status, text)

# …unless it declines, in which case the shipped text reader still runs
declined = os.path.join(DATA, "file.txt")
with open(declined, "w", encoding="utf-8") as fh:
    fh.write("builtin text wins")
text, status = extract_content(declined)
assert status == "indexed" and text == "builtin text wins", (status, text)

# an unknown extension with no plugin extractor is still a clean "no content"
unknown = os.path.join(DATA, "thing.weirdext")
with open(unknown, "wb") as fh:
    fh.write(b"\x00\x01\x02")
text, status = extract_content(unknown)
assert text is None and status == "no_content", (text, status)
assert registry.extractor_for(".EMLX") is not None, "extension lookup must be case-insensitive"

# ---------------------------------------------------------------------------
# 3. tools through the same contract as the built-ins
# ---------------------------------------------------------------------------
names = registry.tool_names()
assert "demo_echo" in names, names
tool = next(t for t in registry.tools() if t.name == "demo_echo")
assert isinstance(tool, Tool) and tool.source == "Demo pack"
body, parsed = tool.call({"value": "hi"})
assert parsed == {"echo": "hi"} and "hi" in body
# a JSON string (what every provider actually sends) is accepted too
assert tool.call('{"value": "json"}')[1] == {"echo": "json"}
# malformed arguments become an empty dict instead of an exception
assert tool.call("not json")[1] == {"echo": ""}

# the same name cannot be taken twice
assert registry.register_tool(tool) is False
assert registry.register_tool(Tool(name="", description="", parameters={},
                                   handler=lambda a: a)) is False

# ---------------------------------------------------------------------------
# 4. hooks: payloads delivered, failures isolated
# ---------------------------------------------------------------------------
results = registry.fire("scan_complete", {"root": DATA, "indexed": 3})
assert results == [], "the exploding hook should contribute no result"
assert registry.fire("document_indexed", {"doc_id": 1, "name": "note.emlx"}) == [None]
with open(HOOK_LOG, "r", encoding="utf-8") as fh:
    assert fh.read() == "note.emlx"
assert registry.fire("unknown-event") == [], "unknown events are a no-op at fire time"
try:
    registry.register_hook("nope", lambda p: p)
    raise AssertionError("registering an unknown hook must raise")
except ValueError:
    pass

# ---------------------------------------------------------------------------
# 5. the config switch
# ---------------------------------------------------------------------------
class StubConfig:
    data_dir = DATA

    def __init__(self, enabled=True):
        self.enabled = enabled

    def get(self, key, default=None):
        if key == "plugins_enabled":
            return self.enabled
        return default


extensions.reset()
extensions.load_from_config(StubConfig(enabled=False))
assert extensions.default().extensions == [], "plugins_enabled=False must load nothing"

extensions.reset()
extensions.load_from_config(StubConfig(enabled=True))
assert len(extensions.default().extensions) == 4
assert extensions.plugin_dir(DATA) == PLUGINS

# a registry is independent: loading into a fresh one does not inherit the old
fresh = extensions.Registry()
assert fresh.extensions == [] and fresh.tool_names() == []
fresh.load(PLUGINS)
assert "demo_echo" in fresh.tool_names()

# registering an extractor with no callable is rejected, not crashed
assert fresh.register_extractor("xyz", None) is False

print("Extensions OK — discovery, extractor precedence, tools, hooks, config switch")
