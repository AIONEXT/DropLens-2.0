"""Content extraction for many file formats.

Every extractor returns a string of searchable text (or None when the file
contains no extractable text). Extraction never modifies the source file, so
original content and quality are never compromised — extracted text is only a
read-only index copy stored in the searchable catalogue.

Formats covered: plain text & code (auto-detected encoding), PDF, Office
(Word/Excel/PowerPoint/OpenDocument), RTF, e-books (EPUB/DJVU), archives
(contents listing), e-mail, classic binary Office, and images via OCR when
Tesseract is installed.
"""
from __future__ import annotations

import os
import re
import tarfile
import zipfile
from typing import Optional

from charset_normalizer import from_bytes

# --------------------------------------------------------------------------
# Optional heavy dependencies (fail gracefully when missing)
# --------------------------------------------------------------------------
try:
    import pymupdf as _fitz
except ImportError:  # pragma: no cover
    try:
        import fitz as _fitz  # type: ignore
    except ImportError:
        _fitz = None

try:
    import docx  # python-docx
except ImportError:  # pragma: no cover
    docx = None

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None

try:
    from pptx import Presentation
except ImportError:  # pragma: no cover
    Presentation = None

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    import pytesseract
except ImportError:  # pragma: no cover
    pytesseract = None

try:
    import extract_msg
except ImportError:  # pragma: no cover
    extract_msg = None

_MAX_CONTENT = int(os.environ.get("DROPLENS_MAX_CONTENT", 5_000_000))

_TEXTLIKE_MAX = 512 * 1024  # sniff/decode limit for "binary-looking" safety

# extensions we know are part of the auto-detected text family
_TEXT_EXTENSIONS = {
    "txt", "text", "md", "markdown", "rst", "log", "csv", "tsv", "json",
    "jsonl", "ndjson", "xml", "html", "htm", "xhtml", "css", "scss", "less",
    "js", "jsx", "ts", "tsx", "py", "pyw", "java", "c", "cpp", "h", "hpp",
    "cs", "vb", "go", "rs", "rb", "php", "sh", "bat", "cmd", "ps1", "psm1",
    "sql", "pl", "lua", "r", "m", "scala", "kt", "swift", "vue", "svelte",
    "ini", "cfg", "conf", "yaml", "yml", "toml", "properties", "env",
    "gitignore", "dockerignore", "dockerfile", "makefile", "cmake", "gradle",
    "url", "webloc", "srt", "vtt", "nfo", "diz", "diff", "patch", "reg",
    "ico", "svgz", "map",
}


# --------------------------------------------------------------------------
# Generic helpers
# --------------------------------------------------------------------------
def _decode_bytes(raw: bytes) -> Optional[str]:
    """Robustly decode arbitrary bytes into text."""
    if not raw:
        return None
    if b"\x00" in raw[: min(len(raw), _TEXTLIKE_MAX)]:
        return None  # binary null bytes -> not plain text
    # strip UTF-8 / UTF-16 BOMs and also handle UTF-16 content
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        best = from_bytes(raw)
        return best.best().output_str
    except Exception:
        pass
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return None


def _clean(parts: list[str]) -> str:
    """Normalize a sequence of extracted text parts."""
    kept = []
    for p in parts:
        if not p:
            continue
        p = p.replace("\x00", "")
        kept.append(p)
    if not kept:
        return ""
    text = "\n".join(kept)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _limit(text: str) -> str:
    if len(text) > _MAX_CONTENT:
        return text[:_MAX_CONTENT]
    return text


