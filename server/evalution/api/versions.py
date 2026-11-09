"""A target's versions (ADR 0011): the refs of its repository, a remote branch fetched into
evalution's own refs, and the worktrees evalution keeps, with their sizes. Local only: hosted has
no copies, so no versions."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request

from evalution.api.deps import Current, busy, invalid, not_found, target_name
from evalution.api.workspaces import Workspace
from evalution.project import building, versions

HOSTED = "Versions are local only: hosted evalution has no copies of apps to play them on."


def _local(request: Request) -> None:
    """:raises HTTPException: 400, hosted."""
    if request.app.state.hosted:
        raise invalid(HOSTED)


def _versions(ws: Workspace, name: str) -> versions.Versions:
    """:raises HTTPException: 400, no code folder or not a git checkout. What evalution built of
    each version counts with it, and goes with it."""
    try:
        return building.versions_of(ws.targets, name)
    except versions.VersionError as exc:
        raise invalid(str(exc)) from exc


def versions_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(_local)])

    @router.get("/targets/{target}/versions")
    async def get_versions(ws: Current, target: str) -> dict[str, Any]:
        """The repository's branches, remote branches and tags, each with its commit, subject
        and date, which is checked out and which evalution keeps a worktree of; the worktrees
        with their sizes, the limit and the disk's free space."""
        name = target_name(ws.targets, target)

        def view() -> dict[str, Any]:
            return _versions(ws, name).view()

        return await asyncio.to_thread(view)

    @router.post("/targets/{target}/versions/fetch")
    async def fetch_version(ws: Current, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Fetch ``{remote, branch}`` into evalution's own refs, never the person's: the commit
        it is at."""
        name = target_name(ws.targets, target)
        remote, branch = body.get("remote"), body.get("branch")
        if not isinstance(remote, str) or not remote.strip():
            raise invalid("remote: the remote's name, e.g. origin")
        if not isinstance(branch, str) or not branch.strip():
            raise invalid("branch: the branch's name on the remote")

        def fetch() -> dict[str, Any]:
            found = _versions(ws, name)
            try:
                commit = found.fetch(remote, branch)
            except versions.VersionError as exc:
                raise invalid(str(exc)) from exc
            return {"remote": remote, "branch": branch, "commit": commit}

        return await asyncio.to_thread(fetch)

    @router.get("/targets/{target}/versions/remote")
    async def remote_branches(
        ws: Current, target: str, remote: str | None = None
    ) -> dict[str, Any]:
        """The branches *remote* has now (``origin`` by default, else the first remote), listed
        with ``git ls-remote`` and the person's own credentials: nothing fetched, nothing
        written. Each says whether evalution fetched it."""
        name = target_name(ws.targets, target)

        def listed() -> dict[str, Any]:
            found = _versions(ws, name)
            remotes = found.remotes()
            chosen = remote or ("origin" if "origin" in remotes else (remotes or [""])[0])
            if not chosen:
                raise invalid(
                    f"{found.top.name} has no remote, so only the branches and tags here are listed"
                )
            try:
                branches = found.remote_branches(chosen)
            except versions.VersionError as exc:
                raise invalid(str(exc)) from exc
            return {"remote": chosen, "branches": [b.as_dict() for b in branches]}

        return await asyncio.to_thread(listed)

    @router.delete("/targets/{target}/versions/{commit}")
    async def remove_version(ws: Current, target: str, commit: str) -> dict[str, Any]:
        """Remove the version evalution keeps of *commit*: its worktree, nothing of the
        person's. Not while a run plays."""
        name = target_name(ws.targets, target)
        if ws.runs.busy():
            raise busy("A run is going. Remove versions when it ends.")

        def remove() -> dict[str, Any]:
            found = _versions(ws, name)
            try:
                gone = found.remove(commit)
            except versions.VersionError as exc:
                raise invalid(str(exc)) from exc
            except LookupError as exc:
                raise not_found("Version") from exc
            return {"removed": gone.as_dict(), **found.view()}

        return await asyncio.to_thread(remove)

    return router
