"""``evalution.yaml`` (docs/architecture.md, "Config"): the model connection, the targets and
where data lives. No secrets in the file: every secret is named by a ``*_env`` key, read only
when used, through the config's :class:`KeyRing`: the env var of that name, else the secret
stored under that name in the data folder's database (the secrets of what the page set).

The file is ``$EVALUTION_CONFIG``, else ``./evalution.yaml``. Relative paths are resolved
against the file's folder (the repo root): ``sets/`` (the tests as files, which ``evalution sets
import`` and ``export`` use), ``targets/`` (profiles), ``library/`` and ``web/dist`` live there
unless the file says otherwise. ``traces:`` says how long evalution keeps the spans apps send it
and how it reads them (:class:`TracesConfig`).

What the page sets lives in the database, kind :data:`SETTINGS`, owned here: the targets
connected from the page under ``targets/<name>`` (each its block), merged in, a name in both
being the hand-edited one; and the model set from the page under ``model`` (its ``model:``
block), which a ``model:`` in the file wins over whole. The file itself is never written.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from evalution.storage import Database, Docs
from evalution.storage.secrets import SecretKeyError, Secrets

_logger = logging.getLogger(__name__)

MODEL_KINDS = ("anthropic", "openai")
MODEL_KEYS = ("strong", "light")
#: What the page sets (a document kind): :data:`MODEL_DOC` and ``targets/<name>``.
SETTINGS = "settings"
MODEL_DOC = "model"
_TARGETS = "targets/"
#: ``traces`` keys from when evalution could run Phoenix itself: still read, and left unused.
RETIRED_TRACES = ("managed", "port", "grpc", "keep_mb")
#: The stored secret that holds the model's API key, when it was set from the page.
MODEL_SECRET = "model/api_key"  # noqa: S105 (a name, not a value)
#: USD per million tokens of well-known models, for models the config does not price.
BUILTIN_PRICES: dict[str, dict[str, float]] = {
    "claude-fable-5-1": {"input": 10, "output": 50},
    "claude-fable-5": {"input": 10, "output": 50},
    "claude-opus-5-5": {"input": 4, "output": 20},
    "claude-opus-5": {"input": 5, "output": 25},
    "claude-opus-4-8": {"input": 5, "output": 25},
    "claude-opus-4-7": {"input": 5, "output": 25},
    "claude-opus-4-6": {"input": 5, "output": 25},
    "claude-sonnet-5-5": {"input": 2, "output": 10},
    "claude-sonnet-5": {"input": 2, "output": 10},
    "claude-sonnet-4-6": {"input": 3, "output": 15},
    "claude-haiku-5-5": {"input": 0.1, "output": 0.5},
    "claude-haiku-4-5": {"input": 1, "output": 5},
}
#: The names a server open to other computers answers under: wins over ``allowed_hosts``.
ALLOWED_HOSTS_ENV = "EVALUTION_ALLOWED_HOSTS"


class ConfigError(ValueError):
    """The config file does not follow the shape; the message names the field."""


class KeyRing:
    """What a ``*_env`` name stands for: the env var of that name, else the secret stored under
    that name. ``KeyRing()``, with no store, reads the environment only.

    Hosted, a workspace's ring is given *account*: the model key of whoever the work is for
    (:mod:`evalution.accounts.keys`). The environment is then the server's own, so a name never
    reads it: a workspace could otherwise name any of the server's variables and send it away."""

    def __init__(
        self, store: Secrets | None = None, account: Callable[[], str | None] | None = None
    ) -> None:
        self._store = store
        self._account = account

    @property
    def hosted(self) -> bool:
        return self._account is not None

    def account_key(self) -> str | None:
        """Hosted, the model key of whoever the work is for; ``None`` when nobody has one."""
        return self._account() if self._account else None

    @property
    def store(self) -> Secrets:
        """Where the page's secrets are kept, for setting them.

        :raises ConfigError: There is none (a config not read from a data folder).
        """
        if self._store is None:
            raise ConfigError("no data folder to keep secrets in")
        return self._store

    def env(self, name: str | None) -> str | None:
        """The env var *name*; never hosted."""
        if self.hosted or not name:
            return None
        return os.environ.get(name, "").strip() or None

    def stored(self, name: str) -> str | None:
        """:raises SecretKeyError: Stored under another key."""
        found = self._store.get(name) if self._store else None
        return (found or "").strip() or None

    def get(self, name: str | None) -> str | None:
        """The value of env var *name*, else of the stored secret *name*; ``None`` when unnamed or
        unset."""
        return self.env(name) or (self.stored(name) if name else None)