# --------------------------------------------------------------------------
# Text / code
# --------------------------------------------------------------------------
def _extract_text_file(path: str) -> Optional[str]:
    with open(path, "rb") as fh:
        raw = fh.read(_TEXTLIKE_MAX * 4)
    if len(raw) >= _TEXTLIKE_MAX * 4 and os.path.getsize(path) > len(raw):
        # huge file: still try decoding the head
        pass
    # archives / media inside a text extension? format maps dispatch elsewhere
    return _decode_bytes(raw)


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def _extract_pdf(path: str) -> Optional[str]:
    if _fitz is None:
        return None
    out: list[str] = []
    try:
        with _fitz.open(path) as doc:
            for page in doc:
                out.append(page.get_text("text") or "")
                if sum(len(o) for o in out) > _MAX_CONTENT:
                    break
    except Exception:
        return None
    return _clean(out) or None


# --------------------------------------------------------------------------
# Office
# --------------------------------------------------------------------------
def _extract_docx(path: str) -> Optional[str]:
    if docx is None:
        return None
    out: list[str] = []
    try:
        d = docx.Document(path)
        for p in d.paragraphs:
            if p.text:
                out.append(p.text)
        for tbl in d.tables:
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells]
                out.append(" | ".join(cells))
        for shape in getattr(d, "_element", iter(())):
            pass
    except Exception:
        return None
    return _clean(out) or None


def _extract_xlsx(path: str) -> Optional[str]:
    if openpyxl is None:
        return None
    out: list[str] = []
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            out.append(f"== Sheet: {ws.title} ==")
            rows = 0
            for row in ws.iter_rows(values_only=True):
                vals = [str(v) if v is not None else "" for v in row]
                line = " | ".join(vals).strip()
                if line:
                    out.append(line)
                rows += 1
                if rows > 2000:
                    out.append("… (sheet truncated)")
                    break
        wb.close()
    except Exception:
        return None
    return _clean(out) or None


def _extract_pptx(path: str) -> Optional[str]:
    if Presentation is None:
        return None
    out: list[str] = []
    try:
        prs = Presentation(path)
        for i, slide in enumerate(prs.slides, 1):
            out.append(f"== Slide {i} ==")
            for shape in slide.shapes:
                if shape.has_text_frame:
                    for para in shape.text_frame.paragraphs:
                        t = "".join(run.text for run in para.runs)
                        if t.strip():
                            out.append(t)
                if shape.shape_type == 19:  # table
                    try:
                        tbl = shape.table
                        for row in tbl.rows:
                            cells = [c.text.strip() for c in row.cells]
                            if any(cells):
                                out.append(" | ".join(cells))
                    except Exception:
                        pass
        if prs.core_properties and prs.core_properties.notes:
            pass
    except Exception:
        return None
    return _clean(out) or None


def _extract_odf(path: str) -> Optional[str]:
    """OpenDocument (odt/ods/odp) — plain XML text inside a zip container."""
    out: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            if "content.xml" in zf.namelist():
                xml = zf.read("content.xml").decode("utf-8", "replace")
            elif "word/document.xml" in zf.namelist():
                xml = zipfile.ZipFile(path).read("word/document.xml").decode("utf-8", "replace")
            else:
                return None
        # strip tags, keep table cells
        xml = re.sub(r"<text:tab[^>]*/>", " | ", xml)
        xml = re.sub(r"<[^>]+>", "\n", xml)
        xml = re.sub(r"&lt;", "<", xml)
        xml = re.sub(r"&gt;", ">", xml)
        xml = re.sub(r"&amp;", "&", xml)
        xml = re.sub(r"\n{2,}", "\n", xml)
        lines = [l.strip() for l in xml.splitlines() if l.strip()]
        out.extend(lines)
    except Exception:
        return None
    return _clean(out) or None


