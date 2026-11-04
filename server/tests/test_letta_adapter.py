"""The letta adapter against a stub App Server on this machine: agents, conversations, turns, Stop
and memory files, all in memory, with no model and nothing outside."""

import asyncio
import json
import time
import uuid

import pytest
from websockets.asyncio.server import serve

from evalution.adapters.contract import run
from evalution.adapters.letta import FILE_TOOLS, INFO, LettaTarget
from evalution.config import TargetConfig
from evalution.core.evidence import TurnEnd


class Stub:
    """An App Server whose agents echo, commit "remember <x>" to a memory file, write "note <x>"
    without committing, wait on "slow" until aborted, ask for approval on "approve" and fail on
    "fail"."""

    def __init__(self) -> None:
        self.agents: dict[str, list[dict]] = {}
        self.conversations: dict[str, str] = {}
        self.inputs: list[dict] = []
        self.aborts: dict[str, asyncio.Event] = {}
        self.turns: list[asyncio.Future] = []

    async def __call__(self, socket) -> None:
        async for raw in socket:
            await self._frame(socket, json.loads(raw))

    async def _frame(self, socket, frame: dict) -> None:
        kind, request = frame["type"], frame.get("request_id")
        if kind == "runtime_start":
            await socket.send(json.dumps(self._start(frame)))
        elif kind == "input":
            self.inputs.append(frame)
            if frame["payload"]["kind"] == "approval_response":
                assert frame["payload"]["decision"]["behavior"] == "deny"
                body = {"type": "turn_finished", "runtime": frame["runtime"]}
                await socket.send(json.dumps({**body, "stop_reason": "end_turn"}))
                return
            await socket.send(
                json.dumps({"type": "input_accepted", "request_id": request, "accepted": True})
            )
            self.turns.append(asyncio.ensure_future(self._turn(socket, frame)))
        elif kind == "abort_message":
            event = self.aborts.get(frame["runtime"]["conversation_id"])
            if event:
                event.set()
            answer = {"type": "abort_message_response", "request_id": request, "success": True}
            await socket.send(json.dumps(answer))
        elif kind == "list_memory":
            entries = self.agents.get(frame["agent_id"], [])
            await socket.send(
                json.dumps(
                    {
                        "type": "list_memory_response",
                        "request_id": request,
                        "entries": entries,
                        "done": True,
                        "success": True,
                        "memfs_enabled": True,
                    }
                )
            )
        elif kind == "memory_file_at_ref":
            assert frame["ref"] == "HEAD"
            file = next(
                f
                for f in self.agents[frame["agent_id"]]
                if f["relative_path"] == frame["file_path"]
            )
            answer = {"type": "memory_file_at_ref_response", "request_id": request}
            if file["committed"]:
                answer |= {"success": True, "content": file["content"]}
            else:
                error = f"fatal: path '{frame['file_path']}' exists on disk, but not in 'HEAD'"
                answer |= {"success": False, "content": None, "error": error}
            await socket.send(json.dumps(answer))
        elif kind == "agent_delete":
            del self.agents[frame["agent_id"]]
            answer = {"type": "agent_delete_response", "request_id": request, "success": True}
            await socket.send(json.dumps(answer))

    def _start(self, frame: dict) -> dict:
        request = frame["request_id"]
        agent = frame.get("agent_id")
        if "create_agent" in frame:
            agent = f"agent-{uuid.uuid4().hex}"
            self.agents[agent] = []
        conversation = frame.get("conversation_id")
        if "create_conversation" in frame:
            conversation = f"conv-{uuid.uuid4().hex}"
            self.conversations[conversation] = agent
        if self.conversations.get(conversation) != agent:
            return {
                "type": "runtime_start_response",
                "request_id": request,
                "success": False,
                "runtime": None,
                "error": "no such conversation",
            }
        return {
            "type": "runtime_start_response",
            "request_id": request,
            "success": True,
            "runtime": {"agent_id": agent, "conversation_id": conversation},
        }

    async def _turn(self, socket, frame: dict) -> None:
        runtime = frame["runtime"]
        text = frame["payload"]["messages"][0]["content"]

        async def delta(content) -> None:
            body = {"message_type": "assistant_message", "content": content}
            await socket.send(
                json.dumps({"type": "stream_delta", "runtime": runtime, "delta": body})
            )

        async def finish(reason: str, **more) -> None:
            await socket.send(
                json.dumps(
                    {"type": "turn_finished", "runtime": runtime, "stop_reason": reason, **more}
                )
            )

        verb, _, fact = text.partition(" ")
        if verb in ("remember", "note"):
            files = self.agents[runtime["agent_id"]]
            path = f"file-{len(files)}.md"
            file = {"relative_path": path, "content": fact, "committed": verb == "remember"}
            files.append(file)
        if "slow" in text:
            event = self.aborts.setdefault(runtime["conversation_id"], asyncio.Event())
            await event.wait()
            self.aborts.pop(runtime["conversation_id"])
            await finish("cancelled")
        elif text == "approve":
            await socket.send(json.dumps({"type": "control_request", "runtime": runtime}))
        elif text == "unlisted":
            ask = {"type": "control_request", "request_id": "perm-1", "runtime": runtime}
            await socket.send(json.dumps({**ask, "request": {"tool_name": "Bash"}}))
        elif text == "fail":
            await finish("llm_api_error", error="the model is down")
        else:
            await delta("You said: ")
            await delta([{"type": "text", "text": text}])
            await finish("end_turn")


