"""``evalution.yaml`` (docs/architecture.md, "Config"): the model connection, the targets and
where data lives. No secrets in the file: every secret is named by a ``*_env`` key, read only
when used, from the env var of that name, else from ``<data_dir>/secrets.json`` (the secrets
of targets connected from the page, written only by :func:`write_secrets`).

The file is ``$EVALUTION_CONFIG``, else ``./evalution.yaml``. Relative paths are resolved
against the file's folder (the repo root): ``sets/`` (the tests as files, which ``evalution sets
import`` and ``export`` use), ``targets/`` (profiles), ``library/`` and ``web/dist`` live there
unless the file says otherwise. ``traces:`` says where evalution keeps
traces itself, if it does (:class:`TracesConfig`). Targets connected from the page live in
``targets.yaml`` next to it (``targets:`` only), merged in; a name in both is the hand-edited
one. ``model.yaml`` next to it holds the model set from the page (``model:`` only); a ``model:``
in the config itself wins over it. ``$EVALUTION_SETTINGS_DIR`` moves those two files elsewhere,
for a config folder that can't be written (a container's).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

MODEL_KINDS = ("anthropic", "openai")
MODEL_KEYS = ("strong", "light")
TARGETS_FILE = "targets.yaml"
MODEL_FILE = "model.yaml"
#: ``traces.managed``: evalution keeps traces itself, with no Phoenix.
BUILTIN_TRACES = "builtin"
#: The stored secret that holds the model's API key, when it was set from the page.
MODEL_SECRET = "model.api_key"  # noqa: S105 (a name, not a value)
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
SECRETS_FILE = "secrets.json"
#: The names a server open to other computers answers under: wins over ``allowed_hosts``.
ALLOWED_HOSTS_ENV = "EVALUTION_ALLOWED_HOSTS"
#: Where :func:`secret` looks after the environment; set by :func:`load`.
_secrets_path: Path | None = None


class ConfigError(ValueError):
    """The config file does not follow the shape; the message names the field."""


def read_secrets() -> dict[str, str]:
    """The stored secrets, ``key -> value``; empty when there are none."""
    if _secrets_path is None:
        return {}
    try:
        data = json.loads(_secrets_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def write_secrets(changes: dict[str, str | None]) -> None:
    """Set (a value) or remove (``None``) stored secrets. The file is only ever owner-readable."""
    if _secrets_path is None:
        raise ConfigError("no data folder to keep secrets in")
    found = read_secrets()
    for key, value in changes.items():
        if value is None:
            found.pop(key, None)
        else:
            found[key] = value
    _secrets_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _secrets_path.with_name(f".{SECRETS_FILE}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(found, handle, indent=2)
    os.chmod(tmp, 0o600)
    tmp.replace(_secrets_path)


def secret(env: str | None) -> str | None:
    """The value of env var *env*, else of the stored secret *env*; ``None`` when unnamed or
    unset."""
    if not env:
        return None
    return os.environ.get(env, "").strip() or read_secrets().get(env, "").strip() or None


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

    def rate(self, model: str) -> dict[str, float] | None:
        """*model*'s price per million tokens: the config's table, else the built-in one."""
        return self.prices.get(model) or BUILTIN_PRICES.get(model)

    def api_key(self) -> str | None:
        """The key: a typed one, then env var ``api_key_env``, then the stored ``model.api_key``,
        unless it stays behind (``no_stored_key``)."""
        if self.key:
            return self.key
        if self.no_stored_key:
            return os.environ.get(self.api_key_env or "", "").strip() or None
        return secret(self.api_key_env) or read_secrets().get(MODEL_SECRET, "").strip() or None

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
    """Where traces land. ``managed``: ``evalution serve`` runs its own Phoenix (under
    ``<data_dir>/phoenix``) on ``port``, or the next free one, and each target's spans go to
    project ``evalution-<target>``. ``grpc``: also take OTLP over gRPC on this port (off by
    default). Not managed: each target's own Phoenix, as configured on the target. ``builtin``:
    no Phoenix, ``POST /v1/traces`` takes OTLP into ``<data_dir>/traces/`` and keeps it
    ``keep_days`` days and ``keep_mb`` megabytes at most. ``settle_seconds``: how long the spans of
    a try must hold still before they are taken as all there (above OpenTelemetry's 5 s batch)."""

    managed: bool | str = True
    port: int = 6007
    grpc: int | None = None
    keep_days: int = 14
    keep_mb: int = 500
    settle_seconds: float = 6.0
    #: How long after an agent's answer an adapter may go on waiting, before it's taken to have
    #: missed it. ``0``: never.
    stuck_seconds: float = 120.0

    @property
    def builtin(self) -> bool:
        return self.managed == BUILTIN_TRACES

    @property
    def phoenix(self) -> bool:
        """Whether evalution runs its own Phoenix."""
        return self.managed is True


@dataclass(frozen=True)
class Config:
    file: Path
    root: Path
    data_dir: Path
    sets_dir: Path
    targets_dir: Path
    web_dir: Path
    model: ModelConfig | None
    targets: dict[str, TargetConfig]
    #: Where the model comes from: ``config`` (this file), ``page`` (``model.yaml``) or none.
    model_source: str | None = None
    #: Cost of one generated (filler) message on a warm conversation, for estimates.
    filler_usd: float = 0.04
    #: The targets from ``targets.yaml`` (connected from the page), which the page may edit.
    managed: frozenset[str] = frozenset()
    traces: TracesConfig = TracesConfig()
    #: The ready-made test library (``library/stateful/*.json``).
    library_dir: Path = Path("library")
    #: One real turn before the first run on an adapter and its settings (``runs/preflight.py``).
    check_adapters: bool = True
    #: Your own adapters, one module each. ``None``: ``<data_dir>/adapters``.
    adapters_dir: Path | None = None
    #: Where ``targets.yaml`` and ``model.yaml`` live. ``None``: next to the config.
    settings_dir: Path | None = None
    #: The names a server open to other computers answers under, beyond what its bind implies.
    allowed_hosts: tuple[str, ...] = ()

    @property
    def targets_file(self) -> Path:
        return (self.settings_dir or self.root) / TARGETS_FILE

    @property
    def spend_file(self) -> Path:
        """What evalution's own model calls have cost so far."""
        return self.data_dir / "spend.json"

    @property
    def model_file(self) -> Path:
        return (self.settings_dir or self.root) / MODEL_FILE


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


def _model(block: dict[str, Any]) -> ModelConfig | None:
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
    )


