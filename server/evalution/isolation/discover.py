"""The stores a project folder names, read with no model and no app running.

Four kinds of evidence, each kept as ``file:line`` so a person can check it:

* **Packages**: a database driver or a memory library in the package files.
* **Env names**: ``os.getenv("X")``, ``os.environ["X"]``, ``process.env.X`` and ``.env.example``
  keys whose name or example value says what they point at (``DATABASE_URL``, ``REDIS_URL``,
  ``CHROMA_PATH``...). Only ``.env.example`` values are read, and a URL's user and password are
  dropped. ``.env`` itself is never read here.
* **Compose**: a service running a known store image, and the volumes the file declares.
* **Literals in code**: a ``.db`` / ``.sqlite`` path, a ``sqlite:///`` URL, a persistent vector
  client's path, and in-memory stores (gone with the process, so a restart isolates them).

Stores found more than once are merged by kind and locator, keeping every piece of evidence.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from evalution.code import SKIP_DIRS, is_secret

#: Files read at most, and the most of each.
MAX_FILES, MAX_BYTES = 4000, 400_000

_TEXT = re.compile(
    r"\.(py|ts|tsx|js|mjs|cjs|go|rs|rb|java|kt|toml|ya?ml|json|cfg|ini|sql)$|"
    r"(^|/)(\.env\.example|\.env\.sample|Dockerfile|requirements[^/]*\.txt)$"
)
#: Tests name throwaway stores of their own, never the app's.
_TEST_DIR = {"tests", "test", "__tests__", "spec", "specs", "fixtures", "testdata", "e2e"}
_TEST_FILE = re.compile(r"^(test_.*|.*_test\.\w+|.*\.(test|spec)\.\w+|conftest\.py)$")
_SKIP = SKIP_DIRS | {".codegraph", "coverage", "target", "vendor", ".mypy_cache", ".ruff_cache"}

#: Package names, as they appear in package files, and the store kind each means.
PACKAGES: dict[str, str] = {
    "psycopg": "postgres",
    "psycopg2": "postgres",
    "asyncpg": "postgres",
    "pg": "postgres",
    "postgres": "postgres",
    "@neondatabase/serverless": "postgres",
    "pymysql": "mysql",
    "mysqlclient": "mysql",
    "mysql2": "mysql",
    "aiosqlite": "sqlite",
    "better-sqlite3": "sqlite",
    "sqlite3": "sqlite",
    "@libsql/client": "sqlite",
    "redis": "redis",
    "ioredis": "redis",
    "pymongo": "mongo",
    "motor": "mongo",
    "mongoose": "mongo",
    "mongodb": "mongo",
    "chromadb": "vector",
    "qdrant-client": "vector",
    "@qdrant/js-client-rest": "vector",
    "lancedb": "vector",
    "faiss-cpu": "vector",
    "pinecone": "hosted",
    "pinecone-client": "hosted",
    "@pinecone-database/pinecone": "hosted",
    "mem0ai": "hosted",
    "zep-python": "hosted",
}
#: Images a compose service runs, by the start of their name.
IMAGES = {
    "postgres": "postgres",
    "pgvector/": "postgres",
    "timescale/": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "redis": "redis",
    "valkey/": "redis",
    "mongo": "mongo",
    "qdrant/": "vector",
    "chromadb/": "vector",
}
_SCHEMES = {
    "postgres": "postgres",
    "postgresql": "postgres",
    "mysql": "mysql",
    "sqlite": "sqlite",
    "redis": "redis",
    "rediss": "redis",
    "mongodb": "mongo",
    "mongodb+srv": "mongo",
}
_ENV_CODE = re.compile(
    r"""(?:getenv\(\s*|environ(?:\.get\(\s*|\[\s*)|process\.env\.|process\.env\[\s*|Deno\.env\.get\(\s*)["']?([A-Z][A-Z0-9_]{2,})"""
)
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,})\s*=\s*(.*)$")
_URL = re.compile(r"\b((?:postgres(?:ql)?|mysql|sqlite|rediss?|mongodb(?:\+srv)?)://[^\s\"'`]*)")
_DB_FILE = re.compile(r"""["'`]([^"'`\s]*\.(?:db|sqlite3?|duckdb))["'`]""")
_PERSIST = re.compile(
    r"""(?:PersistentClient|persist_directory|lancedb\.connect)\s*[(=]\s*(?:path\s*=\s*)?["']([^"']+)["']"""
)
_IN_MEMORY = re.compile(r"\b(InMemoryStore|MemorySaver|InMemorySaver|:memory:|EphemeralClient)\b")
_IMAGE = re.compile(r"^\s*image:\s*[\"']?([\w./-]+)")
_SERVICE = re.compile(r"^  ([\w.-]+):\s*$")
_VOLUME = re.compile(r"^\s*-\s*[\"']?([\w./${}:-]+):(/[\w./-]+)")

#: An env name's words and the kind they suggest, checked in order.
_ENV_KINDS = (
    (re.compile(r"POSTGRES|PG_|^PG|DATABASE_URL|DB_URL|DSN"), "postgres"),
    (re.compile(r"MYSQL"), "mysql"),
    (re.compile(r"SQLITE"), "sqlite"),
    (re.compile(r"REDIS|VALKEY"), "redis"),
    (re.compile(r"MONGO"), "mongo"),
    (re.compile(r"CHROMA|QDRANT|LANCE|VECTOR|FAISS"), "vector"),
    (
        re.compile(r"(DATA|STORAGE|UPLOAD|CACHE|MEMORY|STATE)_(DIR|PATH|ROOT|FOLDER)|_DB_PATH"),
        "dir",
    ),
)
_NOT_STORE = re.compile(r"PASSWORD|USER(NAME)?$|_PORT$|_HOST$|_KEY$|TOKEN|SECRET")


@dataclass
class Store:
    """One place the app may keep state.

    :param kind: ``postgres``, ``mysql``, ``sqlite``, ``redis``, ``mongo``, ``vector``, ``dir``,
        ``volume``, ``hosted`` or ``memory``.
    :param locator: What moves or names it: ``env:NAME``, a path, ``volume:NAME``, ``package:NAME``.
    """

    kind: str
    locator: str
    evidence: list[str] = field(default_factory=list)
    #: Filled by :func:`evalution.isolation.observe.reconcile`.
    seen: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return self.kind, self.locator


def _clean_url(url: str) -> str:
    """A URL without its user and password."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}{parts.path}"


_PLACEHOLDER_HOSTS = {"host", "x", "example.com", "hostname", "user", "db", "server", "..."}


def _placeholder(url: str) -> bool:
    """A URL from a docstring or an example, not a store: no host or path, or a made-up host."""
    rest = url.split("://", 1)[1]
    if rest in ("", "/", "/:memory:") or rest.startswith(":memory:"):
        return True
    host = (urlsplit(url).hostname or "").lower()
    return host in _PLACEHOLDER_HOSTS or host.endswith((".example", ".invalid"))


def env_kind(name: str, example: str = "") -> str | None:
    """The kind of store an env name (and its example value) points at, if any."""
    if (m := _URL.search(example)) is not None:
        return _SCHEMES.get(m.group(1).split("://", 1)[0])
    if _NOT_STORE.search(name):
        return None
    return next((kind for pattern, kind in _ENV_KINDS if pattern.search(name)), None)


def files(folder: Path) -> dict[str, str]:
    """``relative path -> text`` of what can name a store. Never a secrets file."""
    out: dict[str, str] = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        parts = Path(rel).parts
        if any(
            p in _SKIP or (p.startswith(".") and p not in (".env.example", ".env.sample"))
            for p in parts
        ):
            continue
        if not path.is_file() or not _TEXT.search(rel) or is_secret(rel) or rel.endswith(".lock"):
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            out[rel] = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(out) >= MAX_FILES:
            break
    return out


def _package_names(rel: str, text: str) -> list[tuple[int, str]]:
    name = rel.rsplit("/", 1)[-1]
    found: list[tuple[int, str]] = []
    if name == "package.json":
        try:
            data = json.loads(text)
        except ValueError:
            return []
        lines = text.splitlines()
        for section in ("dependencies", "devDependencies", "optionalDependencies"):
            for pkg in data.get(section) or {}:
                n = next((i for i, ln in enumerate(lines, 1) if f'"{pkg}"' in ln), 1)
                found.append((n, pkg.lower()))
        return found
    for n, line in enumerate(text.splitlines(), 1):
        if name in ("pyproject.toml", "setup.py", "setup.cfg") or name.startswith("requirements"):
            m = re.match(
                r"""\s*["']?([A-Za-z][\w.-]*)(?:\[[^\]]*\])?\s*(?:[<>=~!;,"'\]]|$)""", line
            )
        else:
            return []
        if m:
            found.append((n, m.group(1).lower()))
    return found


def scan(folder: Path) -> list[Store]:
    """Every store the folder names, merged, the most evidenced first."""
    stores: dict[tuple[str, str], Store] = {}

    def add(kind: str, locator: str, where: str) -> None:
        if "{" in locator and not locator.startswith("${"):
            return  # a template filled at run time, like f"sqlite:///{path}"
        store = stores.setdefault((kind, locator), Store(kind, locator))
        if where not in store.evidence:
            store.evidence.append(where)

    for rel, text in files(folder).items():
        for n, pkg in _package_names(rel, text):
            if (kind := PACKAGES.get(pkg)) is not None:
                add(kind, f"package:{pkg}", f"{rel}:{n}")
        is_example = rel.rsplit("/", 1)[-1].startswith(".env.")
        is_compose = "compose" in rel.rsplit("/", 1)[-1] and rel.endswith((".yaml", ".yml"))
        service = ""
        for n, line in enumerate(text.splitlines(), 1):
            where = f"{rel}:{n}"
            if is_example and (m := _ENV_LINE.match(line)):
                if kind := env_kind(m.group(1), m.group(2)):
                    add(kind, f"env:{m.group(1)}", where)
                continue
            for name in _ENV_CODE.findall(line):
                if kind := env_kind(name):
                    add(kind, f"env:{name}", where)
            if is_compose:
                if m := _SERVICE.match(line):
                    service = m.group(1)
                if (m := _IMAGE.match(line)) is not None:
                    image = m.group(1).lower()
                    kind = next((k for p, k in IMAGES.items() if image.startswith(p)), None)
                    if kind:
                        add(kind, f"service:{service or image}", where)
                if (m := _VOLUME.match(line)) is not None and not m.group(1).endswith(".sock"):
                    source = m.group(1)
                    if source.startswith((".", "/", "~", "$")):
                        if not re.search(r"\.(sql|sh|conf|ya?ml|json|py)$", source):
                            add("dir", source, where)
                    else:
                        add("volume", f"volume:{source}", where)
                continue
            for url in _URL.findall(line):
                if _placeholder(url):
                    continue
                if (kind := _SCHEMES.get(url.split("://", 1)[0])) is not None:
                    add(kind, _clean_url(url) if kind != "sqlite" else url, where)
            for path in _DB_FILE.findall(line):
                add("sqlite", path, where)
            for path in _PERSIST.findall(line):
                add("vector", path, where)
            if (m := _IN_MEMORY.search(line)) is not None:
                add("memory", m.group(1), where)
    return sorted(stores.values(), key=lambda s: (-len(s.evidence), s.kind, s.locator))
