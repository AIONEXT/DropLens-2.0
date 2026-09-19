"""Runtime resource resolution (paths, logging, optional binaries)."""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import APP_NAME

LOG_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def data_dir() -> str:
    """Return (and create) the directory where DropLens stores its database."""
    cfg_dir = os.environ.get("DROPLENS_DIR")
    if cfg_dir:
        base = cfg_dir
    else:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        path = os.path.join(os.path.expanduser("~"), "." + APP_NAME.lower())
        os.makedirs(path, exist_ok=True)
    return path


def db_path() -> str:
    return os.path.join(data_dir(), "catalog.db")


def settings_path() -> str:
    return os.path.join(data_dir(), "settings.json")


def log_path() -> str:
    return os.path.join(data_dir(), "droplens.log")


def app_dir() -> str:
    """Directory of the running executable / source checkout (works in PyInstaller)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_tesseract() -> str | None:
    """Locate tesseract.exe in common install locations."""
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\%s\AppData\Local\Programs\Tesseract-OCR\tesseract.exe" % os.environ.get("USERNAME", ""),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    path = os.environ.get("TESSERACT_PATH", "")
    if path and os.path.isfile(path):
        return path
    return None


def setup_logging(level: str = "info") -> logging.Logger:
    logger = logging.getLogger(APP_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(LOG_LEVELS.get(level, logging.INFO))
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        handler = RotatingFileHandler(log_path(), maxBytes=5 * 1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except OSError:
        pass
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)
    logger.propagate = False
    return logger