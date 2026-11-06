"""The letta adapter: plays a Letta Code agent through its App Server, one WebSocket at ``/ws``.

.. code-block:: yaml

    my-letta:
      adapter: letta
      url: http://127.0.0.1:4500        # letta server --backend local --listen ws://127.0.0.1:4500
      model: anthropic/claude-sonnet-5-5 # optional: the server's default otherwise
      tools: [Read, Write, Edit]        # optional: the client tools a turn may use
      cwd: /tmp/letta-eval              # optional: the agent's working folder
      api_key_env: MY_LETTA_TOKEN       # optional, sent as a bearer token

**Who plays.** Each ``open(run)`` makes a new agent named ``eval-<run>`` and a conversation with
it, so sessions share nothing and may run at once.

**Tools.** Letta keeps memory in files, edited with its client tools, so an agent with none can't
save anything. By default a turn gets :data:`FILE_TOOLS` and no shell. Writing outside the memory
folder asks for approval, which ends the turn as blocked. Reading isn't limited to that folder.

**Traces.** The App Server exports none that evalution can read, so tests of this target rest on
replies and memory checks. ``context_contains`` and tool checks have nothing to read.

**Between tries** (:meth:`LettaTarget.reset`): a new agent. **A break**
(:meth:`LettaTarget.break_conversation`): a new conversation with the same agent, which keeps its
memory.

**A turn** (:meth:`LettaTarget.send`): a socket for the turn, ``runtime_start`` on the session's
agent and conversation, then ``input``. The reply is the assistant text streamed before
``turn_finished``. Stop is ``abort_message``. A turn that asks to use a tool outside ``tools`` is
told no and goes on. Any other approval can't be answered, so the turn is stopped and ends as
blocked.

**State checks** (:meth:`LettaTarget.state`): ``memory_contains`` / ``memory_lacks``, over what is
committed in the agent's memory files, since a file only written didn't reach a new conversation
in testing (``docs/letta-findings.md``). Waits up to :data:`STATE_WAIT_SECONDS` for background
writes. A committed file deleted but not committed isn't listed, so ``memory_lacks`` can't see it.

**At the end** (:meth:`LettaTarget.close`): what the run made stays. With ``cleanup: true`` on the
target, the agents it made are deleted, and no one else's.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus, InvalidURI

from evalution.adapters.base import (
    NO_FILES,
    AdapterInfo,
    AppError,
    Capabilities,
    Isolation,
    MemoryRead,
    Session,
    StateResult,
    Target,
    TargetGone,
    Turn,
    failure_of,
    lost_connection,
    part,
    text_options,
)
from evalution.config import ConfigError, TargetConfig
from evalution.core import net
from evalution.core.evidence import Failure, TurnEnd

USER_PREFIX = "eval-"
PROTOCOL_VERSION = 1
FILE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")
STATE_WAIT_SECONDS = 30.0
_POLL = 1.0
#: Making an agent sets up its memory files, so asking may take a while.
_ANSWER_SECONDS = 120.0
_NOT_WORK = ("usage_statistics", "stop_reason")
_RUN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
#: How git says a file isn't in a commit.
_NOT_COMMITTED = re.compile(r"exists on disk, but not in|does not exist in")


def _ws_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = {"http": "ws", "https": "wss"}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, parts.path.rstrip("/") or "/ws", "", ""))


def _http_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, "", "", ""))


def _text(content: Any) -> str:
    """A message's text, from a string or a list of content parts."""
    if isinstance(content, list):
        return "".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return content if isinstance(content, str) else ""


