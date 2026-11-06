"""Where evalution keeps what it learns and writes (docs/architecture.md, "Storage"): one database,
SQLite in ``<data_dir>/evalution.db`` by default, any SQLAlchemy URL in ``$DATABASE_URL``.

Everything is a JSON **document** under ``(workspace, kind, key)``: a run, an Ask AI session, a
target's profile, a test. Keys are paths, ``"<target>/profile.md"`` or ``"<set>/<case>"``, so a
kind lists by prefix. A few things are **entries** instead, an ordered log under one key that
is only ever added to (a run's evidence). Secrets live in their own kind, encrypted
(:mod:`evalution.storage.secrets`).

Every read and write names its workspace through :class:`Docs`; this server's own is
:data:`LOCAL`. The calls are blocking and safe from any thread: async code runs them with
``asyncio.to_thread`` where a write may be slow, as it did with files.
"""

from evalution.storage.database import LOCAL, Database, Docs, Entry

__all__ = ["LOCAL", "Database", "Docs", "Entry"]
