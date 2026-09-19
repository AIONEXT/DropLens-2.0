"""Automatic categorization of files by extension."""
from __future__ import annotations

CATEGORY_MAP = {
    # (category, kind) -> extensions
    "document": [
        "pdf", "doc", "docx", "docm", "dotx", "dotm", "rtf", "txt", "text",
        "odt", "ott", "wpd", "tex", "md", "markdown", "rst", "pages",
    ],
    "spreadsheet": [
        "xls", "xlsx", "xlsm", "xltx", "xltm", "ods", "ots", "numbers", "csv", "tsv",
    ],
    "presentation": ["ppt", "pptx", "pptm", "odp", "key"],
    "code": [
        "py", "pyw", "js", "jsx", "ts", "tsx", "java", "c", "cpp", "h", "hpp",
        "cs", "vb", "go", "rs", "rb", "php", "sh", "bat", "cmd", "ps1", "psm1",
        "sql", "pl", "lua", "r", "m", "scala", "kt", "swift", "html", "css",
        "scss", "less", "vue", "svelte", "dockerfile", "makefile", "cmake",
        "gradle", "toml", "ini", "cfg", "conf", "yaml", "yml", "json", "xml",
        "properties", "env", "gitignore", "dockerignore", "editorconfig",
    ],
    "data": [
        "json", "xml", "yaml", "yml", "csv", "tsv", "sql", "db", "sqlite",
        "sqlite3", "parquet", "avro", "proto", "dbf", "ndjson", "jsonl",
    ],
    "image": [
        "png", "jpg", "jpeg", "gif", "bmp", "tiff", "tif", "webp", "svg", "ico",
        "heic", "jfif", "psd", "ai", "eps",
    ],
    "audio": ["mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "wma", "amr", "mid"],
    "video": ["mp4", "mkv", "avi", "mov", "wmv", "webm", "flv", "m4v", "mpg", "mpeg", "3gp"],
    "archive": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "zst", "cab", "iso"],
    "email": ["eml", "msg"],
    "ebook": ["epub", "mobi", "azw", "azw3", "fb2", "djvu"],
    "web": ["html", "htm", "xhtml", "mhtml", "url", "webloc"],
    "font": ["ttf", "otf", "woff", "woff2"],
    "other": [],
}

_REVERSE: dict[str, str] = {}
for _cat, _exts in CATEGORY_MAP.items():
    for _e in _exts:
        _REVERSE[_e] = _cat


def categorize(ext: str) -> str:
    """Return a stable category label for a file extension (lowercase, no dot)."""
    clean = (ext or "").lstrip(".").lower()
    return _REVERSE.get(clean, "other")


CATEGORY_LABELS = {
    "document": "Document",
    "spreadsheet": "Spreadsheet",
    "presentation": "Presentation",
    "code": "Code",
    "data": "Data",
    "image": "Image",
    "audio": "Audio",
    "video": "Video",
    "archive": "Archive",
    "email": "Email",
    "ebook": "eBook",
    "web": "Web",
    "font": "Font",
    "other": "Other",
}


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(category, category.title())


def all_categories() -> list[str]:
    return list(CATEGORY_LABELS.keys())