@pytest.fixture
async def app():
    stub = Stub()
    async with serve(stub, "127.0.0.1", 0, max_size=None) as server:
        port = server.sockets[0].getsockname()[1]
        options = {"url": f"http://127.0.0.1:{port}", "cleanup": True}
        yield stub, LettaTarget(TargetConfig("letta", "letta", None, None, options))


async def test_the_contract_kit_passes_on_a_stub_server(app, monkeypatch) -> None:
    monkeypatch.setattr("evalution.adapters.letta.STATE_WAIT_SECONDS", 0.1)
    _, target = app
    report = await run(target, INFO, settle=0.1, limit=20.0, short=0.2)
    assert report.ok, report.steps


async def test_memory_survives_a_break_but_not_a_reset(app, monkeypatch) -> None:
    monkeypatch.setattr("evalution.adapters.letta.STATE_WAIT_SECONDS", 0.1)
    stub, target = app
    session = await target.open("r")
    first_agent = session.extra["agent"]
    deadline = time.time() + 5

    turn = await target.send(session, "remember the code is 41", deadline=deadline)
    assert turn.reply == "You said: remember the code is 41" and not turn.error
    assert stub.inputs[-1]["payload"]["client_tool_allowlist"] == list(FILE_TOOLS)
    assert (await target.state(session, {"memory_contains": "code is 41"})).ok

    await target.send(session, "note the door is red", deadline=deadline)
    uncommitted = await target.state(session, {"memory_contains": "door is red"})
    assert not uncommitted.ok and "1 more written but not committed" in uncommitted.actual
    assert (await target.state(session, {"memory_lacks": "door is red"})).ok
    read = await target.read_memory(session)
    assert "code is 41" in read.committed and "door is red" not in read.committed
    assert "door is red" in read.written

    conversation = session.id
    await target.break_conversation(session)
    assert session.id != conversation and session.extra["agent"] == first_agent
    assert (await target.state(session, {"memory_contains": "code is 41"})).ok

    await target.reset(session)
    assert session.extra["agent"] != first_agent
    assert (await target.state(session, {"memory_lacks": "code is 41"})).ok

    await target.close(session)
    assert not stub.agents


async def test_how_a_turn_ends_is_told_apart(app) -> None:
    _, target = app
    session = await target.open("r")
    deadline = time.time() + 5

    stopped = await target.send(session, "slow", stop_after=0.05, deadline=deadline)
    assert not stopped.error and stopped.ended_as is TurnEnd.ANSWERED

    blocked = await target.send(session, "approve", deadline=deadline)
    assert blocked.ended_as is TurnEnd.BLOCKED and "approval" in blocked.error

    denied = await target.send(session, "unlisted", deadline=deadline)
    assert not denied.error and denied.ended_as is TurnEnd.ANSWERED

    failed = await target.send(session, "fail", deadline=deadline)
    assert failed.ended_as is TurnEnd.FAILED and "the model is down" in failed.error

    late = await target.send(session, "slow again", deadline=time.time() + 0.05)
    assert late.error == "the turn did not finish in time"
