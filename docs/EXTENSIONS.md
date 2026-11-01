# Extensions, tools and the AI interface

DropLens 2.2 opens three seams that used to be closed: **extractors** (new file
formats), **tools** (what the assistant can do) and **hooks** (what runs after an
engine event). All three are served by one small plugin system that loads plain
Python files from your data directory. Nothing is downloaded, nothing is
uploaded, and a broken plugin cannot take the app down.

## Where plugins live

```
<data_dir>/plugins/
    csv_tables.py          # a single module
    my_pack/               # or a package
        __init__.py
```

`<data_dir>` is the folder the app already uses for `catalog.db` (Settings → Data
folder; the default is the per-user DropLens folder). Files starting with `.` or
`_` are ignored.

Plugins load on the first scan and before any CLI command that needs them. To
turn the whole system off, set `plugins_enabled` to `false` in the config file;
built-in extractors then behave exactly as before.

An example that does all three things lives in
[`examples/plugins/csv_tables.py`](../examples/plugins/csv_tables.py).

## A plugin is a module with `register(api)`

```python
NAME = "CSV tables"
VERSION = "1.0"
DESCRIPTION = "Reads CSV/TSV with its header row"

def register(api):
    api.register_extractor(("csv", "tsv"), extract, replace=True)

    @api.tool("csv_columns", description="List the column names of a CSV file")
    def csv_columns(args):
        return {"columns": [...]}

    @api.on("document_indexed")
    def after_index(doc):
        ...
```

`NAME`, `VERSION` and `DESCRIPTION` are optional; the file name is used when
`NAME` is missing.

### Extractors

```python
api.register_extractor("emlx", extract)          # one extension
api.register_extractor(("emlx", "msg"), extract) # several
api.register_extractor("csv", extract, replace=True)
```

`extract(path)` receives an absolute path and returns the document text (or
`None` to decline). The returned text is what gets indexed, chunked, embedded
and searched, so return plain text with `\n` between lines — DropLens adds pages
itself where the format has them.

Rules:

* A registered extractor is tried **before** the built-in parser for that
  extension, and falls back to it when it returns `None` or raises. That is how
  you can improve `.csv` handling without losing the built-in path.
* Raise only for a genuinely broken file. Exceptions are recorded as a failed
  extraction for that file and never abort a scan.
* Return `""` or `None` for "nothing to index" (an empty or irrelevant file).
* `replace=True` only matters when two plugins claim the same extension; the
  first one wins otherwise.

### AI tools

```python
api.register_tool(my_tool)                       # a Tool instance
@api.tool("name", description="...", parameters=schema)
def handler(args): ...
```

The decorated function receives the model's parsed arguments as a dict and
returns either a dict (sent back to the model as JSON) or a string. Keep the
result small: one result is capped at 8,000 characters.

Tools are how you extend the assistant without touching its prompt. A tool may
read whatever you like, but it must be *safe to call repeatedly*: the model
decides when and how often. Do not put destructive work (deleting, moving,
uploading) behind a tool.

Built-in tools you can reason about alongside your own:

| Tool | Returns |
| --- | --- |
| `search_library` | matching passages with `chunk_id`, or whole files if no chunk index exists |
| `read_document` | a window of one document's text, with page number and total length |
| `library_stats` | totals, categories, chunk/vector counts, AI queue state |
| `list_documents` | recent documents, optionally filtered by folder or category |

### Hooks

```python
@api.on("document_indexed")
def after_document(doc): ...
```

| Event | When | Payload |
| --- | --- | --- |
| `scan_start` | a scan begins | `root`, `incremental` |
| `document_indexed` | one document's text was saved | `doc_id`, `name`, `path`, `chars`, `status` |
| `scan_complete` | a scan ends | `root`, `indexed`, `skipped`, `deleted`, `errors`, `cancelled`, `duration` |

Each hook runs after the catalogue write for that document, inside the same
scan batch. A hook that raises is logged and skipped; the remaining hooks and
the scan continue.

Hooks are for bookkeeping — copying a note next to a file, appending to a log,
sending a desktop notification. They are **not** a way to modify indexed text:
that decision was already made when the document was extracted.

## The AI interface

Two ways to ask, both in the GUI and the CLI:

```bash
droplens ask "what does the contract say about renewal?"     # retrieval + one model call
droplens ask --tools "compare the two invoices"              # tool-calling agent
droplens ask --tools --steps 6 --json "..."                  # more rounds, machine readable
droplens tools                                               # what the model can call
droplens tools --json
droplens extensions                                          # plugins, extractors, hooks, errors
droplens extensions --json
droplens extensions --reload
```

`ask` keeps the old behaviour: hybrid retrieval (BM25 + vectors, fused), one
call, citations resolved from the answer.

`ask --tools` runs the agent loop in `dropLens/ai/agent.py`:

1. the model is given the tool specs and asked to look things up;
2. each requested call is executed through the same read-only toolbox;
3. results go back as tool messages and the model continues;
4. at most `--steps` rounds (default 4) — after that the final call is made
   **without** tools, so you always get prose rather than another tool request.

Citations work the same way in both modes: a chunk id the tools actually
returned, mapped back to `(doc_id, page, start_char, end_char)`. A model cannot
invent a citation, because the ids it prints are checked against the chunks in
the index; ids that were never returned collapse to nothing.

The tool-calling protocol is provider-neutral. One normalised message shape is
translated per provider:

| Provider | Request | Response |
| --- | --- | --- |
| OpenAI-compatible (OpenAI, Ollama, LM Studio, Groq…) | `tools` + `tool_choice` | `message.tool_calls[].function` |
| Anthropic | `tools` with `input_schema` | `content[]` blocks `tool_use` / `text` |
| Gemini | `tools[].functionDeclarations` | `parts[].functionCall` |

That means a tool you write once works with every provider DropLens supports.

## Safety

* Tools shipped with DropLens are **read-only** over the catalogue. The
  assistant can search and read; it cannot delete, move or rewrite.
* A plugin gets an `ExtensionAPI`, not the catalogue and not the network
  helper. It sees the data directory path and nothing else.
* Tool and hook failures become error payloads or log lines. A plugin that
  raises on import is listed with its error in `droplens extensions` and
  otherwise ignored.
* The outbound-request log (`droplens egress`, Network log in the GUI) still
  records every AI call, so tool usage is inside the same audit trail as
  ordinary questions.

## Testing a plugin

Plugins are ordinary modules, so test them directly:

```python
from dropLens.extensions import Registry, ExtensionAPI
from dropLens.extensions.registry import Extension

reg = Registry()
reg.load("/path/to/plugins")            # or reg.load_file(single_file)
assert reg.extractor_for("csv") is not None
assert "csv_columns" in reg.tool_names()
reg.fire("scan_complete", {"root": "/tmp", "indexed": 1})
```

`tests/test_extensions.py` covers discovery, extractor precedence, hook
isolation and failing plugins; `tests/test_tools.py` covers the tools, the
toolbox and the agent loop with a stubbed model.