def _extract_rtf(path: str) -> Optional[str]:
    """RFC 1892 RTF: strip control words and group braces."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        text = raw.decode("cp1252", "replace").replace("\\u", "\\u")
    except Exception:
        return None
    if "\\rtf" not in text[:2000].lower():
        return _decode_bytes(raw)
    # unicode escapes \uN? to char
    def _unesc(m):
        try:
            return chr(int(m.group(1)))
        except ValueError:
            return ""
    text = re.sub(r"\\u(-?\d+)\??", _unesc, text)
    text = re.sub(r"\\curprop\s*[^;]*;.*$", "", text, flags=re.S)
    text = re.sub(r"\\\*\\[A-Za-z]+\s*", " ", text)  # ignored destinations
    text = re.sub(r"\\[A-Za-z]+-?\d* ?", "", text)     # control words
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), text)
    text = re.sub(r"[{}]", "", text)
    # hex runs \'NN inside \u conversion already handled; drop leftover hex blocks
    text = re.sub(r"\\par", "\n", text)
    text = re.sub(r"\\line", "\n", text)
    text = re.sub(r"\\tab", "\t", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return _clean(lines) or None


# --------------------------------------------------------------------------
# Archives (contents listing), e-books, e-mail
# --------------------------------------------------------------------------
def _extract_archive(path: str) -> Optional[str]:
    out: list[str] = []
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".zip" or ext == ".epub":
            with zipfile.ZipFile(path) as zf:
                for info in zf.infolist():
                    if not info.is_dir():
                        out.append(info.filename)
        elif ext == ".tar" or ext == ".gz" or ext == ".bz2" or ext == ".xz" or ext == ".tgz":
            mode = "r:*"
            with tarfile.open(path, mode) as tf:
                for m in tf.getmembers():
                    if m.isfile():
                        out.append(m.name)
        elif ext == ".7z" or ext == ".rar":
            try:
                import py7zr
                with py7zr.SevenZipFile(path) as z:
                    for name in z.getnames():
                        out.append(name)
            except ImportError:
                return None
            except Exception:
                return None
        else:
            return None
    except Exception:
        return None
    return _clean(out) or ("(empty archive)" if not out and os.path.exists(path) else None)


def _extract_epub(path: str) -> Optional[str]:
    out: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.endswith((".html", ".xhtml", ".htm", ".opf", ".ncx"))]
            for n in names[:120]:
                try:
                    xml = zf.read(n).decode("utf-8", "replace")
                except Exception:
                    continue
                xml = re.sub(r"<[^>]+>", " ", xml)
                xml = re.sub(r"&nbsp;?", " ", xml)
                xml = re.sub(r"&\S+?;", " ", xml)
                xml = re.sub(r"\s{2,}", " ", xml).strip()
                if xml:
                    out.append(xml)
    except Exception:
        return None
    return _clean(out) or None


def _extract_eml(path: str) -> Optional[str]:
    import email
    from email import policy
    out: list[str] = []
    try:
        with open(path, "rb") as fh:
            msg = email.message_from_binary_file(fh, policy=policy.default)
        out.append(f"From: {msg.get('From','')}")
        out.append(f"To: {msg.get('To','')}")
        out.append(f"Subject: {msg.get('Subject','')}")
        out.append(f"Date: {msg.get('Date','')}")
        body = msg.get_body(preferencelist=("plain", "html"))
        if body:
            txt = body.get_content()
            if body.get_content_type() == "text/html":
                txt = re.sub(r"<[^>]+>", " ", txt)
            out.append(txt or "")
        for part in msg.walk():
            fn = part.get_filename()
            if fn:
                out.append(f"[attachment: {fn}]")
    except Exception:
        return None
    return _clean(out) or None


def _extract_msg_file(path: str) -> Optional[str]:
    if extract_msg is None:
        return None
    out: list[str] = []
    try:
        m = extract_msg.Message(path)
        out.append(f"From: {m.sender or ''}")
        out.append(f"To: {m.to or ''}")
        out.append(f"Subject: {m.subject or ''}")
        body = getattr(m, "body", None) or str(m.body or "")
        out.append(body)
        m.close()
    except Exception:
        return None
    return _clean(out) or None


# --------------------------------------------------------------------------
# Legacy binary office
# --------------------------------------------------------------------------
_LATIN_RE = re.compile(r"[\x20-\x7e\xa0-\xff]{4,}")


def _extract_legacy_doc(path: str) -> Optional[str]:
    """Best-effort readable-string extraction for .doc/.xls/.ppt binaries."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(2_000_000)
    except OSError:
        return None
    parts = []
    chunks = []
    for m in _LATIN_RE.finditer(raw.decode("latin-1", "replace")):
        s = m.group(0)
        if s.strip():
            chunks.append(s.strip())
    if not chunks:
        return None
    return _clean(chunks)