@dataclass(frozen=True)
class ModelConfig:
    """evalution's own model connection, for the Grader, filler, analysis and the agent.

    :param models: ``strong`` and ``light`` model ids.
    :param prices: ``model id -> {"input": usd, "output": usd}`` per million tokens.
    """

    kind: str
    base_url: str
    api_key_env: str | None
    models: dict[str, str]
    prices: dict[str, dict[str, float]] = field(default_factory=dict)
    #: A key typed in a form not saved yet; used before any other.
    key: str | None = field(default=None, repr=False)
    #: The form moved to another host: a key stored by a form stays behind. One in the environment
    #: is the owner's own and may go wherever they point it, as it does once this is saved.
    no_stored_key: bool = False
    keys: KeyRing = field(default_factory=KeyRing, repr=False, compare=False)

    def rate(self, model: str) -> dict[str, float] | None:
        """*model*'s price per million tokens: the config's table, else the built-in one."""
        return self.prices.get(model) or BUILTIN_PRICES.get(model)

    def api_key(self) -> str | None:
        """The key: a typed one, then env var ``api_key_env``, then the stored ``model.api_key``,
        unless it stays behind (``no_stored_key``). Hosted, a typed one, then the account's."""
        if self.key:
            return self.key
        if self.keys.hosted:
            return self.keys.account_key()
        if self.no_stored_key:
            return self.keys.env(self.api_key_env)
        return self.keys.get(self.api_key_env) or self.keys.stored(MODEL_SECRET)

    def price(self, model: str, tokens_in: int, tokens_out: int) -> float:
        """What *model* costs for these tokens; 0 when it is in neither table."""
        rate = self.rate(model)
        if not rate:
            return 0.0
        usd = tokens_in * rate.get("input", 0.0) + tokens_out * rate.get("output", 0.0)
        return round(usd / 1_000_000, 6)


@dataclass(frozen=True)
class PhoenixConfig:
    """A target's Phoenix project.

    :param public_url: Where the browser reaches Phoenix, for trace links (default ``url``).
    :param mirror: Also write finished runs to it as datasets and experiments.
    """

    url: str
    project: str = "default"
    api_key_env: str | None = None
    public_url: str | None = None
    mirror: bool = False
    keys: KeyRing = field(default_factory=KeyRing, repr=False, compare=False)


@dataclass(frozen=True)
class CodeConfig:
    """A target's source, read-only for the agent: every source file under ``path``, except
    those matching one of ``ignore`` (and never a secrets file)."""

    path: Path
    ignore: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetConfig:
    """One target. ``options`` is the target's whole block, for the adapter's own keys."""

    name: str
    adapter: str
    phoenix: PhoenixConfig | None
    code: CodeConfig | None
    options: dict[str, Any]
    keys: KeyRing = field(default_factory=KeyRing, repr=False, compare=False)

    @property
    def trace_attribute(self) -> str:
        """The span attribute a session is found by (the http adapter's setting, else
        ``session.id``)."""
        http = self.options.get("http") if isinstance(self.options.get("http"), dict) else {}
        found = self.options.get("trace_attribute") or http.get("trace_attribute")
        return str(found or "session.id")

    @property
    def max_parallel(self) -> int:
        """Cases a run may play at once (``max_parallel``, default 1), when the adapter can."""
        return int(self.options.get("max_parallel") or 1)


@dataclass(frozen=True)
class TracesConfig:
    """How evalution keeps and reads traces. Apps send spans to ``POST /v1/traces``, kept in the
    database ``keep_days`` days. ``settle_seconds``: how long the spans of a try must hold still
    before they are taken as all there (above OpenTelemetry's 5 s batch)."""

    keep_days: int = 30
    settle_seconds: float = 6.0
    #: How long after an agent's answer an adapter may go on waiting, before it's taken to have
    #: missed it. ``0``: never.
    stuck_seconds: float = 120.0


@dataclass(frozen=True)
class Config:
    file: Path
    root: Path
    data_dir: Path
    sets_dir: Path
    web_dir: Path
    model: ModelConfig | None
    targets: dict[str, TargetConfig]
    #: Where the model comes from: ``config`` (this file), ``page`` (the database) or none.
    model_source: str | None = None
    #: Cost of one generated (filler) message on a warm conversation, for estimates.
    filler_usd: float = 0.04
    #: A run stops once the app has spent this many times what its tries played were estimated at
    #: (at least its low estimate); 0: never.
    spend_cap: float = 3.0
    #: The targets connected from the page, which the page may edit.
    managed: frozenset[str] = frozenset()
    traces: TracesConfig = TracesConfig()
    #: The ready-made test library (``library/stateful/*.json``).
    library_dir: Path = Path("library")
    #: One real turn before the first run on an adapter and its settings (``runs/preflight.py``).
    check_adapters: bool = True
    #: Your own adapters, one module each. ``None``: ``<data_dir>/adapters``.
    adapters_dir: Path | None = None
    #: The names a server open to other computers answers under, beyond what its bind implies.
    allowed_hosts: tuple[str, ...] = ()
    #: Where every ``*_env`` secret is read, and the page's are kept.
    keys: KeyRing = field(default_factory=KeyRing, repr=False, compare=False)


