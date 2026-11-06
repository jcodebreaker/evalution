"""The langgraph adapter (ADR 0002, "a second stateful adapter"): plays a LangGraph graph served
by the LangGraph Server API (``langgraph dev``, ``langgraph up`` or LangGraph Platform), over
plain HTTP.

.. code-block:: yaml

    my-agent:
      adapter: langgraph
      url: http://127.0.0.1:2024              # langgraph dev's usual address
      assistant_id: agent                     # an assistant id, or a graph name in langgraph.json
      api_key_env: MY_AGENT_KEY               # optional, sent as x-api-key
      user_field: configurable.user_id        # where the eval user goes in the run
      store_namespace: ["memories", "{user}"] # the user's long-term memory in the store
      trace_attribute: session.id             # spans are found by this, valued with the thread id

**Who plays.** Each ``open(run)`` plays as a fresh user ``eval-<run>``, set at ``user_field`` of
every run (``configurable.<key>``, which the server also passes as the run's context, or
``context.<key>`` / ``metadata.<key>``), so every session is its own identity and sessions may
run at once. The session is a thread, made with metadata ``evalution_user`` so it can be found
and deleted later.

**Traces.** Spans are found by ``trace_attribute`` = the thread id. OpenInference's LangChain
instrumentation sets ``session.id`` from the run's ``thread_id`` metadata, which LangGraph copies
from the config; an app traced some other way sets it itself (``using_session(thread_id)``, or
``session_id`` in the run metadata).

**Between tries** (:meth:`LangGraphTarget.reset`): a new thread, and every store item under the
user's namespace deleted. **A break** (:meth:`LangGraphTarget.break_conversation`): a new thread
for the same user, store kept, as when a person comes back another day.

**A turn** (:meth:`LangGraphTarget.send`): ``POST /threads/{id}/runs`` with ``{"messages":
[{"role": "user", "content": message}]}`` as input, then ``GET .../runs/{run}/join`` until it
ends; Stop is ``POST .../runs/{run}/cancel?action=interrupt``. The reply is the text of the last
AI message in the thread's values.

**State checks** (:meth:`LangGraphTarget.state`): ``memory_contains`` / ``memory_lacks`` (an
item in the user's store namespace contains the text, or none does; waits up to
:data:`STATE_WAIT_SECONDS` for background writes), ``checkpoint`` (the thread state holds a
summary: ``summary``, ``running_summary`` or ``context.running_summary``),
``messages_trimmed`` (the thread holds fewer user messages than were sent on it) and
``state_contains`` (``"<json path> <text>"``, or just text: the thread values, at that path,
contain the text).

**At the end** (:meth:`LangGraphTarget.close`): what the run made stays. With ``cleanup: true`` on
the target, the user's threads and store items are deleted, and no one else's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx

from evalution.adapters.base import (
    COMPOSE_PORT,
    NO_FILES,
    NOT_APP_PORTS,
    AdapterInfo,
    Capabilities,
    Detection,
    Folder,
    Isolation,
    MemoryRead,
    Session,
    StateResult,
    Target,
    TargetGone,
    Turn,
    failure_of,
    lost_connection,
    may_have_heard,
    part,
    text_options,
)
from evalution.adapters.http import json_path
from evalution.config import ConfigError, TargetConfig
from evalution.core import net
from evalution.core.evidence import Failure, TurnEnd

USER_PREFIX = "eval-"
USER_METADATA = "evalution_user"
DEFAULT_NAMESPACE = ("memories", "{user}")
#: Where a summarising graph keeps its summary in the thread values.
SUMMARY_PATHS = ("$.summary", "$.running_summary", "$.context.running_summary")
STATE_WAIT_SECONDS = 30.0
_POLL = 1.0
_TIMEOUT = 30.0
_PAGE = 100
#: Threads one eval user may leave (the search's own maximum).
_THREADS = 1000
_RUN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
#: ``langgraph dev`` and ``langgraph up`` serve here unless told otherwise.
DEV_PORT, UP_PORT = 2024, 8123
_SERVE_PORT = re.compile(r"langgraph (?:dev|up)\b[^\n]*?--port[ =](\d{2,5})")


class LangGraphTarget(Target):
    """A LangGraph graph, played through the LangGraph Server API as one eval user per run."""

    capabilities = Capabilities(
        isolation=Isolation.IDENTITY,
        isolation_note="Each run plays as a new user; memory kept outside the store carries over.",
        reset=True,
        conversation_break=True,
        stop=True,
        parallel=True,
        cancel=True,
        blocks=True,
        state_checks=frozenset(
            {"memory_contains", "memory_lacks", "checkpoint", "messages_trimmed", "state_contains"}
        ),
    )

    def __init__(self, target: TargetConfig, transport: httpx.AsyncBaseTransport | None = None):
        options = target.options
        self.name = target.name
        self.url = str(options.get("url") or "").rstrip("/")
        self.assistant = str(options.get("assistant_id") or "")
        if not self.url or not self.assistant:
            raise ConfigError(f"targets.{target.name}: url and assistant_id are required")
        self.key_env = options.get("api_key_env")
        self.keys = target.keys
        self.user_field = str(options.get("user_field") or "configurable.user_id")
        namespace = options.get("store_namespace") or list(DEFAULT_NAMESPACE)
        # Reset and a cleanup delete everything under the namespace: it must be the eval user's own.
        if not (
            isinstance(namespace, list)
            and all(isinstance(p, str) and p for p in namespace)
            and any("{user}" in p for p in namespace)
        ):
            raise ConfigError(
                f"targets.{target.name}.store_namespace: a list of strings with a {{user}} part"
            )
        self.namespace = namespace
        if options.get("cleanup"):
            self.capabilities = replace(self.capabilities, deletes=True)
        self.trace_attribute = str(options.get("trace_attribute") or "session.id")
        self._transport = transport

    # ---- the API ---------------------------------------------------------------------------

    def _client(self, seconds: float = _TIMEOUT) -> httpx.AsyncClient:
        headers = {}
        if value := self.keys.get(self.key_env):
            headers["x-api-key"] = value
        return net.client(
            base_url=self.url, headers=headers, timeout=seconds, transport=self._transport
        )

    async def _call(
        self, method: str, path: str, body: Any = None, *, seconds: float = _TIMEOUT
    ) -> Any:
        async with self._client(seconds) as client:
            response = await client.request(method, path, json=body)
        response.raise_for_status()
        return response.json() if response.content.strip() else None

    def user(self, run: str) -> str:
        if not _RUN.match(run):
            raise ValueError(f"not a run id: {run}")
        return f"{USER_PREFIX}{run}"

    def _namespace(self, user: str) -> list[str]:
        return [part.replace("{user}", user) for part in self.namespace]

    async def _thread(self, user: str) -> str:
        made = await self._call("POST", "/threads", {"metadata": {USER_METADATA: user}})
        return str(made["thread_id"])

    def _run_body(self, session: Session, message: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "assistant_id": self.assistant,
            "input": {"messages": [{"role": "user", "content": message}]},
            "metadata": {"evalution_run": session.extra.get("run", "")},
        }
        if fresh := session.extra.get("fresh"):
            body["metadata"]["evalution_fresh"] = fresh
        parts = self.user_field.split(".")
        if parts[0] == "configurable":
            parts = ["config", *parts]
        place = body
        for key in parts[:-1]:
            place = place.setdefault(key, {})
        place[parts[-1]] = session.extra["user"]
        return body

    # ---- the Target protocol ---------------------------------------------------------------

    async def open(self, run: str) -> Session:
        user = self.user(run)
        thread = await self._thread(user)
        return Session(thread, self.trace_attribute, thread, {"user": user, "run": run, "sent": 0})

    async def reset(self, session: Session) -> None:
        await self._forget(session.extra["user"])
        await self.break_conversation(session)

    async def break_conversation(self, session: Session) -> None:
        """Carry on in a new thread as the same user: the store stays, the chat does not."""
        thread = await self._thread(session.extra["user"])
        session.id = session.trace_value = thread
        session.extra["sent"] = 0

    async def send(
        self,
        session: Session,
        message: str,
        *,
        files: list[Path] = (),  # type: ignore[assignment]
        stop_after: float | None = None,
        deadline: float,
    ) -> Turn:
        started = time.time()
        if files:
            return Turn("", started, started, error=NO_FILES)
        runs = f"/threads/{session.id}/runs"
        run_id = None
        try:
            run = await self._call("POST", runs, self._run_body(session, message))
            session.extra["sent"] = int(session.extra.get("sent") or 0) + 1
            run_id = str(run["run_id"])
            join = asyncio.ensure_future(self._join(f"{runs}/{run_id}/join", deadline))
            try:
                if stop_after:
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(join), stop_after)
                    if not join.done():
                        await self._call("POST", f"{runs}/{run_id}/cancel?action=interrupt")
                values = await join
            finally:
                join.cancel()
        except (TimeoutError, httpx.TimeoutException):
            if run_id:
                with contextlib.suppress(httpx.HTTPError):
                    await self._call("POST", f"{runs}/{run_id}/cancel?action=interrupt")
            error = "the turn did not finish in time"
            return Turn("", started, time.time(), error=error, failure=Failure.TIMED_OUT)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise TargetGone("the eval thread is gone") from exc
            said = f"{exc.response.status_code} from {exc.request.url.path}"
            return Turn(
                "",
                started,
                time.time(),
                error=f"langgraph answered {said}",
                failure=failure_of(exc),
                heard=run_id is not None or may_have_heard(exc),
            )
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return Turn(
                "",
                started,
                time.time(),
                error=net.said(exc),
                failure=failure_of(exc),
                heard=run_id is not None,
                lost=lost_connection(exc),
            )
        error, ended_as = None, TurnEnd.ANSWERED
        if isinstance(values, dict) and values.get("__error__"):
            error, ended_as = f"the run failed: {_text(values['__error__'])[:300]}", TurnEnd.FAILED
        elif isinstance(values, dict) and values.get("__interrupt__"):
            error, ended_as = "the run stopped to wait for input (an interrupt)", TurnEnd.BLOCKED
        return Turn(_reply(values), started, time.time(), error=error, ended_as=ended_as)

    async def _join(self, path: str, deadline: float) -> Any:
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError
        # The server keeps the connection alive with whitespace, so no read timeout fires.
        return await asyncio.wait_for(self._call("GET", path, seconds=left), left)

    async def read_memory(self, session: Session) -> MemoryRead | None:
        items = await self._items(session.extra["user"])
        return MemoryRead("\n\n".join(_text(item.get("value")) for item in items))

    async def state(self, session: Session, check: dict[str, Any]) -> StateResult:
        ((key, value),) = check.items()
        if key in ("memory_contains", "memory_lacks"):
            needle, want = str(value), key == "memory_contains"
            stop = time.time() + STATE_WAIT_SECONDS
            while True:
                items = await self._items(session.extra["user"])
                found = [i for i in items if needle.casefold() in _text(i.get("value")).casefold()]
                if bool(found) == want or time.time() >= stop:
                    break
                await asyncio.sleep(_POLL)
            actual = f"{len(found)} of {len(items)} memory item(s) contain {needle!r}"
            return StateResult(bool(found) == want, actual, actual)
        if key not in self.capabilities.state_checks:
            return StateResult(False, None, f"langgraph has no {key!r} check", observed=False)
        values = (await self._call("GET", f"/threads/{session.id}/state") or {}).get("values")
        if key == "checkpoint":
            want = bool(value)
            summary = next(
                (s for p in SUMMARY_PATHS if (s := _at(values, p)) not in (None, "", {}, [])),
                None,
            )
            actual = f"summary: {_text(summary)[:200]}" if summary is not None else None
            ok = (summary is not None) == want
            missing = (
                "no summary in the thread state (looked for "
                + ", ".join(p.removeprefix("$.") for p in SUMMARY_PATHS)
                + ")"
                if want
                else "the thread state holds a summary"
            )
            return StateResult(ok, actual, (actual or "no summary") if ok else missing)
        if key == "messages_trimmed":
            messages = _at(values, "$.messages")
            if not isinstance(messages, list):
                said = "the thread state has no messages to count"
                return StateResult(False, None, said, observed=False)
            kept = sum(1 for m in messages if _role(m) == "human")
            sent = int(session.extra.get("sent") or 0)
            trimmed = kept < sent
            actual = f"{kept} of {sent} user message(s) sent on this thread are kept"
            ok = trimmed == bool(value)
            return StateResult(ok, actual, actual)
        path, needle = "$", str(value)
        if needle.startswith("$"):
            path, _, needle = needle.partition(" ")
        needle = needle.strip()
        found = _at(values, path)
        ok = found is not None and needle.casefold() in _text(found).casefold()
        actual = _text(found)[:200] if found is not None else None
        said = f"{path} {'contains' if ok else 'lacks'} {needle!r}"
        return StateResult(ok, actual, said, observed=values is not None)

    async def cancel(self, session: Session) -> None:
        """Stop this thread's runs that are still going: asked of the server, so it works for a
        session an earlier process left open."""
        runs = f"/threads/{session.id}/runs"
        for run in await self._call("GET", runs) or []:
            if run.get("status") in ("pending", "running"):
                await self._call("POST", f"{runs}/{run['run_id']}/cancel?action=interrupt")

    async def close(self, session: Session) -> None:
        """Keeps what the run made, unless the target sets ``cleanup``: then this run's eval user's
        threads and store items go, and no one else's."""
        if not self.capabilities.deletes:
            return
        user = session.extra["user"]
        if not user.startswith(USER_PREFIX):
            raise ValueError(f"not an eval user: {user}")
        search = {"metadata": {USER_METADATA: user}, "limit": _THREADS}
        for thread in await self._call("POST", "/threads/search", search) or []:
            # The filter is the server's to honour: what's deleted is checked here too.
            if (thread.get("metadata") or {}).get(USER_METADATA) == user:
                await self._call("DELETE", f"/threads/{thread['thread_id']}")
        await self._forget(user)

    # ---- the store -------------------------------------------------------------------------

    async def _items(self, user: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        while True:
            body = {"namespace_prefix": self._namespace(user), "limit": _PAGE, "offset": len(found)}
            page = ((await self._call("POST", "/store/items/search", body)) or {}).get("items")
            if not isinstance(page, list):
                raise ValueError("the store's answer holds no items")  # not read, not empty
            found.extend(page)
            if not page or len(page) < _PAGE:
                return found

    async def _forget(self, user: str) -> None:
        """Delete every store item under *user*'s namespace."""
        for item in await self._items(user):
            await self._call(
                "DELETE", "/store/items", {"namespace": item["namespace"], "key": item["key"]}
            )


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _at(values: Any, path: str) -> Any:
    try:
        return json_path(values, path)
    except ValueError:
        return None


def _role(message: Any) -> str:
    """A serialised message's role: ``human``, ``ai``, ``tool``, ``system`` (LangChain's
    ``type``), from ``type`` or OpenAI-style ``role``."""
    if not isinstance(message, dict):
        return ""
    role = str(message.get("type") or message.get("role") or "")
    return {"user": "human", "assistant": "ai", "AIMessageChunk": "ai"}.get(role, role)


def _content(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        return "".join(
            str(b.get("text") or "") if isinstance(b, dict) else str(b)
            for b in content
            if not isinstance(b, dict) or b.get("type") in (None, "text")
        )
    return _text(content)


def _reply(values: Any) -> str:
    """The text of the last AI message after the last user message in the thread values."""
    messages = _at(values, "$.messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        role = _role(message)
        if role == "human":
            return ""
        if role == "ai" and (text := _content(message)):
            return text
    return ""


def create(target: TargetConfig) -> LangGraphTarget:
    return LangGraphTarget(target)


async def _check(
    client: httpx.AsyncClient, options: dict[str, Any], lookup: Callable[[Any], str | None]
) -> dict[str, Any]:
    url = str(options.get("url") or "").rstrip("/")
    ok = await client.get(f"{url}/ok")
    if not ok.is_success:
        return part(False, f"/ok answered {ok.status_code}")
    key = lookup(options.get("api_key_env"))
    headers = {"x-api-key": key} if key else {}
    found = await client.post(f"{url}/assistants/search", json={"limit": 100}, headers=headers)
    if found.status_code in (401, 403):
        return part(False, "The API key was refused", refused=True)
    if not found.is_success:
        return part(False, f"/assistants/search answered {found.status_code}")
    wanted = str(options.get("assistant_id") or "")
    if not any(wanted in (a.get("assistant_id"), a.get("graph_id")) for a in found.json()):
        return part(False, f"No assistant or graph named {wanted!r}")
    return part(True, f"Reachable, and it serves {wanted!r}")


def _detect(folder: Folder) -> Detection | None:
    """A LangGraph project (``langgraph.json``, or ``langgraph`` among its packages). The URL: a
    ``--port`` given to ``langgraph dev`` / ``up`` in the README or scripts, else a published port
    of a compose service that mentions langgraph, else ``langgraph up``'s 8123 when that is how it
    runs, else ``langgraph dev``'s 2024. The assistant: the first graph in ``langgraph.json``."""
    config = folder.path / "langgraph.json"
    graphs: dict[str, Any] = {}
    if config.is_file():
        try:
            found = json.loads(config.read_text(encoding="utf-8", errors="replace"))
        except ValueError:
            found = {}
        graphs = found.get("graphs") if isinstance(found, dict) else None
        graphs = graphs if isinstance(graphs, dict) else {}
    elif not re.search(r"(?<![\w-])langgraph(?![\w])", folder.packages()):
        return None
    files = folder.files
    text = "\n".join(t for r, t in files.items() if "compose" not in r.lower())
    compose = [t for r, t in files.items() if "compose" in r.lower() and "langgraph" in t]
    port = None
    if found_port := _SERVE_PORT.search(text):
        port = int(found_port[1])
    for compose_text in compose:
        ports = [int(p) for p in COMPOSE_PORT.findall(compose_text) if int(p) not in NOT_APP_PORTS]
        port = port or (ports[0] if ports else None)
    if port is None:
        port = UP_PORT if "langgraph up" in text else DEV_PORT
    graph = next(iter(graphs), "agent")
    return Detection(
        base=f"http://127.0.0.1:{port}",
        options={"assistant_id": graph},
        steps=[(f"It's a LangGraph app (graph {graph})", "done")],
    )


def _validate(options: dict[str, Any]) -> None:
    namespace = options.get("store_namespace", [])
    if not isinstance(namespace, list) or not all(isinstance(p, str) and p for p in namespace):
        raise ValueError("options.store_namespace: a list of strings")
    text_options({k: v for k, v in options.items() if k != "store_namespace"})


INFO = AdapterInfo(
    name="langgraph",
    label="LangGraph adapter",
    options=("url", "assistant_id", "user_field", "store_namespace", "trace_attribute"),
    required=("url", "assistant_id"),
    secrets=("api_key",),
    check=_check,
    detect=_detect,
    rank=1,
    refused="The LangGraph server refused the request.",
    framework=("langgraph", "LangGraph"),
    validate=_validate,
)
