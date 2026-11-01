"""Respan Gateway integration check: the app's own AI client through a loopback gateway.

No provider key is involved. A stdlib HTTP server stands in for an
OpenAI-compatible backend, a ``gateway.toml`` routes the app's model names to
it, and DropLens' client — the single path every summary, tag, embedding and
assistant answer takes — is driven through the ``gateway`` provider kind.
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ["DROPLENS_DIR"] = tempfile.mkdtemp(prefix="droplens_gateway_test_")
sys.path.insert(0, os.getcwd())

REQUESTS: list[dict] = []


class Fixture(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible backend: chat completions, embeddings, models."""

    def log_message(self, *args):  # keep the check output readable
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"object": "list", "data": [
            {"id": "fixture-chat", "object": "model", "owned_by": "fixture"}]})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
        body = json.loads(raw or b"{}")
        REQUESTS.append({"path": self.path, "body": body})
        if self.path.endswith("/embeddings"):
            inputs = body.get("input") or []
            self._send({"object": "list", "model": body.get("model"), "data": [
                {"object": "embedding", "index": i, "embedding": [float(i), 1.0, 0.0]}
                for i in range(len(inputs))]})
            return
        self._send({
            "id": "chatcmpl-fixture", "object": "chat.completion", "created": 0,
            "model": body.get("model"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant",
                                     "content": "Replies through the gateway."}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
        })


fixture = ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
threading.Thread(target=fixture.serve_forever, daemon=True).start()
fixture_port = fixture.server_address[1]

from dropLens import net, resources  # noqa: E402
from dropLens.ai import gateway  # noqa: E402
from dropLens.ai.client import chat, chat_tools, embed, list_models, ping  # noqa: E402
from dropLens.ai.providers import KIND_GATEWAY, PRESETS, AIProvider  # noqa: E402

# the gateway the app embeds reads gateway.toml from its data directory
config_path = os.path.join(resources.data_dir(), "gateway.toml")
with open(config_path, "w", encoding="utf-8") as fh:
    fh.write(
        'listen = "127.0.0.1:0"\n'
        'log = "off"\n'
        'ui = false\n'
        '[providers.fixture]\n'
        'formats = ["openai-chat", "openai-embeddings"]\n'
        f'base_url = "http://127.0.0.1:{fixture_port}/v1"\n'
    )

assert gateway.config_path() == config_path, gateway.config_path()
assert gateway.available(), "respan-gateway is not installed"
assert PRESETS["Respan Gateway (local)"]["kind"] == KIND_GATEWAY

url = gateway.base_url()
assert url.startswith("http://127.0.0.1:"), url
assert gateway.base_url() == url, "the gateway server must be reused, not restarted"

provider = AIProvider(name="Respan Gateway (local)", kind=KIND_GATEWAY,
                      model="fixture/fixture-chat", embedding_model="fixture/embed")

# 1. chat: the app's summary/tag/answer calls
reply = chat(provider, "You are DropLens.", "Summarise this file.")
assert reply == "Replies through the gateway.", reply
assert REQUESTS[-1]["path"].endswith("/v1/chat/completions"), REQUESTS[-1]
assert REQUESTS[-1]["body"]["model"] == "fixture-chat", REQUESTS[-1]["body"]["model"]

# 2. tool calling: the agent loop
turn = chat_tools(provider, [{"role": "user", "content": "what is indexed?"}], [])
assert turn["content"] == "Replies through the gateway.", turn
assert turn["tool_calls"] == [], turn

# 3. embeddings: semantic search
vectors = embed(provider, ["alpha", "beta"])
assert vectors == [[0.0, 1.0, 0.0], [1.0, 1.0, 0.0]], vectors

# 4. model listing + connectivity probe used by Settings → AI Models
assert list_models(provider) == ["fixture-chat"], list_models(provider)
assert ping(provider) == "Replies through the gateway.", ping

# 5. every hop stayed on loopback, so offline-only mode still covers it
assert all(entry["local"] for entry in net.recent()), net.recent()
assert net.is_local_url(url)

gateway.stop()
assert gateway.base_url() != url, "stop() must release the server so it restarts cleanly"
gateway.stop()
fixture.shutdown()

print("gateway: chat, tools, embeddings, models, ping all routed through the "
      f"loopback gateway on {url} — {len(REQUESTS)} fixture requests")
print("test_gateway: OK")
