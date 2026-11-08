"""The HTTP API (docs/architecture.md "API and page"), and the page from ``web/dist`` when it is
built.

Hosted, people sign in and each request runs in its own workspace (:mod:`.workspaces`). Local,
no login: evalution answers only this computer (loopback clients) unless ``serve --host`` was
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
from sqlalchemy.exc import SQLAlchemyError

from evalution.accounts.settings import AuthSettings
from evalution.api.auth import mount_accounts
from evalution.api.benchmarks import benchmarks_router
from evalution.api.connect import connect_router
from evalution.api.deps import answers_to, guard
from evalution.api.health import health_router
from evalution.api.isolation import isolation_router
from evalution.api.runs import runs_router
from evalution.api.sets import sets_router
from evalution.api.settings import settings_router
from evalution.api.targets import target_router, targets_router
from evalution.api.tests import tests_router
from evalution.api.traces import traces_router
from evalution.api.understanding import understanding_router
from evalution.api.workspaces import Workspaces
from evalution.api.writer import writer_router
from evalution.config import Config, ConfigError
from evalution.project import drift, isolating, knowledge
from evalution.results.store import FolderUse
from evalution.storage import LOCAL, Database
from evalution.traces.builtin import prune

DAY = 24 * 3600


def create_router(url: str = "http://127.0.0.1:8080") -> APIRouter:
    """The ``/v1`` routes; each runs in the request's workspace (:func:`deps.workspace`). *url* is
    where this server listens, which is what a hook calls."""
    router = APIRouter(prefix="/v1")

    @router.get("/health")
    async def get_health() -> dict[str, bool]:
        """That the server answers, and nothing else: for a container's health check."""
        return {"ok": True}

    # The first match wins, so this order is behaviour: /targets/discover/{job_id} must come
    # before /targets/{target}/sets and the other one-target routes.
    for part in (
        traces_router(),
        targets_router(),
        settings_router(),
        connect_router(),
        target_router(),
        understanding_router(url),
        health_router(),
        isolation_router(),
        sets_router(),
        benchmarks_router(),
        tests_router(),
        runs_router(),
        writer_router(),
    ):
        router.include_router(part)
    return router


async def _daily_drift(workspaces: Workspaces) -> None:
    """Check every workspace's targets for drift, and what each knows against its commits, once a
    day. No one asked, so a model call uses the key of the workspace's earliest member."""
    while True:
        await asyncio.sleep(DAY)
        for ws_id in await asyncio.to_thread(workspaces.ids):
            try:
                ws = workspaces.get(ws_id)
            except ConfigError as exc:
                print(f"evalution: workspace {ws_id}: {exc}", file=sys.stderr)
                continue
            for name in ws.targets.names():
                await drift.check_quietly(ws.targets, name)
                await knowledge.refresh_quietly(ws.runs, name)
                await isolating.mark_stale_quietly(ws.targets, name)


async def _daily_prune(database: Database, keep_days: int) -> None:
    """Drop spans past ``traces.keep_days``, now and once a day: one pass over every workspace."""
    while True:
        try:
            await asyncio.to_thread(prune, database, keep_days)
        except SQLAlchemyError as exc:
            print(f"evalution: old spans not pruned: {type(exc).__name__}", file=sys.stderr)
        await asyncio.sleep(DAY)


def create_app(
    config: Config,
    remote: bool = False,
    url: str = "http://127.0.0.1:8080",
    bind: str | None = None,
) -> FastAPI:
    """The app. *remote* lets in clients from other computers (``serve --host``, the *bind*), with
    no login. *url* is where this server listens, which is where apps on this computer send
    traces; hosted, they send them to its public address.

    Hosted (:meth:`AuthSettings.from_env`), each request runs in its own workspace; locally, every
    one runs in :data:`LOCAL`, opened here so a config that doesn't read fails now.

    :raises RuntimeError: On start, a restore is going on this data folder.
    :raises AuthSettingsError: Hosted, but a sign-in setting is missing.
    """
    auth = AuthSettings.from_env()
    hosted = auth is not None
    database = Database.open(config.data_dir)
    workspaces = Workspaces(config, database, hosted, auth.public_url if auth else url)
    local = None if hosted else workspaces.get(LOCAL)
    use = FolderUse(config.data_dir)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if not use.serving():
            raise RuntimeError("a restore is going on this data folder: let it end first")
        daily = [
            asyncio.create_task(_daily_drift(workspaces)),
            asyncio.create_task(_daily_prune(database, config.traces.keep_days)),
        ]
        try:
            yield
        finally:
            for task in daily:
                task.cancel()
            await workspaces.close()
            use.release()

    app = FastAPI(title="evalution", lifespan=lifespan)
    app.state.hosted = hosted
    app.state.workspaces = workspaces
    if local is not None:
        #: The local workspace's runs, as the CLI and tests reach them.
        app.state.runs = local.runs
    app.state.remote = remote
    app.state.allowed_hosts = answers_to(bind, config.allowed_hosts)
    mount_accounts(app, workspaces.database)
    app.middleware("http")(guard)
    app.include_router(create_router(url))
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
