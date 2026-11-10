"""What leaves evalution's copy of the app: settings that reach a service outside it.

The copy starts with the project's own env file, which may hold live keys: a test on the copy
could then charge a card or send mail. :func:`found` lists the setting **names** that look like
a third-party credential or address: a short list of kinds (payment, mail, SMS, webhooks, cloud
storage), and any name ending ``_KEY``, ``_SECRET`` or ``_TOKEN`` that isn't the model provider's,
evalution's own, or the app's own (a session or signing secret, a database password). The copy
starts with each of those set empty (:func:`blocked`), unless the person allowed it
(:class:`OutboundStore`).

Names only: a value is never kept, returned or logged. An env file is read line by line and each
line dropped once its name is taken. Not a network proxy: what the app reaches by an address in
its code, or by a setting not listed, still leaves.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

#: A setting's name, as env files and compose files write it.
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TARGET = re.compile(r"^[A-Za-z0-9_-]+$")

#: Kinds of outside service, by a word in the name.
KINDS: dict[str, frozenset[str]] = {
    "payment": frozenset({"PAYMENT", "PAYMENTS", "BILLING", "CHECKOUT", "INVOICE", "MERCHANT",
                          "PAYOUT", "CARD"}),
    "mail": frozenset({"MAIL", "MAILER", "SMTP", "EMAIL", "IMAP"}),
    "sms": frozenset({"SMS", "TELEPHONY", "PHONE", "VOICE"}),
    "webhook": frozenset({"WEBHOOK", "WEBHOOKS", "HOOK"}),
    "storage": frozenset({"S3", "BUCKET", "BLOB", "GCS", "AWS", "GCP", "AZURE", "STORAGE"}),
}  # fmt: skip
#: The last word of a name in one of :data:`KINDS` that makes it reach the service: a credential
#: or an address. ``SMTP_HOST`` is listed; ``EMAIL_FROM`` is not.
REACHES = frozenset({"KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "PASS", "HOST", "URL",
                     "URI", "DSN", "SID", "ID", "ENDPOINT", "CREDENTIALS", "USER",
                     "USERNAME"})  # fmt: skip
#: Endings that make any name a credential.
CREDENTIAL = ("_KEY", "_SECRET", "_TOKEN")
#: Words of a name that is the model provider's: the app needs it to answer at all.
MODEL = frozenset({"OPENAI", "ANTHROPIC", "CLAUDE", "GEMINI", "MISTRAL", "COHERE", "GROQ",
                   "TOGETHER", "OPENROUTER", "DEEPSEEK", "OLLAMA", "BEDROCK", "VERTEX", "LLM",
                   "MODEL", "MODELS", "EMBEDDING", "EMBEDDINGS", "HF", "HUGGINGFACE"})  # fmt: skip
#: Words of a name that is evalution's own (its traces), or the app's own: it reaches nothing
#: outside the copy, and the app may not start without it.
OWN = frozenset({"EVALUTION", "OTEL", "SESSION", "JWT", "SIGNING", "ENCRYPTION", "ENCRYPT",
                 "CSRF", "COOKIE", "NEXTAUTH", "CACHE", "POSTGRES", "PG", "DATABASE", "DB",
                 "REDIS", "MYSQL", "MONGO", "MONGODB", "INTERNAL", "ADMIN", "SALT"})  # fmt: skip
#: Whole names that are the app's own.
OWN_NAMES = frozenset({"SECRET_KEY", "APP_KEY", "APP_SECRET"})


@dataclass(frozen=True)
class Outbound:
    """A setting that reaches a service outside the copy: its name and what kind it is."""

    name: str
    kind: str


def kind_of(name: str) -> str | None:
    """What kind of outside service *name* reaches (``credential`` when only its ending says so);
    ``None`` when it looks like none, or is the model provider's, evalution's or the app's own."""
    upper = name.upper()
    words = [w for w in upper.split("_") if w]
    if not words or upper in OWN_NAMES or any(w in MODEL or w in OWN for w in words):
        return None
    if words[-1] in REACHES:
        for kind, marks in KINDS.items():
            if any(w in marks for w in words):
                return kind
    if upper.endswith(CREDENTIAL):
        return "credential"
    return None


