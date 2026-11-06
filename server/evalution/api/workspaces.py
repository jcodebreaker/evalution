"""Every request runs in one workspace (docs/hosting.md): this builds what serves it.

A :class:`Workspace` is the service graph of one workspace: its targets, model client, runs, test
writer and the locks that keep its work in step. :class:`Workspaces` builds each on first use and
keeps it, so a workspace's running work and caches outlive the request that started them. Local,
there is one, :data:`LOCAL`. Hosted, each workspace reads only its own documents, keeps its files
in its own folder under the data folder (so one run at a time holds per workspace, and no cache
is shared), and nothing reads a folder of this computer: finding apps on disk, the git hooks and
their code-change watcher, and an app's code are off.

It lives at the API layer because only the server serves several workspaces; the CLI works on
the local one with :class:`evalution.targets.Targets` directly.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace

from evalution.accounts.context import LOCAL, acting_user
from evalution.accounts.keys import model_key_for
from evalution.api.plans import CasePlans
from evalution.config import Config, ConfigError, KeyRing, load
from evalution.connect import discover, probe
from evalution.llm import LLM
from evalution.project import impact, knowledge
from evalution.runs.runner import Runs
from evalution.storage import Database
from evalution.storage.secrets import SecretKeyError, Secrets
from evalution.targets import Targets
from evalution.writer.agent import Agent
from evalution.writer.sessions import SessionStore

#: A workspace id, safe as a folder name. User namespaces (``user:<id>``) are not workspaces.
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
#: Where a hosted workspace keeps its files, under the data folder.
FOLDERS = "workspaces"


@dataclass
class Workspace:
    id: str
    targets: Targets
    llm: LLM
    runs: Runs
    agent: Agent
    plans: CasePlans
    probes: probe.Probes
    #: Saving the model and saving a target both rewrite the config, one at a time.
    lock: asyncio.Lock
    #: Apps found on disk; local only.
    found: discover.Discoveries | None
    #: The code-change checks the hooks and Home ask for; local only.
    watcher: impact.Watcher | None

    @property
    def hosted(self) -> bool:
        return not self.targets.folders

    async def close(self) -> None:
        if self.watcher is not None:
            await self.watcher.close()
        await self.targets.close()
        await self.llm.close()


def _hosted_config(config: Config, database: Database, workspace: str) -> Config:
    """*config* as *workspace* sees it hosted: what its page set, its secrets, the model key of
    whoever its work is for, and its own folder.

    :raises ConfigError: The secrets can't be read with this server's key.
    """
    docs = database.docs(workspace)
    try:
        secrets = Secrets(docs, config.data_dir)
    except SecretKeyError as exc:
        raise ConfigError(str(exc)) from exc
    keys = KeyRing(secrets, lambda: model_key_for(database, workspace, acting_user.get()))
    own = load(config.file, docs, keys)
    return replace(
        own,
        data_dir=config.data_dir / FOLDERS / workspace,
        adapters_dir=config.adapters_dir or config.data_dir / "adapters",
    )


def open_workspace(
    config: Config, database: Database, workspace: str, hosted: bool, url: str
) -> Workspace:
    """The service graph of *workspace*. *config* is the server's, read for :data:`LOCAL`; *url*
    is where this server listens, where the built-in trace source takes spans.

    :raises ConfigError: *workspace*'s config doesn't read.
    """
    if hosted:
        config = _hosted_config(config, database, workspace)
    targets = Targets(config, docs=database.docs(workspace), folders=not hosted)
    if targets.store is not None:
        targets.managed_url = url
    llm = LLM(config.model, docs=targets.docs)
    runs = Runs(targets, llm)
    found = None
    watcher = None
    if not hosted:
        found = discover.Discoveries(
            config.root,
            lambda: list(targets.config.targets),
            llm,
            lambda: targets.managed_url,
            targets.store,
        )
        watcher = impact.Watcher(runs, knowledge.refresh)
    return Workspace(
        id=workspace,
        targets=targets,
        llm=llm,
        runs=runs,
        agent=Agent(runs, SessionStore(targets.docs), llm),
        plans=CasePlans(runs),
        probes=probe.Probes(busy=runs.probing),
        lock=asyncio.Lock(),
        found=found,
        watcher=watcher,
    )


class Workspaces:
    """The workspaces this server has opened, each built on first use and kept until it stops."""

    def __init__(self, config: Config, database: Database, hosted: bool, url: str) -> None:
        self.config = config
        self.database = database
        self.hosted = hosted
        self.url = url
        self._open: dict[str, Workspace] = {}

    def get(self, workspace: str) -> Workspace:
        """:raises LookupError: Not a workspace id; locally, any but :data:`LOCAL`; hosted,
        :data:`LOCAL`, which only a request no sign-in placed would name.
        :raises ConfigError: Its config doesn't read."""
        if workspace not in self._open:
            if not _ID.match(workspace) or (workspace == LOCAL) == self.hosted:
                raise LookupError(f"no workspace {workspace!r}")
            self._open[workspace] = open_workspace(
                self.config, self.database, workspace, self.hosted, self.url
            )
        return self._open[workspace]

    def ids(self) -> list[str]:
        """Every workspace with something in it: the local one, or hosted each one stored."""
        if not self.hosted:
            return [LOCAL]
        return [w for w in self.database.workspaces() if _ID.match(w) and w != LOCAL]

    async def close(self) -> None:
        opened, self._open = list(self._open.values()), {}
        for workspace in opened:
            await workspace.close()
