"""The configured targets and what each one comes with: its adapter (built on first use), where
its traces are read (:func:`evalution.traces.builtin.reader`) and its code folder.
:attr:`Targets.docs` is the workspace's storage (:mod:`evalution.storage`), which everything above
reads and writes through; what is kept on each target is :mod:`evalution.project.profile`'s.
"""

from __future__ import annotations

import threading
from dataclasses import replace

from evalution.adapters import add_folder, build, forget
from evalution.adapters.base import Target
from evalution.code import Code, Digests
from evalution.config import Config, TargetConfig, load
from evalution.core.case import SLUG
from evalution.results.store import RunSink
from evalution.storage import Database, Docs
from evalution.traces.base import TraceSource
from evalution.traces.builtin import SpanStore, reader
from evalution.traces.mirror import mirror_of


class Guards:
    """What keeps this process's work on each target's documents in step: each guards a read and
    the write that follows it. Only this process's: ``evalution run`` and ``serve`` are separate
    ones."""

    def __init__(self) -> None:
        #: One knowledge refresh at a time per app; what comes meanwhile is covered by the next
        #: check.
        self.refreshing: set[str] = set()
        #: Reading and writing the knowledge document is one step.
        self.knowledge = threading.RLock()
        #: A background check and a run's start both write the tested state.
        self.tested = threading.Lock()


def _without_folders(config: Config) -> Config:
    """*config* with no target reading a folder of this computer: its code is left out."""
    return replace(config, targets={n: replace(t, code=None) for n, t in config.targets.items()})


class Targets:
    def __init__(
        self,
        config: Config,
        docs: Docs | None = None,
        folders: bool = True,
    ) -> None:
        #: Whether a target may read a folder of this computer (its code); hosted, none may.
        self.folders = folders
        self.config = config if folders else _without_folders(config)
        #: This workspace's storage; by default this server's own, in its data folder.
        self.docs = docs or Database.open(config.data_dir).docs()
        add_folder(config.adapters_dir or config.data_dir / "adapters")
        #: The spans apps sent this workspace (docs/architecture.md, "Traces").
        self.store = SpanStore(self.docs.database, self.docs.workspace)
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
        return build(self.conf(name), self.docs.workspace)

    def source(self, name: str) -> TraceSource:
        """Where *name*'s traces are read: what evalution keeps itself (its spans, the default
        ones) plus the app's own Phoenix when it has one."""
        if name not in self._sources:
            self._sources[name] = reader(name, self.conf(name).phoenix, self.store)
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
        fresh = load(self.config.file, self.docs, self.config.keys)
        if not self.folders:
            fresh = _without_folders(fresh)
        self.config = replace(self.config, targets=fresh.targets, managed=fresh.managed)
        forget(name, self.docs.workspace)
        if source := self._sources.pop(name, None):
            await source.close()
        if sink := self._sinks.pop(name, None):
            await sink.close()

    async def close(self) -> None:
        for source in self._sources.values():
            await source.close()
        for sink in self._sinks.values():
            await sink.close()
