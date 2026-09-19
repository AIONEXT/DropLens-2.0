"""Directory walking and file fingerprinting."""
from __future__ import annotations

import dataclasses
import hashlib
import os
from typing import Callable, Iterator

DEFAULT_IMPORTANT = True


@dataclasses.dataclass
class FileInfo:
    path: str
    name: str
    ext: str
    size: int
    mtime: float
    ctime: float
    root: str
    kind: str = "file"   # "file" | "folder"


def walk_root(
    root: str,
    ignore_names: list[str] | None = None,
    on_error: Callable[[str, str], None] | None = None,
    include_folders: bool = True,
) -> Iterator[FileInfo]:
    """Yield a :class:`FileInfo` for the root itself, every folder and file.

    Safe against permission errors and junction cycles.
    """
    ignore = {n.lower() for n in (ignore_names or []) if n}
    root = os.path.abspath(root)
    visited: set[str] = set()

    def should_ignore(name: str) -> bool:
        low = name.lower()
        return low in ignore or low.endswith(".tmp")

    if os.path.isfile(root):
        yield _file_info(root, root_dir=os.path.dirname(root))
        return

    if not os.path.isdir(root):
        return

    # yield the root directory itself
    if include_folders:
        yield _folder_info(root, root_dir=root)

    stack = [root]
    while stack:
        current = stack.pop()
        try:
            real = os.path.realpath(current)
            if real in visited:
                continue
            visited.add(real)
            entries = sorted(os.scandir(current), key=lambda e: e.name.lower(), reverse=True)  # stack -> alpha order
        except (OSError, RuntimeError) as exc:
            if on_error:
                on_error(current, str(exc))
            continue
        for entry in entries:
            try:
                if should_ignore(entry.name):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if include_folders:
                        yield _folder_info(entry.path, root_dir=root)
                    stack.append(entry.path)
                else:
                    yield _file_info(entry.path, root_dir=root)
            except OSError as exc:
                if on_error:
                    on_error(entry.path, str(exc))
                continue


def _file_info(path: str, root_dir: str) -> FileInfo:
    st = os.stat(path)
    name = os.path.basename(path)
    return FileInfo(
        path=os.path.abspath(path),
        name=name,
        ext=os.path.splitext(name)[1].lstrip(".").lower() if "." in name else "",
        size=st.st_size,
        mtime=st.st_mtime,
        ctime=getattr(st, "st_ctime", 0.0) or 0.0,
        root=root_dir,
        kind="file",
    )


def _folder_info(path: str, root_dir: str) -> FileInfo:
    try:
        st = os.stat(path)
    except OSError:
        st = os.stat_result((0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    return FileInfo(
        path=os.path.abspath(path),
        name=os.path.basename(path) or os.path.splitdrive(path)[0] + os.sep,
        ext="",
        size=0,
        mtime=st.st_mtime,
        ctime=getattr(st, "st_ctime", 0.0) or 0.0,
        root=root_dir,
        kind="folder",
    )


def quick_signature(root: str) -> tuple[int, float]:
    """Cheap footprint for change detection: (entry count, newest mtime)."""
    count = 0
    newest = 0.0
    try:
        for ent in walk_root(root, include_folders=True):
            count += 1
            if ent.mtime > newest:
                newest = ent.mtime
    except Exception:
        pass
    return count, newest


def hash_file(path: str, chunk: int = 1 << 20) -> str | None:
    """MD5 content hash; returns hex or None on error."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(chunk)
                if not block:
                    break
                h.update(block)
    except OSError:
        return None
    return h.hexdigest() if h is not None else None


def human_size(n: int | float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"