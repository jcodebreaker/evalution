"""The HTTP API (docs/architecture.md "API and page"), and the page from ``web/dist`` when it is
built.

No login: evalution answers only this computer (loopback clients) unless ``serve --host`` was
set to something else on purpose (``create_app(config, remote=True)``), and then only under the
names :func:`evalution.api.deps.answers_to` gives. Where a route names a
set, the target comes as ``?target=`` or a body ``target``; it may be left out when exactly one
target is configured.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse

from evalution.api.benchmarks import benchmarks_router
from evalution.api.connect import connect_router
from evalution.api.deps import answers_to, guard
from evalution.api.health import health_router
from evalution.api.plans import CasePlans
from evalution.api.runs import runs_router
from evalution.api.sets import sets_router
from evalution.api.settings import settings_router
from evalution.api.targets import target_router, targets_router
from evalution.api.tests import tests_router
from evalution.api.traces import traces_router
from evalution.api.understanding import understanding_router
from evalution.api.writer import writer_router
from evalution.config import Config
from evalution.connect import discover
from evalution.llm import LLM
from evalution.project import drift, impact, knowledge
from evalution.results.store import FolderUse
from evalution.runs.runner import Runs
from evalution.targets import Targets
from evalution.traces.managed import Supervisor
from evalution.writer.agent import Agent
from evalution.writer.sessions import SessionFiles

DAY = 24 * 3600


def create_router(
    runs: Runs,
    agent: Agent,
    found: discover.Discoveries,
    llm: LLM,
    watcher: impact.Watcher,
    url: str = "http://127.0.0.1:8080",
) -> APIRouter:
    """The ``/v1`` routes over *runs*, *agent*, the folder discoveries *found*, the model client
    *llm* every part shares and the *watcher* of code changes. *url* is where this server listens,
    which is what a hook calls."""
    targets = runs.targets
    # Saving the model and saving a target both rewrite the config, one at a time.
    lock = asyncio.Lock()
    plans = CasePlans(runs)
    router = APIRouter(prefix="/v1")

    @router.get("/health")
    async def get_health() -> dict[str, bool]:
        """That the server answers, and nothing else: for a container's health check."""
        return {"ok": True}

    # The first match wins, so this order is behaviour: /targets/discover/{job_id} must come
    # before /targets/{target}/sets and the other one-target routes.
    for part in (
        traces_router(targets),
        targets_router(runs),
        settings_router(targets, llm, lock),
        connect_router(runs, found, lock),
        target_router(runs),
        understanding_router(runs, agent, url),
        health_router(runs, watcher, llm),
        sets_router(runs),
        benchmarks_router(runs),
        tests_router(runs, plans),
        runs_router(runs, plans),
        writer_router(targets, agent),
    ):
        router.include_router(part)
    return router


async def _daily_drift(runs: Runs) -> None:
    """Check every target for drift, and what it knows against its commits, once a day."""
    while True:
        await asyncio.sleep(DAY)
        for name in runs.targets.names():
            await drift.check_quietly(runs.targets, name)
            await knowledge.refresh_quietly(runs, name)


def create_app(
    config: Config,
    remote: bool = False,
    phoenix: Supervisor | None = None,
    url: str = "http://127.0.0.1:8080",
    bind: str | None = None,
) -> FastAPI:
    """The app. *remote* lets in clients from other computers (``serve --host``, the *bind*), with
    no login. *phoenix* is evalution's own Phoenix, started and stopped with the app. *url* is where
    this server listens, which is where apps send traces with the built-in source.

    :raises RuntimeError: On start, a restore is going on this data folder.
    """
    targets = Targets(config)
    if targets.store is not None:
        targets.managed_url = url
    llm = LLM(config.model, spend_file=config.spend_file)
    runs = Runs(targets, llm)
    agent = Agent(runs, SessionFiles(config.data_dir), llm)
    found = discover.Discoveries(
        config.root,
        lambda: list(targets.config.targets),
        llm,
        lambda: targets.managed_url,
        targets.store,
    )
    watcher = impact.Watcher(runs, knowledge.refresh)
    use = FolderUse(config.data_dir)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if not use.serving():
            raise RuntimeError("a restore is going on this data folder: let it end first")
        if phoenix is not None:
            try:
                for note in await phoenix.start():
                    print(f"evalution: {note}", file=sys.stderr)
                targets.managed_url = phoenix.url
            except RuntimeError as exc:
                print(f"evalution: {exc}; using each target's own Phoenix", file=sys.stderr)
        daily = asyncio.create_task(_daily_drift(runs))
        try:
            yield
        finally:
            daily.cancel()
            await watcher.close()
            if phoenix is not None:
                await phoenix.stop()
            await targets.close()
            await llm.close()
            use.release()

    app = FastAPI(title="evalution", lifespan=lifespan)
    app.state.runs = runs
    app.state.remote = remote
    app.state.allowed_hosts = answers_to(bind, config.allowed_hosts)
    app.middleware("http")(guard)
    app.include_router(create_router(runs, agent, found, llm, watcher, url))
    web = config.web_dir
    if (web / "index.html").is_file():

        @app.get("/{path:path}", include_in_schema=False)
        async def page(path: str) -> FileResponse:
            """The built page; any path that is not a file is the page itself (its router)."""
            found = (web / path).resolve()
            if path and found.is_relative_to(web) and found.is_file():
                return FileResponse(found)
            return FileResponse(web / "index.html")

    return app
