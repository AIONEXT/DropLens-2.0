"""In-process Respan Gateway: one loopback endpoint in front of every backend.

DropLens speaks plain OpenAI HTTP (see :mod:`dropLens.ai.client`) and already
keeps the developer's provider keys in its own settings, so the gateway runs
*inside* the app rather than as a separate service:

    gw = respan_gateway.Gateway()   # gateway.toml, else provider env keys
    server = gw.serve(port=0)       # a real socket, on 127.0.0.1 only

The ``gateway`` provider kind (``providers.KIND_GATEWAY``) asks
:func:`base_url` for that address at request time, so model routing, fallback
and provider translation are declared once in ``gateway.toml`` while the app
keeps its existing client, embeddings and tool calling.

Configuration is read from, in order: ``$DROPLENS_GATEWAY_CONFIG``, then
``<data dir>/gateway.toml``, then the gateway's own defaults (every vendor key
present in the environment becomes a provider). The server listens on loopback
only — a gateway holding provider keys must not be exposed.
"""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_gateway = None
_server = None
_url = ""


class GatewayUnavailable(Exception):
    """Raised when the embedded gateway cannot be imported or started."""


def available() -> bool:
    """True when the pinned ``respan-gateway`` package can be imported."""
    try:
        import respan_gateway  # noqa: F401
    except ImportError:
        return False
    return True


def config_path() -> str:
    """Where the gateway configuration is looked up."""
    explicit = os.environ.get("DROPLENS_GATEWAY_CONFIG", "").strip()
    if explicit:
        return explicit
    from .. import resources
    return os.path.join(resources.data_dir(), "gateway.toml")


def base_url() -> str:
    """Loopback URL of the running gateway, starting it on first use."""
    global _gateway, _server, _url
    with _lock:
        if _url:
            return _url
        if not available():
            raise GatewayUnavailable(
                "Respan Gateway is not installed — run: pip install respan-gateway==0.3.0"
            )
        import respan_gateway as rg

        path = config_path()
        try:
            gw = rg.Gateway.from_file(path) if os.path.isfile(path) else rg.Gateway()
            server = gw.serve(port=0)
        except Exception as exc:  # noqa: BLE001 - ConfigError, GatewayError, ...
            raise GatewayUnavailable(f"Respan Gateway could not start: {exc}") from exc
        _gateway, _server = gw, server
        _url = str(server.url).rstrip("/")
        log.info("Respan Gateway on %s (config: %s)", _url,
                 path if os.path.isfile(path) else "environment")
        return _url


def stop() -> None:
    """Stop the loopback server and close the gateway (safe to call twice)."""
    global _gateway, _server, _url
    with _lock:
        server, gw = _server, _gateway
        _server = _gateway = None
        _url = ""
    if server is not None:
        try:
            server.stop()
        except Exception:  # noqa: BLE001 - shutdown must never raise
            log.debug("gateway server stop failed", exc_info=True)
    if gw is not None:
        try:
            gw.close()
        except Exception:  # noqa: BLE001
            log.debug("gateway close failed", exc_info=True)
