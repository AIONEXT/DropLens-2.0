"""Bounded tool-calling loop for the assistant.

The loop is deliberately small and provider-neutral: the client returns one
normalised turn (``content`` plus ``tool_calls``), the loop executes the calls
through the :class:`~dropLens.tools.Toolbox`, appends the results and asks
again — at most ``max_steps`` times.

Everything is bounded on purpose. A model that keeps asking for tools cannot
spin forever, a failing tool becomes an error payload rather than an exception,
and the last turn is always taken without tools so the caller gets prose back.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..tools import Toolbox
from . import client
from .client import AIConnectionError
from .providers import AIProvider

log = logging.getLogger(__name__)

MAX_STEPS = 4

SYSTEM_AGENT = (
    "You are DropLens, a document analyst working over the user's own indexed files.\n"
    "Rules:\n"
    "1. Call a tool before answering — the library is the only source of truth.\n"
    "2. search_library returns passages with a chunk_id. Cite every claim as [#id].\n"
    "3. Use read_document when a snippet is not enough. Quote numbers exactly.\n"
    "4. Never invent ids, paths or facts. If the tools return nothing relevant, say "
    "the library does not contain the answer.\n"
    "5. Answer in at most eight sentences unless the user asks for a report."
)


@dataclass
class AgentRun:
    """Outcome of one agentic answer."""

    answer: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    chunk_ids: list[int] = field(default_factory=list)

    @property
    def tool_calls(self) -> int:
        return len(self.steps)


def run(
    question: str,
    toolbox: Toolbox,
    provider: AIProvider,
    *,
    max_steps: int = MAX_STEPS,
    chat_fn: Optional[Callable[..., dict[str, Any]]] = None,
    on_step: Optional[Callable[[dict[str, Any]], None]] = None,
) -> AgentRun:
    """Answer *question* using tools. Raises ``AIConnectionError`` on transport faults."""
    ask = chat_fn or client.chat_tools
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_AGENT},
        {"role": "user", "content": question},
    ]
    result = AgentRun(answer="")
    specs = toolbox.specs()

    for step in range(1, max(1, int(max_steps)) + 1):
        turn = ask(provider, messages, specs)
        calls = list(turn.get("tool_calls") or [])
        if not calls:
            result.answer = str(turn.get("content") or "").strip()
            break

        messages.append({
            "role": "assistant",
            "content": str(turn.get("content") or ""),
            "tool_calls": calls,
        })
        for call in calls:
            name = str(call.get("name") or "")
            tool = toolbox.get(name)
            if tool is None:
                text, parsed = f"Unknown tool: {name}", None
            else:
                text, parsed = tool.call(call.get("arguments"))
            result.chunk_ids.extend(_chunk_ids(parsed))
            record = {
                "step": step, "tool": name,
                "arguments": call.get("arguments") or {},
                "ok": parsed is not None,
                "result_chars": len(text),
            }
            result.steps.append(record)
            if on_step:
                on_step(record)
            messages.append({
                "role": "tool",
                "tool_call_id": str(call.get("id") or ""),
                "name": name,
                "content": text,
            })

    if not result.answer:
        # Out of steps (or the model only ever asked for tools): ask for prose
        # with the tool list removed so this call cannot loop again.
        final = ask(provider, messages + [{
            "role": "user",
            "content": "Now answer the question in prose using what the tools returned. No more tool calls.",
        }], [])
        result.answer = str(final.get("content") or "").strip()
        if not result.answer:
            raise AIConnectionError("The model returned an empty answer.")

    result.chunk_ids = list(dict.fromkeys(result.chunk_ids))
    return result


def _chunk_ids(parsed: Any) -> list[int]:
    """Collect chunk ids from a tool result so citations can be resolved."""
    if not isinstance(parsed, dict):
        return []
    ids: list[int] = []
    for item in parsed.get("results") or []:
        if isinstance(item, dict) and item.get("chunk_id") is not None:
            try:
                ids.append(int(item["chunk_id"]))
            except (TypeError, ValueError):
                continue
    return ids
