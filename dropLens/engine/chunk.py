"""Document chunking with provenance.

Retrieval quality is decided here: a whole 300-page PDF collapsed into one
vector is semantically invisible past its first few thousand characters, so
:func:`chunk_text` cuts content into paragraph-aligned windows that keep

  * an ordinal (position in the document),
  * a page number when the extractor preserved page/slide structure,
  * ``start_char`` / ``end_char`` offsets into the stored text,

which is what lets the assistant cite a paragraph and the UI jump to it.

Page structure
--------------
``extract.py`` separates PDF pages with a form feed (``"\\f"``) and PowerPoint
slides / Excel sheets with ``== Slide N ==`` / ``== Sheet: name ==`` lines.
Both are recognised here; everything else is treated as one continuous page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

PAGE_MARK = "\f"

DEFAULT_TARGET_CHARS = 3200   # ~800-900 tokens of English prose
DEFAULT_OVERLAP = 0.15
MIN_CHARS = 200

_SLIDE_RE = re.compile(r"^==\s*Slide\s+(\d+)\s*==\s*$")
_SHEET_RE = re.compile(r"^==\s*Sheet:\s*(.+?)\s*==\s*$")


@dataclass(frozen=True)
class Chunk:
    ord: int
    page: int | None
    start_char: int
    end_char: int
    text: str
    token_est: int

    def label(self) -> str:
        return f"p.{self.page}" if self.page else ""


def _page_spans(text: str) -> list[tuple[int, int, int | None]]:
    """Split *text* into (start, end, page) spans.

    ``page`` is a 1-based number for form-feed separated pages and for
    numbered slides; ``None`` when the document has no usable structure.
    """
    spans: list[tuple[int, int, int | None]] = []
    if PAGE_MARK in text:
        pos = 0
        page = 1
        for part in text.split(PAGE_MARK):
            spans.append((pos, pos + len(part), page))
            pos += len(part) + len(PAGE_MARK)
            page += 1
        # trim empty spans but keep numbering
        return [(s, e, p) for s, e, p in spans if e > s]

    # slide / sheet markers: page = ordinal of the marked section
    marks: list[tuple[int, int, int | None]] = []
    for m in _SLIDE_RE.finditer(text):
        try:
            marks.append((m.start(), m.end(), int(m.group(1))))
        except ValueError:  # pragma: no cover - defensive
            continue
    if not marks:
        for i, m in enumerate(_SHEET_RE.finditer(text), 1):
            marks.append((m.start(), m.end(), i))
    if not marks:
        return [(0, len(text), None)]

    if marks[0][0] > 0:
        spans.append((0, marks[0][0], None))
    for i, (start, end, page) in enumerate(marks):
        stop = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        spans.append((end, stop, page))
    return [(s, e, p) for s, e, p in spans if e > s]


def _paragraphs(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """(start, end) spans of blank-line separated paragraphs inside a span."""
    out: list[tuple[int, int]] = []
    pos = start
    for m in re.finditer(r"\n[ \t]*\n", text[start:end]):
        stop = start + m.start()
        if stop > pos:
            out.append((pos, stop))
        pos = start + m.end()
    if end > pos:
        out.append((pos, end))
    return out


def _sentences(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Split an oversize paragraph on sentence boundaries (hard cut as last resort)."""
    out: list[tuple[int, int]] = []
    pos = start
    for m in re.finditer(r"[^.!?\n]{1,400}(?:[.!?]+|\n|$)", text[start:end]):
        stop = start + m.end()
        if stop <= pos:
            continue
        piece_start = pos
        while stop - piece_start > target:
            cut = text.rfind(" ", piece_start, piece_start + target)
            cut = cut if cut > piece_start else piece_start + target
            out.append((piece_start, cut))
            piece_start = cut
        if stop > piece_start:
            out.append((piece_start, stop))
        pos = stop
    if end > pos:
        out.append((pos, end))
    return out


def _hard_split(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    """Cut a span into <= target pieces, preferring spaces over raw offsets."""
    out: list[tuple[int, int]] = []
    pos = start
    while end - pos > target:
        cut = text.rfind(" ", pos, pos + target)
        if cut <= pos:
            cut = pos + target
        out.append((pos, cut))
        pos = cut
    if end > pos:
        out.append((pos, end))
    return out


def _blocks(text: str, start: int, end: int, target: int) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    for s, e in _paragraphs(text, start, end):
        if e - s <= target:
            blocks.append((s, e))
        else:
            for s2, e2 in _sentences(text, s, e, target):
                if e2 - s2 <= target:
                    blocks.append((s2, e2))
                else:
                    blocks.extend(_hard_split(text, s2, e2, target))
    return blocks


def chunk_text(
    text: str | None,
    *,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap: float = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Split *text* into paragraph-aligned overlapping windows."""
    content = (text or "").strip()
    if not content:
        return []
    target = max(400, int(target_chars))
    overlap_chars = max(0, min(int(target * max(0.0, overlap)), target // 2))

    chunks: list[Chunk] = []
    for span_start, span_end, page in _page_spans(content):
        windows: list[tuple[int, int]] = []
        cur: list[int] | None = None
        for s, e in _blocks(content, span_start, span_end, target):
            if cur is None:
                cur = [s, e]
            elif e - cur[0] <= target:
                cur[1] = e
            else:
                windows.append((cur[0], cur[1]))
                cur = [s, e]
        if cur is not None:
            windows.append((cur[0], cur[1]))

        prev_end: int | None = None
        for s, e in windows:
            if e - s < MIN_CHARS and windows and (s, e) != windows[-1]:
                continue
            start = max(span_start, s - overlap_chars) if prev_end is not None else s
            body = content[start:e].strip()
            if not body:
                prev_end = e
                continue
            chunks.append(Chunk(
                ord=len(chunks),
                page=page,
                start_char=start,
                end_char=e,
                text=body,
                token_est=max(1, len(body) // 4),
            ))
            prev_end = e
    return chunks


def page_for_offset(chunks: list[Chunk], offset: int) -> int | None:
    """Page number containing *offset* (None when unknown)."""
    for c in chunks:
        if c.start_char <= offset <= c.end_char:
            return c.page
    return None