def _dict(value: Any, where: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: must be a mapping")
    return value


def _str(block: dict[str, Any], key: str, where: str, default: str | None = None) -> str:
    value = block.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}.{key}: must be a non-empty string")
    return value.strip()


def _model(block: dict[str, Any], keys: KeyRing) -> ModelConfig | None:
    if not block:
        return None
    kind = block.get("kind", "anthropic")
    if kind not in MODEL_KINDS:
        raise ConfigError(f"model.kind: must be one of {', '.join(MODEL_KINDS)}")
    default_url = "https://api.anthropic.com" if kind == "anthropic" else None
    models = _dict(block.get("models"), "model.models")
    for key in MODEL_KEYS:
        if not isinstance(models.get(key), str):
            raise ConfigError(f"model.models.{key}: must be a model id")
    prices = {
        str(name): {k: float(v) for k, v in _dict(rate, f"model.prices.{name}").items()}
        for name, rate in _dict(block.get("prices"), "model.prices").items()
    }
    return ModelConfig(
        kind=kind,
        base_url=_str(block, "base_url", "model", default_url).rstrip("/"),
        api_key_env=block.get("api_key_env"),
        models={k: str(v) for k, v in models.items()},
        prices=prices,
        keys=keys,
    )


_said_retired = False


def _note_retired(block: dict[str, Any]) -> None:
    """Say once per process that a config from before still works, and what changed: evalution
    no longer runs Phoenix, whatever ``traces.managed`` says."""
    global _said_retired
    found = [f"traces.{k}" for k in RETIRED_TRACES if k in block]
    if found and not _said_retired:
        _said_retired = True
        _logger.warning(
            "evalution: %s no longer apply: evalution doesn't run Phoenix any more and keeps "
            "traces in its own database; remove them from the config",
            ", ".join(found),
        )


def _traces(block: dict[str, Any]) -> TracesConfig:
    _note_retired(block)
    keep_days = block.get("keep_days", 30)
    if isinstance(keep_days, bool) or not isinstance(keep_days, int) or keep_days < 1:
        raise ConfigError("traces.keep_days: a whole number of at least 1")
    settle = block.get("settle_seconds", 6.0)
    if isinstance(settle, bool) or not isinstance(settle, (int, float)) or settle <= 0:
        raise ConfigError("traces.settle_seconds: a number above 0")
    stuck = block.get("stuck_seconds", 120.0)
    if isinstance(stuck, bool) or not isinstance(stuck, (int, float)) or stuck < 0:
        raise ConfigError("traces.stuck_seconds: a number of 0 or more")
    return TracesConfig(keep_days, float(settle), float(stuck))


def _check_adapters(raw: dict[str, Any]) -> bool:
    found = raw.get("check_adapters", True)
    if not isinstance(found, bool):
        raise ConfigError("check_adapters: true or false")
    return found


def target_config(
    name: str, block: dict[str, Any], root: Path, keys: KeyRing | None = None
) -> TargetConfig:
    """*keys*: where its secrets are read (default: the environment only).

    :raises ConfigError: A field that does not follow the shape."""
    keys = keys or KeyRing()
    where = f"targets.{name}"
    adapter = _str(block, "adapter", where)
    phoenix = None
    if block.get("phoenix") is not None:
        p = _dict(block["phoenix"], f"{where}.phoenix")
        phoenix = PhoenixConfig(
            url=_str(p, "url", f"{where}.phoenix").rstrip("/"),
            project=str(p.get("project") or "default"),
            api_key_env=p.get("api_key_env"),
            public_url=(str(p["public_url"]).rstrip("/") if p.get("public_url") else None),
            mirror=bool(p.get("mirror", False)),
            keys=keys,
        )
    code = None
    if block.get("code") is not None:
        c = _dict(block["code"], f"{where}.code")
        # An ``include`` from before everything was watched is left where it is, and not read.
        ignore = c.get("ignore") or []
        if not isinstance(ignore, list) or not all(isinstance(g, str) for g in ignore):
            raise ConfigError(f"{where}.code.ignore: must be a list of globs")
        code = CodeConfig(
            path=(root / _str(c, "path", f"{where}.code")).resolve(), ignore=tuple(ignore)
        )
    parallel = block.get("max_parallel", 1)
    if isinstance(parallel, bool) or not isinstance(parallel, int) or not 1 <= parallel <= 16:
        raise ConfigError(f"{where}.max_parallel: must be a whole number from 1 to 16")
    return TargetConfig(
        name=name, adapter=adapter, phoenix=phoenix, code=code, options=block, keys=keys
    )


