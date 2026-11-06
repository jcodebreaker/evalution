"""``evalution run --server``: play an app where it runs, keep the run in a workspace elsewhere
(docs/hosting.md, "Run where your app runs").

A hosted server only reaches apps on the internet. One that runs only on someone's computer or in
CI is played there by the CLI: it takes the target's tests from the server first, so it plays what
the workspace holds, and sends the run and its evidence back after. The app's settings and the
model key stay local; the server only ever sees the run.
"""

from __future__ import annotations

from typing import Any

import httpx

from evalution.results.store import RunStore
from evalution.storage import Docs
from evalution.suite.store import put_bundle

#: Where the CLI reads a workspace API token from when ``--token`` doesn't give one.
TOKEN_ENV = "EVALUTION_TOKEN"  # noqa: S105 - the variable's name, not a token
#: An upload of a long run can take a while on a slow link.
TIMEOUT_SECONDS = 120.0


class RemoteError(Exception):
    """The server couldn't be reached or refused; the words say which."""


def connect(server: str, token: str | None) -> httpx.Client:
    """A client of the API at *server* (``https://evals.example.com``), signed in by *token* when
    there is one; a local evalution needs none."""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(
        base_url=f"{server.rstrip('/')}/v1", headers=headers, timeout=TIMEOUT_SECONDS
    )


def _call(client: httpx.Client, what: str, method: str, path: str, **kw: Any) -> dict[str, Any]:
    """The JSON answer. Errors say what the server said, never what was sent: it holds the token.

    :raises RemoteError: Not reached, or not a success.
    """
    try:
        response = client.request(method, path, **kw)
    except httpx.HTTPError as exc:
        raise RemoteError(f"{what}: {type(exc).__name__}") from exc
    try:
        found = response.json()
    except ValueError:
        found = None
    if not response.is_success:
        said = found.get("detail") if isinstance(found, dict) else None
        raise RemoteError(f"{what}: {response.status_code} {said or response.reason_phrase}")
    if not isinstance(found, dict):
        raise RemoteError(f"{what}: the server didn't answer with JSON")
    return found


def fetch_sets(client: httpx.Client, docs: Docs, target: str, set_name: str) -> list[str]:
    """Set *set_name* of *target* from the server into *docs*, replacing the local one whole, as
    ``sets import`` would; ``<target>/<set>`` of each.

    :raises RemoteError: The server refused, e.g. it has no such app or set.
    :raises CaseError: What came isn't a set.
    """
    found = _call(
        client,
        "getting the tests",
        "GET",
        "/evals/sets/export",
        params={"target": target, "set": set_name},
    )
    sets = found.get("sets")
    if not isinstance(sets, dict):
        raise RemoteError("getting the tests: the answer has no sets")
    for slug, bundle in sets.items():
        put_bundle(docs, target, slug, bundle)
    return [f"{target}/{slug}" for slug in sets]


def upload(client: httpx.Client, store: RunStore, run: str) -> None:
    """Send *run* and its evidence to the server, which keeps it as one of its workspace's; sent
    again, it replaces itself.

    :raises RemoteError: No such run here, or the server refused it.
    """
    doc = store.load(run)
    if doc is None:
        raise RemoteError(f"uploading: no run {run}")
    evidence = [kept.model_dump(mode="json") for kept in store.evidence(run)]
    _call(
        client,
        "uploading the run",
        "POST",
        "/evals/runs/import",
        json={"run": doc, "evidence": evidence},
    )