def env_names(path: Path) -> list[str]:
    """The names an env file sets, in its order; never a value (each line is dropped once its
    name is taken). None when it can't be read."""
    names: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        name = line.strip().removeprefix("export ").partition("=")[0].strip()
        if "=" in line and _NAME.match(name) and name not in names:
            names.append(name)
    return names


def _compose_names(files: list[Path]) -> list[str]:
    """The names compose files' services set, from ``environment`` and the ``env_file`` each
    names. Names only."""
    names: list[str] = []
    for file in files:
        try:
            data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        services = data.get("services") if isinstance(data, dict) else None
        for service in (services or {}).values():
            if not isinstance(service, dict):
                continue
            env = service.get("environment") or {}
            keys = env.keys() if isinstance(env, dict) else [str(e).split("=", 1)[0] for e in env]
            names += [str(k) for k in keys]
            listed = service.get("env_file") or []
            for item in [listed] if isinstance(listed, (str, dict)) else listed:
                path = item.get("path") if isinstance(item, dict) else item
                if isinstance(path, str):
                    names += env_names(file.parent / path)
    return names


def found(spec: dict[str, Any]) -> list[Outbound]:
    """The settings a copy started as *spec* says (:func:`~evalution.isolation.isolate.launch_spec`)
    would have that reach a service outside it, by name, each once, in name order."""
    names: list[str] = []
    if spec.get("kind") == "compose":
        names += _compose_names([Path(f) for f in spec.get("files") or []])
        if spec.get("env_file"):
            names += env_names(Path(spec["env_file"]))
    names += [str(k) for k in (spec.get("env") or {})]
    out: dict[str, Outbound] = {}
    for name in names:
        kind = kind_of(name)
        if kind is not None and _NAME.match(name):
            out.setdefault(name, Outbound(name, kind))
    return [out[n] for n in sorted(out)]


class OutboundStore:
    """The names a person allowed to reach outside the copy, per target:
    ``<data_dir>/isolation/<target>/outbound.json``. Names only."""

    def __init__(self, data_dir: Path) -> None:
        self.root = data_dir / "isolation"

    def _path(self, target: str) -> Path:
        if not _TARGET.match(target):
            raise ValueError(f"not a target name: {target!r}")
        return self.root / target / "outbound.json"

    def allowed(self, target: str) -> set[str]:
        path = self._path(target)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return set()
        names = data.get("allowed") if isinstance(data, dict) else None
        return {n for n in names or [] if isinstance(n, str) and _NAME.match(n)}

    def allow(self, target: str, name: str, allowed: bool) -> set[str]:
        """Allow *name* to reach outside the copy, or block it again; the names now allowed.

        :raises ValueError: *name* isn't a setting's name.
        """
        if not _NAME.match(name):
            raise ValueError(f"not a setting's name: {name!r}")
        names = self.allowed(target)
        if allowed:
            names.add(name)
        else:
            names.discard(name)
        path = self._path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"allowed": sorted(names)}, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return names


def listed(spec: dict[str, Any], allowed: set[str]) -> list[dict[str, Any]]:
    """``[{name, kind, allowed}]``: what the isolation API shows."""
    return [{"name": o.name, "kind": o.kind, "allowed": o.name in allowed} for o in found(spec)]


def blocked(spec: dict[str, Any], allowed: set[str]) -> dict[str, str]:
    """``name -> ""`` for each setting :func:`found` lists that the person didn't allow: what the
    copy starts with on top of its own settings."""
    return {o.name: "" for o in found(spec) if o.name not in allowed}


def blocked_for(data_dir: Path, target: str, spec: dict[str, Any]) -> dict[str, str]:
    """:func:`blocked` for *target*, with what its person allowed."""
    return blocked(spec, OutboundStore(data_dir).allowed(target))