class LettaTarget(Target):
    """A Letta Code App Server, played as one new agent per session."""

    capabilities = Capabilities(
        isolation=Isolation.IDENTITY,
        isolation_note="Each run plays with a new agent, which starts with no memory of others.",
        reset=True,
        conversation_break=True,
        stop=True,
        parallel=True,
        cancel=True,
        blocks=True,
        state_checks=frozenset({"memory_contains", "memory_lacks"}),
    )

    def __init__(self, target: TargetConfig):
        options = target.options
        self.name = target.name
        self.url = str(options.get("url") or "").rstrip("/")
        if not self.url:
            raise ConfigError(f"targets.{target.name}: url is required")
        tools = options.get("tools", list(FILE_TOOLS))
        if not (isinstance(tools, list) and all(isinstance(t, str) and t for t in tools)):
            raise ConfigError(f"targets.{target.name}.tools: a list of tool names")
        self.tools = tools
        self.model = options.get("model")
        self.cwd = options.get("cwd")
        self.key_env = options.get("api_key_env")
        self.keys = target.keys
        if options.get("cleanup"):
            self.capabilities = replace(self.capabilities, deletes=True)

    # ---- the socket ------------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _socket(self) -> AsyncIterator[ClientConnection]:
        """A socket to the server. What goes wrong reads as the HTTP errors the rest of evalution
        knows, so an app that isn't there or refuses the key ends a run the same way on any
        adapter."""
        address = _ws_url(self.url)
        key = self.keys.get(self.key_env)
        headers = {"Authorization": f"Bearer {key}"} if key else None
        request = httpx.Request("GET", _http_url(self.url))
        try:
            socket = await connect(
                address,
                additional_headers=headers,
                max_size=None,
                proxy=None,
                open_timeout=30,
            )
        except InvalidStatus as exc:
            status = exc.response.status_code
            response = httpx.Response(status, request=request)
            raise httpx.HTTPStatusError(
                f"{status} from {address}", request=request, response=response
            ) from exc
        except (OSError, TimeoutError, InvalidHandshake, InvalidURI) as exc:
            raise httpx.ConnectError(f"can't reach {address}: {exc}", request=request) from exc
        try:
            yield socket
        except ConnectionClosed as exc:
            raise httpx.ReadError(f"the connection to {address} broke", request=request) from exc
        finally:
            with contextlib.suppress(Exception):
                await socket.close()

    @staticmethod
    async def _next(socket: ClientConnection, seconds: float) -> dict[str, Any]:
        while True:
            raw = await asyncio.wait_for(socket.recv(), seconds)
            frame = json.loads(raw)
            if isinstance(frame, dict):
                return frame

    async def _ask(self, socket: ClientConnection, command: dict[str, Any]) -> dict[str, Any]:
        """Send *command* and wait for its answer, passing over everything else on the socket."""
        request = uuid.uuid4().hex
        await socket.send(json.dumps({**command, "request_id": request}))
        stop = time.monotonic() + _ANSWER_SECONDS
        while True:
            left = stop - time.monotonic()
            if left <= 0:
                raise TimeoutError
            frame = await self._next(socket, left)
            if frame.get("request_id") == request and frame.get("type") != "input_accepted":
                return frame

    async def _start(self, socket: ClientConnection, **scope: Any) -> dict[str, Any]:
        command = {"type": "runtime_start", "mode": "standard", **scope}
        if self.cwd:
            command["cwd"] = self.cwd
        answer = await self._ask(socket, command)
        if not answer.get("success") or not answer.get("runtime"):
            if scope.get("agent_id") and not scope.get("create_conversation"):
                raise TargetGone(f"the eval conversation is gone: {answer.get('error')}")
            raise AppError(f"letta could not start a session: {answer.get('error')}")
        return answer["runtime"]

    async def _new_agent(self, run: str) -> dict[str, Any]:
        if not _RUN.match(run):
            raise ValueError(f"not a run id: {run}")
        body: dict[str, Any] = {"name": f"{USER_PREFIX}{run}-{uuid.uuid4().hex[:8]}"}
        if self.model:
            body["model"] = self.model
        async with self._socket() as socket:
            return await self._start(
                socket,
                create_agent={"body": body, "pin_global": False},
                create_conversation={"body": {}},
            )

    # ---- the Target protocol ---------------------------------------------------------------

    async def open(self, run: str) -> Session:
        runtime = await self._new_agent(run)
        agent, conversation = runtime["agent_id"], runtime["conversation_id"]
        extra = {"run": run, "agent": agent, "agents": [agent]}
        return Session(conversation, "session.id", conversation, extra)

    async def reset(self, session: Session) -> None:
        runtime = await self._new_agent(session.extra["run"])
        session.id = session.trace_value = runtime["conversation_id"]
        session.extra["agent"] = runtime["agent_id"]
        session.extra["agents"] = [*session.extra.get("agents", []), runtime["agent_id"]]

    async def break_conversation(self, session: Session) -> None:
        """Carry on in a new conversation with the same agent: its memory stays."""
        async with self._socket() as socket:
            runtime = await self._start(
                socket, agent_id=session.extra["agent"], create_conversation={"body": {}}
            )
        session.id = session.trace_value = runtime["conversation_id"]

    def _scope(self, session: Session) -> dict[str, Any]:
        return {"agent_id": session.extra.get("agent"), "conversation_id": session.id}

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
        heard = [False]
        try:
            async with self._socket() as socket:
                return await self._turn(socket, session, message, stop_after, deadline, heard)
        except TimeoutError:
            error = "the turn did not finish in time"
            return Turn("", started, time.time(), error=error, failure=Failure.TIMED_OUT)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            return Turn(
                "",
                started,
                time.time(),
                error=net.said(exc),
                failure=failure_of(exc),
                heard=heard[0],
                lost=lost_connection(exc),
            )

    async def _turn(
        self,
        socket: ClientConnection,
        session: Session,
        message: str,
        stop_after: float | None,
        deadline: float,
        heard: list[bool],
    ) -> Turn:
        started = time.time()
        scope = self._scope(session)
        await self._start(socket, agent_id=scope["agent_id"], conversation_id=session.id)
        request = uuid.uuid4().hex
        payload = {
            "kind": "create_message",
            "messages": [{"role": "user", "content": message, "client_message_id": request}],
            "client_tool_allowlist": self.tools,
            "exclude_interactive_tools": True,
        }
        await socket.send(
            json.dumps(
                {"type": "input", "request_id": request, "runtime": scope, "payload": payload}
            )
        )
        heard[0] = True
        stop_at = started + stop_after if stop_after else None
        stopped, accepted, spoke, reply = False, False, False, []

        async def abort() -> None:
            await socket.send(json.dumps({"type": "abort_message", "runtime": scope}))

        while True:
            now = time.time()
            if now >= deadline:
                with contextlib.suppress(ConnectionClosed):
                    await abort()
                raise TimeoutError
            if stop_at is not None and now >= stop_at:
                stop_at, stopped = None, True
                await abort()
                continue
            wait = min(deadline, stop_at) if stop_at is not None else deadline
            try:
                frame = await self._next(socket, wait - now)
            except TimeoutError:
                continue
            kind = frame.get("type")
            if kind == "input_accepted" and frame.get("request_id") == request:
                if not frame.get("accepted"):
                    heard[0] = False
                    said = f"letta did not take the message: {frame.get('error')}"
                    return Turn("", started, time.time(), error=said, failure=Failure.TRANSIENT)
                accepted = True
            elif kind == "control_request":
                ask = frame.get("request") or {}
                if ask.get("tool_name") and ask["tool_name"] not in self.tools:
                    # Letta asks even about a tool it won't run: saying no lets the turn go on.
                    no = {"behavior": "deny", "message": "That tool isn't available here."}
                    body = {"kind": "approval_response", "request_id": frame.get("request_id")}
                    command = {
                        "type": "input",
                        "runtime": scope,
                        "payload": {**body, "decision": no},
                    }
                    await socket.send(json.dumps(command))
                    continue
                # Nobody here can approve, and it would wait until the deadline.
                await abort()
                said = "the turn stopped to ask for approval"
                ended = TurnEnd.BLOCKED
                return Turn("".join(reply), started, time.time(), error=said, ended_as=ended)
            elif kind == "stream_delta" and accepted and not frame.get("subagent_id"):
                delta = frame.get("delta") or {}
                speaking = delta.get("message_type") == "assistant_message"
                if speaking:
                    if reply and not spoke:  # it did something in between: a new message
                        reply.append("\n\n")
                    reply.append(_text(delta.get("content")))
                # What's said about the turn itself doesn't part one message from the next.
                if speaking or delta.get("message_type") not in _NOT_WORK:
                    spoke = speaking
            elif kind == "turn_finished" and accepted:
                return self._finished(frame, "".join(reply), started, stopped)

    @staticmethod
    def _finished(frame: dict[str, Any], reply: str, started: float, stopped: bool) -> Turn:
        reason = str(frame.get("stop_reason") or "")
        if reason == "end_turn" or (reason == "cancelled" and stopped):
            return Turn(reply, started, time.time())
        if reason == "requires_approval":
            said = "the turn stopped to ask for approval"
            return Turn(reply, started, time.time(), error=said, ended_as=TurnEnd.BLOCKED)
        said = f"the turn ended with {reason or 'no reason'}"
        if frame.get("error"):
            said += f": {str(frame['error'])[:300]}"
        return Turn(reply, started, time.time(), error=said, ended_as=TurnEnd.FAILED)

    async def state(self, session: Session, check: dict[str, Any]) -> StateResult:
        ((key, value),) = check.items()
        if key not in self.capabilities.state_checks:
            return StateResult(False, None, f"letta has no {key!r} check", observed=False)
        needle, want = str(value).casefold(), key == "memory_contains"
        agent = session.extra.get("agent")
        stop = time.time() + STATE_WAIT_SECONDS
        while True:
            files = await self._memory(agent)
            if files is None:
                said = "this agent's memory files are off, so they can't be read"
                return StateResult(False, None, said, observed=False)
            committed = await self._committed(agent, files)
            found = sum(needle in text.casefold() for text in committed)
            if bool(found) == want or time.time() >= stop:
                break
            await asyncio.sleep(_POLL)
        written = sum(needle in str(f.get("content")).casefold() for f in files)
        actual = f"{found} of {len(committed)} committed memory file(s) contain {value!r}"
        if written > found:
            actual += f"; {written - found} more written but not committed"
        return StateResult(bool(found) == want, actual, actual)

    async def read_memory(self, session: Session) -> MemoryRead | None:
        agent = session.extra.get("agent")
        files = await self._memory(agent)
        if files is None:
            return None
        written = "\n\n".join(str(f.get("content") or "") for f in files)
        return MemoryRead("\n\n".join(await self._committed(agent, files)), written)

    async def _committed(self, agent: str | None, files: list[dict[str, Any]]) -> list[str]:
        """The committed text of each of *files*. A file that was never committed is left out."""
        texts = []
        async with self._socket() as socket:
            for file in files:
                if file.get("kind") == "image":
                    continue
                command = {
                    "type": "memory_file_at_ref",
                    "agent_id": agent,
                    "file_path": file.get("relative_path"),
                    "ref": "HEAD",
                }
                answer = await self._ask(socket, command)
                if answer.get("success"):
                    texts.append(str(answer.get("content")))
                elif not _NOT_COMMITTED.search(str(answer.get("error"))):
                    raise ValueError(f"letta could not read memory: {answer.get('error')}")
        return texts

    async def _memory(self, agent: str | None) -> list[dict[str, Any]] | None:
        """The agent's memory files, or ``None`` when it has none to read."""
        request = uuid.uuid4().hex
        files: list[dict[str, Any]] = []
        async with self._socket() as socket:
            await socket.send(
                json.dumps({"type": "list_memory", "request_id": request, "agent_id": agent})
            )
            stop = time.monotonic() + _ANSWER_SECONDS
            while True:
                left = stop - time.monotonic()
                if left <= 0:
                    raise TimeoutError
                frame = await self._next(socket, left)
                if frame.get("request_id") != request:
                    continue
                if not frame.get("success"):
                    raise ValueError(f"letta could not list memory: {frame.get('error')}")
                if frame.get("memfs_enabled") is False:
                    return None
                files.extend(frame.get("entries") or [])
                if frame.get("done"):
                    return files

    async def cancel(self, session: Session) -> None:
        async with self._socket() as socket:
            await self._ask(socket, {"type": "abort_message", "runtime": self._scope(session)})

    async def close(self, session: Session) -> None:
        """Keeps what the run made, unless the target sets ``cleanup``: then the agents this
        session made go, and no one else's."""
        if not self.capabilities.deletes:
            return
        async with self._socket() as socket:
            for agent in session.extra.get("agents", []):
                answer = await self._ask(socket, {"type": "agent_delete", "agent_id": agent})
                if not answer.get("success"):
                    raise AppError(f"letta could not delete {agent}: {answer.get('error')}")