def _traces(block: dict[str, Any]) -> TracesConfig:
    managed = block.get("managed", True)
    if not isinstance(managed, bool) and managed != BUILTIN_TRACES:
        raise ConfigError(f"traces.managed: true, false or {BUILTIN_TRACES}")
    port, grpc = block.get("port", 6007), block.get("grpc")
    for key, value in (("port", port), ("grpc", grpc)):
        if value is None and key == "grpc":
            continue
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < 65536:
            raise ConfigError(f"traces.{key}: a port number")
    keep_days, keep_mb = block.get("keep_days", 14), block.get("keep_mb", 500)
    for key, value in (("keep_days", keep_days), ("keep_mb", keep_mb)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ConfigError(f"traces.{key}: a whole number of at least 1")
    settle = block.get("settle_seconds", 6.0)
    if isinstance(settle, bool) or not isinstance(settle, (int, float)) or settle <= 0:
        raise ConfigError("traces.settle_seconds: a number above 0")
    stuck = block.get("stuck_seconds", 120.0)
    if isinstance(stuck, bool) or not isinstance(stuck, (int, float)) or stuck < 0:
        raise ConfigError("traces.stuck_seconds: a number of 0 or more")
    return TracesConfig(managed, port, grpc, keep_days, keep_mb, float(settle), float(stuck))


def _check_adapters(raw: dict[str, Any]) -> bool:
    found = raw.get("check_adapters", True)
    if not isinstance(found, bool):
        raise ConfigError("check_adapters: true or false")
    return found


def target_config(name: str, block: dict[str, Any], root: Path) -> TargetConfig:
    """:raises ConfigError: A field that does not follow the shape."""
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
    return TargetConfig(name=name, adapter=adapter, phoenix=phoenix, code=code, options=block)


def load(path: Path | None = None) -> Config:
    """The config at *path*, ``$EVALUTION_CONFIG`` or ``./evalution.yaml``. A missing file
    gives an empty config (no model, no targets) rooted at the working folder.

    :raises ConfigError: A field that does not follow the shape.
    """
    path = path or Path(os.environ.get("EVALUTION_CONFIG") or "evalution.yaml")
    data: dict[str, Any] = {}
    if path.is_file():
        data = _dict(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))
    root = path.resolve().parent
    moved = os.environ.get("EVALUTION_SETTINGS_DIR")
    settings_dir = Path(moved).resolve() if moved else None
    if settings_dir is not None:
        settings_dir.mkdir(parents=True, exist_ok=True)
    settings = settings_dir or root
    managed: dict[str, Any] = {}
    if (settings / TARGETS_FILE).is_file():
        text = (settings / TARGETS_FILE).read_text(encoding="utf-8")
        managed = _dict(_dict(yaml.safe_load(text), TARGETS_FILE).get("targets"), TARGETS_FILE)
    model_block, model_source = _dict(data.get("model"), "model"), "config"
    if not model_block and (settings / MODEL_FILE).is_file():
        text = (settings / MODEL_FILE).read_text(encoding="utf-8")
        model_block = _dict(_dict(yaml.safe_load(text), MODEL_FILE).get("model"), MODEL_FILE)
        model_source = "page"

    def where(key: str, default: str) -> Path:
        return (root / str(data.get(key) or default)).resolve()

    own = _dict(data.get("targets"), "targets")
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
    global _secrets_path
    _secrets_path = where("data_dir", "data") / SECRETS_FILE
    return Config(
        file=path.resolve(),
        root=root,
        data_dir=where("data_dir", "data"),
        sets_dir=where("sets_dir", "sets"),
        targets_dir=where("targets_dir", "targets"),
        web_dir=where("web_dir", "web/dist"),
        model=_model(model_block),
        model_source=model_source if model_block else None,
        targets={n: target_config(n, _dict(b, f"targets.{n}"), root) for n, b in targets.items()},
        filler_usd=float(filler),
        managed=frozenset(n for n in managed if n not in own),
        traces=_traces(_dict(data.get("traces"), "traces")),
        library_dir=where("library_dir", "library"),
        check_adapters=_check_adapters(data),
        adapters_dir=where("adapters_dir", "data/adapters") if "adapters_dir" in data else None,
        settings_dir=settings_dir,
        allowed_hosts=tuple(h.strip() for h in hosts if h.strip()),
    )
