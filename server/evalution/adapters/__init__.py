"""Adapters by name: ``evalution.adapters.<adapter>``, each with a ``create(target)`` and an
``INFO`` (see :mod:`.base`). The registry is this folder and ``adapters_dir`` in the config, by
default ``<data_dir>/adapters``: a new module in either is a new adapter, and one for your own app
needn't touch this repo. That folder's code runs, so it comes only from the config, never from a
project being connected.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import re
from functools import cache
from itertools import chain
from pathlib import Path

from evalution.adapters.base import AdapterInfo, OwnTraffic, Target
from evalution.config import ConfigError, TargetConfig
from evalution.storage import LOCAL

_logger = logging.getLogger(__name__)

_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_built: dict[str, Target] = {}


@cache
def infos() -> dict[str, AdapterInfo]:
    """Every adapter here, by name. One that can't be imported (a package it needs isn't installed,
    or a bug in one of your own) is left out, and breaks only its own targets."""
    found: dict[str, AdapterInfo] = {}
    for module in pkgutil.iter_modules(__path__):
        try:
            # Deferred on purpose: adapters are optional modules, found in this folder.
            loaded = importlib.import_module(f"{__name__}.{module.name}")
        except Exception:  # someone's own module can fail in any way at all
            _logger.warning("adapters: %s not loaded", module.name, exc_info=True)
            continue
        if isinstance(info := getattr(loaded, "INFO", None), AdapterInfo):
            found[module.name] = info
    return dict(sorted(found.items()))


def add_folder(folder: Path) -> None:
    """Find adapters in *folder* too."""
    where = str(folder)
    if folder.is_dir() and where not in __path__:
        __path__.append(where)
        infos.cache_clear()
        own_traffic.cache_clear()


def adapter_info(name: str) -> AdapterInfo | None:
    return infos().get(name)


@cache
def own_traffic() -> OwnTraffic:
    """What marks eval traffic in traces, from every adapter."""
    marks = [info.own_traffic for info in infos().values()]
    return OwnTraffic(
        tuple(chain.from_iterable(m.sessions for m in marks)),
        tuple(chain.from_iterable(m.attributes for m in marks)),
        tuple(chain.from_iterable(m.users for m in marks)),
    )


def _key(workspace: str, name: str) -> str:
    """Where a target's adapter is kept: by name locally, by workspace and name hosted, where two
    workspaces may each have a target of the same name."""
    return name if workspace == LOCAL else f"{workspace}/{name}"


def build(target: TargetConfig, workspace: str = LOCAL) -> Target:
    """The adapter of *target*, made once per target name in its *workspace*.

    :raises ConfigError: No such adapter.
    """
    key = _key(workspace, target.name)
    if key in _built:
        return _built[key]
    made = make(target)
    _built[key] = made
    return made


def make(target: TargetConfig) -> Target:
    """A new adapter of *target*, kept by nobody: for settings that aren't saved yet.

    :raises ConfigError: No such adapter.
    """
    if not _NAME.match(target.adapter):
        raise ConfigError(f"targets.{target.name}.adapter: not an adapter name")
    path = f"{__name__}.{target.adapter}"
    try:
        # Deferred on purpose, as in :func:`infos`.
        module = importlib.import_module(path)
    except ModuleNotFoundError as exc:
        if exc.name != path:
            raise
        raise ConfigError(f"targets.{target.name}: no adapter {target.adapter!r}") from exc
    if not hasattr(module, "create"):
        raise ConfigError(f"targets.{target.name}: no adapter {target.adapter!r}")
    return module.create(target)


def forget(name: str, workspace: str = LOCAL) -> None:
    """Drop target *name*'s adapter, so the next :func:`build` reads its config again."""
    _built.pop(_key(workspace, name), None)
