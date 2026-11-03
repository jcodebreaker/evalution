"""The configured targets and what each one comes with: its adapter (built on first use), where
its traces are read (:func:`evalution.traces.managed.reader`), its code folder, its sets folder
``<sets>/<target>/`` and its profile folder ``<targets>/<target>/``, whose contents
:mod:`evalution.project.profile` reads and writes.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path

from evalution.adapters import add_folder, build, forget
from evalution.adapters.base import Target
from evalution.code import Code, Digests
from evalution.config import Config, TargetConfig, load
from evalution.core.case import SLUG
from evalution.results.store import RunSink
from evalution.traces.base import TraceSource
from evalution.traces.builtin import SpanStore
from evalution.traces.managed import reader
from evalution.traces.mirror import mirror_of
from evalution.traces.view import Pricer


class Guards:
    """What keeps this process's work on the profile folders in step. Only this process's:
    ``evalution run`` and ``serve`` are separate ones."""

    def __init__(self) -> None:
        #: One knowledge refresh at a time per app; what comes meanwhile is covered by the next
        #: check.
        self.refreshing: set[str] = set()
        #: Reading and writing the knowledge file is one step.
        self.knowledge = threading.RLock()
        #: A background check and a run's start both write the tested state.
        self.tested = threading.Lock()


class Targets:
    def __init__(self, config: Config, managed_url: str | None = None) -> None:
        self.config = config
        add_folder(config.adapters_dir or config.data_dir / "adapters")
        #: Where apps send traces to evalution, when it keeps them (its own Phoenix, or this
        #: server for the built-in source; docs/architecture.md, "Traces").
        self.managed_url = managed_url
        traces = config.traces
        #: The built-in source's spans, when ``traces.managed`` is ``builtin``.
        self.store = (
            SpanStore(config.data_dir / "traces" / "spans.db", traces.keep_mb, traces.keep_days)
            if traces.builtin
            else None
        )
        self._sources: dict[str, TraceSource] = {}
        self._sinks: dict[str, RunSink] = {}
        self._digests: Digests = {}
        self.guards = Guards()

    def names(self) -> list[str]:
        return sorted(n for n in self.config.targets if SLUG.match(n))

    def conf(self, name: str) -> TargetConfig:
        """:raises LookupError: No such target."""
        found = self.config.targets.get(name)
        if found is None or not SLUG.match(name):
            raise LookupError(f"no target {name}")
        return found

    def adapter(self, name: str) -> Target:
        return build(self.conf(name))

    def source(self, name: str) -> TraceSource | None:
        """Where *name*'s traces are read: what evalution keeps itself (its project, the default
        project) plus the app's own Phoenix when it has one; or only the app's own."""
        if name not in self._sources:
            found = reader(name, self.conf(name).phoenix, self.managed_url, store=self.store)
            if found is None:
                return None
            self._sources[name] = found
        return self._sources[name]

    def sink(self, name: str) -> RunSink | None:
        """Where *name*'s finished runs also go, if anywhere; it is kept until the target
        changes."""
        if name not in self._sinks:
            found = mirror_of(self.conf(name).phoenix)
            if found is None:
                return None
            self._sinks[name] = found
        return self._sinks[name]

    def code(self, name: str) -> Code | None:
        conf = self.conf(name).code
        return Code(conf, self._digests) if conf else None

    def trace_attribute(self, name: str) -> str:
        return self.conf(name).trace_attribute

    async def reload(self, name: str) -> None:
        """The config's targets read again after *name* changed (the rest stays as it was)."""
        fresh = load(self.config.file)
        self.config = replace(self.config, targets=fresh.targets, managed=fresh.managed)
        forget(name)
        if source := self._sources.pop(name, None):
            await source.close()
        if sink := self._sinks.pop(name, None):
            await sink.close()

    def sets(self, name: str) -> Path:
        self.conf(name)
        return self.config.sets_dir / name

    def folder(self, name: str) -> Path:
        self.conf(name)
        return self.config.targets_dir / name

    @property
    def price(self) -> Pricer | None:
        return self.config.model.price if self.config.model else None

    async def close(self) -> None:
        for source in self._sources.values():
            await source.close()
        for sink in self._sinks.values():
            await sink.close()