# --------------------------------------------------------------------------
# Images -> OCR (optional)
# --------------------------------------------------------------------------
def _extract_image_ocr(path: str, tesseract_path: str = "") -> Optional[str]:
    if pytesseract is None or Image is None:
        return None
    try:
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        img = Image.open(path)
        img.load()
        # downscale very large images for faster, more reliable OCR
        max_dim = 2600
        if max(img.size) > max_dim:
            img.thumbnail((max_dim, max_dim))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        return pytesseract.image_to_string(img)
    except Exception:
        return None


# --------------------------------------------------------------------------
# Dispatch table
# --------------------------------------------------------------------------
_BINARY_EXTRACTORS = {
    "pdf": _extract_pdf,
    "docx": _extract_docx,
    "docm": _extract_docx,
    "doc": _extract_legacy_doc,
    "rtf": _extract_rtf,
    "xlsx": _extract_xlsx,
    "xlsm": _extract_xlsx,
    "xls": _extract_legacy_doc,
    "pptx": _extract_pptx,
    "pptm": _extract_pptx,
    "ppt": _extract_legacy_doc,
    "odt": _extract_odf,
    "ods": _extract_odf,
    "odp": _extract_odf,
    "zip": _extract_archive,
    "tar": _extract_archive,
    "gz": _extract_archive,
    "bz2": _extract_archive,
    "xz": _extract_archive,
    "tgz": _extract_archive,
    "7z": _extract_archive,
    "rar": _extract_archive,
    "epub": _extract_epub,
    "eml": _extract_eml,
    "msg": _extract_msg_file,
}

_IMAGE_EXTS = {"png", "jpg", "jpeg", "bmp", "tiff", "tif", "gif", "webp", "ico"}


def extract_content(path: str, *, ocr_enabled: bool = True, tesseract_path: str = "") -> tuple[Optional[str], str]:
    """Extract full-text content from *path*.

    Returns ``(content, status)``:
      content   extracted text or None
      status    one of: indexed / no_content / failed / ocr_unavailable
    """
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    try:
        if ext in _BINARY_EXTRACTORS:
            text = _BINARY_EXTRACTORS[ext](path)
        elif ext in _TEXT_EXTENSIONS:
            text = _extract_text_file(path)
        elif ext in _IMAGE_EXTS:
            if not ocr_enabled or pytesseract is None:
                return None, "ocr_unavailable"
            text = _extract_image_ocr(path, tesseract_path) or None
            if text is None:
                return None, "ocr_unavailable"
        else:
            text = None
    except Exception as exc:  # noqa: BLE001 - never let extraction kill the scan
        return None, f"failed"
    if text:
        return _limit(text), "indexed"
    return None, "no_content"


def folder_listing(dir_path: str) -> str | None:
    """Human-readable listing used as searchable content for a folder entry."""
    try:
        entries = sorted(os.listdir(dir_path))
    except OSError:
        return None
    if not entries:
        return None
    lines = [f"[folder listing: {dir_path}]"]
    for e in entries[:2000]:
        fp = os.path.join(dir_path, e)
        try:
            kind = "dir" if os.path.isdir(fp) else "file"
            size = os.path.getsize(fp) if kind == "file" else ""
            lines.append(f"{kind} | {e}" + (f" | {size} bytes" if size != "" else ""))
        except OSError:
            lines.append(f"file | {e}")
    return "\n".join(lines)