def create(target: TargetConfig) -> LettaTarget:
    return LettaTarget(target)


async def _check(
    client: httpx.AsyncClient, options: dict[str, Any], lookup: Callable[[Any], str | None]
) -> dict[str, Any]:
    key = lookup(options.get("api_key_env"))
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = f"{_http_url(str(options.get('url') or ''))}/app-server-info"
    answer = await client.get(url, headers=headers)
    if answer.status_code in (401, 403):
        return part(False, "The token was refused", refused=True)
    if not answer.is_success:
        return part(False, f"/app-server-info answered {answer.status_code}")
    info = answer.json()
    if info.get("protocol_version") != PROTOCOL_VERSION:
        return part(
            False, f"It speaks protocol {info.get('protocol_version')}, not {PROTOCOL_VERSION}"
        )
    return part(True, f"Reachable: Letta Code {info.get('letta_code_version')}")


def _validate(options: dict[str, Any]) -> None:
    tools = options.get("tools", [])
    if not isinstance(tools, list) or not all(isinstance(t, str) and t for t in tools):
        raise ValueError("options.tools: a list of tool names")
    text_options({k: v for k, v in options.items() if k != "tools"})


INFO = AdapterInfo(
    name="letta",
    label="Letta adapter",
    options=("url", "model", "tools", "cwd"),
    required=("url",),
    secrets=("api_key",),
    check=_check,
    refused="The Letta server refused the token.",
    validate=_validate,
)