# What the page sets


def page_targets(docs: Docs) -> dict[str, Any]:
    """The blocks of the targets connected from the page, by name."""
    return {key.removeprefix(_TARGETS): body for key, body, _ in docs.items(SETTINGS, _TARGETS)}


def save_page_target(docs: Docs, name: str, block: dict[str, Any] | None) -> None:
    """Write *name*'s block; ``None`` removes it."""
    if block is None:
        docs.delete(SETTINGS, f"{_TARGETS}{name}")
    else:
        docs.put(SETTINGS, f"{_TARGETS}{name}", block)


def page_model(docs: Docs) -> dict[str, Any]:
    """The ``model:`` block set from the page; empty when there is none."""
    found = docs.get(SETTINGS, MODEL_DOC)
    return found if isinstance(found, dict) else {}


def save_page_model(docs: Docs, block: dict[str, Any]) -> None:
    docs.put(SETTINGS, MODEL_DOC, block)


def load(path: Path | None = None, docs: Docs | None = None, keys: KeyRing | None = None) -> Config:
    """The config at *path*, ``$EVALUTION_CONFIG`` or ``./evalution.yaml``, with what the page set
    from *docs*, by default the workspace of this server in its data folder's database. A missing
    file gives an empty config (no model, no targets) rooted at the working folder. *keys*: where
    its secrets are read, by default *docs*' own stored secrets and the environment; hosted
    (:attr:`KeyRing.hosted`), only the targets stored in *docs* are read, none from the file.

    :raises ConfigError: A field that does not follow the shape, or a secret key that isn't one.
    """
    path = path or Path(os.environ.get("EVALUTION_CONFIG") or "evalution.yaml")
    data: dict[str, Any] = {}
    if path.is_file():
        data = _dict(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
    root = path.resolve().parent

    def where(key: str, default: str) -> Path:
        return (root / str(data.get(key) or default)).resolve()

    data_dir = where("data_dir", "data")
    docs = docs or Database.open(data_dir).docs()
    if keys is None:
        try:
            keys = KeyRing(Secrets(docs, data_dir))
        except SecretKeyError as exc:
            raise ConfigError(str(exc)) from exc
    managed = page_targets(docs)
    model_block, model_source = _dict(data.get("model"), "model"), "config"
    if not model_block:
        model_block, model_source = page_model(docs), "page"

    # Hosted, the file's targets are the operator's: no workspace sees them.
    own = {} if keys.hosted else _dict(data.get("targets"), "targets")
    targets = {**managed, **own}
    for name in targets:
        if not isinstance(name, str) or not name.replace("-", "").replace("_", "").isalnum():
            raise ConfigError(f"targets.{name}: not a target name")
    allowed = os.environ.get(ALLOWED_HOSTS_ENV)
    hosts = allowed.split(",") if allowed is not None else data.get("allowed_hosts") or []
    if not isinstance(hosts, list) or not all(isinstance(h, str) for h in hosts):
        raise ConfigError("allowed_hosts: must be a list of names")
    filler = data.get("filler_usd", 0.04)
    if isinstance(filler, bool) or not isinstance(filler, (int, float)) or filler < 0:
        raise ConfigError("filler_usd: must be a number of at least 0")
    cap = data.get("spend_cap", 3.0)
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or cap < 0:
        raise ConfigError("spend_cap: must be a number of at least 0")
    return Config(
        file=path.resolve(),
        root=root,
        data_dir=data_dir,
        sets_dir=where("sets_dir", "sets"),
        web_dir=where("web_dir", "web/dist"),
        model=_model(model_block, keys),
        model_source=model_source if model_block else None,
        targets={
            n: target_config(n, _dict(b, f"targets.{n}"), root, keys) for n, b in targets.items()
        },
        filler_usd=float(filler),
        spend_cap=float(cap),
        managed=frozenset(n for n in managed if n not in own),
        traces=_traces(_dict(data.get("traces"), "traces")),
        library_dir=where("library_dir", "library"),
        check_adapters=_check_adapters(data),
        adapters_dir=where("adapters_dir", "data/adapters") if "adapters_dir" in data else None,
        allowed_hosts=tuple(h.strip() for h in hosts if h.strip()),
        keys=keys,
    )
