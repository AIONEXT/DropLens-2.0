"""DropLens - Universal file indexer, organizer and AI-powered search console.

Drop any file or folder into the window and DropLens will:
  * catalogue every file, folder, archive and document silently in the background
  * extract searchable text from 25+ formats (PDF, Office, code, images via OCR ...)
  * connect to any local or cloud AI model (Ollama, LM Studio, OpenAI, ...)
  * write AI summaries, smart tags and embeddings, and answer questions with citations
  * translate extracted content into many languages
  * organize everything by category, tags and favourites, and find duplicates
  * search everything (keyword full-text AND semantic meaning) instantly
  * let an assistant call read-only tools, and accept plugins for new formats
    (see ``dropLens/extensions`` and ``dropLens/tools``)
"""

APP_NAME = "DropLens"
APP_VERSION = "2.3.0"
APP_TAGLINE = "Drop anything. Scan everything. Find it instantly."

#: PEP 396 version attribute — `pip install .` and the wheel metadata read this
#: through ``[tool.setuptools.dynamic]`` in ``pyproject.toml``, so the package,
#: the exe's file properties and the installer can never disagree.
__version__ = APP_VERSION