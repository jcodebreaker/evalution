"""Traces, offline: evalution's own Phoenix with a fake process, where a target's traces are
read from, the tracing detector on tiny fake projects, the digest and drift on fake spans, and
the built-in source: OTLP in, retention, and both conventions giving the same view."""

import asyncio
import gzip
import json
import socket
import time
from dataclasses import replace

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from evalution.adapters import own_traffic
from evalution.api.app import create_app
from evalution.config import PhoenixConfig, load
from evalution.core.evidence import TraceStatus
from evalution.project import drift
from evalution.results.store import FileStore
from evalution.runs import collect as collect_module
from evalution.runs.collect import Wanted, collect, own_spans
from evalution.runs.runner import Runs
from evalution.targets import Targets
from evalution.traces import setup
from evalution.traces.base import Span, SpanRead, SpanReader, nest
from evalution.traces.builtin import Builtin, Received, SpanStore
from evalution.traces.managed import STATE_FILE, Supervisor, free_port, project, reader
from evalution.traces.otlp import MAX_BODY
from evalution.traces.phoenix import Phoenix, span_of
from evalution.traces.profile import conversations, digest, render
from evalution.traces.view import build, helper_of


class FakeProcess:
    def __init__(self) -> None:
        self.code: int | None = None
        self.stopped = False

    def poll(self) -> int | None:
        return self.code

    def terminate(self) -> None:
        self.stopped, self.code = True, 0

    def kill(self) -> None:
        self.code = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.code or 0


async def test_supervisor_starts_restarts_and_stops(tmp_path) -> None:
    spawned: list[tuple[list[str], dict[str, str], FakeProcess]] = []

    def spawn(command, env, log):
        spawned.append((command, env, FakeProcess()))
        return spawned[-1][2]

    async def healthy(url: str) -> bool:
        return True

    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        busy = taken.getsockname()[1]
        phoenix = Supervisor(tmp_path / "phoenix", busy, spawn=spawn, healthy=healthy, every=0.01)
        notes = await phoenix.start()
    assert phoenix.port != busy and f"port {busy} is taken" in notes[0]
    command, env, first = spawned[0]
    assert command[1:] == ["-m", "phoenix.server.main", "serve"]
    assert env["PHOENIX_WORKING_DIR"] == str(tmp_path / "phoenix")
    assert env["PHOENIX_PORT"] == str(phoenix.port) and env["PHOENIX_GRPC_PORT"] == "0"
    state = json.loads((tmp_path / "phoenix" / STATE_FILE).read_text())
    assert state["url"] == phoenix.url

    first.code = 1  # Phoenix died: the supervisor starts it again.
    for _ in range(200):
        if phoenix.restarts:
            break
        await asyncio.sleep(0.01)
    assert phoenix.restarts == 1 and len(spawned) == 2

    await phoenix.stop()
    assert spawned[1][2].stopped and not (tmp_path / "phoenix" / STATE_FILE).exists()


def test_free_port_skips_a_taken_one() -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        port = taken.getsockname()[1]
        assert free_port(port) not in (None, port)


async def test_where_a_targets_traces_are_read() -> None:
    own = PhoenixConfig("http://127.0.0.1:6006", "my-bot")
    assert project("my-bot") == "evalution-my-bot"

    managed = reader("my-bot", own, "http://127.0.0.1:6007")
    assert managed is not None
    sources = [(p.conf.url, p.conf.project) for p in [managed, *managed.also]]
    assert sources == [
        ("http://127.0.0.1:6007", "evalution-my-bot"),
        ("http://127.0.0.1:6007", "default"),
        ("http://127.0.0.1:6006", "my-bot"),
    ]
    assert managed.trace_url("t1") == "http://127.0.0.1:6006/redirects/traces/t1", "where it landed"
    on_its_own = reader("my-bot", None, "http://127.0.0.1:6007")
    assert on_its_own.trace_url("t1") == "http://127.0.0.1:6007/redirects/traces/t1"
    await on_its_own.close()
    alone = reader("my-bot", own, None)
    assert alone is not None and alone.conf.project == "my-bot" and not alone.also
    assert reader("my-bot", None, None) is None
    for made in (managed, alone):
        await made.close()


async def test_one_trace_source_gone_loses_only_its_own_spans() -> None:
    request = httpx.Request("GET", "http://x")

    class Gone(SpanReader):
        async def own_pages(self, start, end, trace_id=None):
            raise httpx.ConnectError("gone", request=request)
            yield []

    class Has(SpanReader):
        async def own_pages(self, start, end, trace_id=None):
            yield []

    gone, kept = Gone(), Gone()
    gone.also = [Has()]
    assert await gone.spans(0.0, 1.0) == []
    kept.also = [Gone()]
    with pytest.raises(httpx.ConnectError):
        await kept.spans(0.0, 1.0)


def test_the_detector_on_fake_projects() -> None:
    registered = setup.detect(
        {
            "pyproject.toml": 'dependencies = ["arize-phoenix-otel", "openai"]',
            "app.py": "from phoenix.otel import register\nregister(project_name='shop')\n",
        }
    )
    assert registered["register"] and registered["project"] == "shop"
    told = setup.advice(registered, "http://127.0.0.1:6006", "evalution-shop")
    assert told["traced"] and "PHOENIX_PROJECT_NAME=evalution-shop" in told["lines"]
    assert '"shop"' in told["text"]

    node = setup.detect(
        {
            "package.json": '{"dependencies": {"@arizeai/openinference-core": "1"}}',
            "index.ts": "registerInstrumentations({instrumentations: []})",
        }
    )
    assert node["language"] == "js" and node["otel"] and not node["register"]
    lines = setup.advice(node, "http://127.0.0.1:6006", "evalution-x")["lines"]
    assert "OTEL_RESOURCE_ATTRIBUTES=openinference.project.name=evalution-x" in lines

    bare = setup.detect({"requirements.txt": "anthropic\nfastapi\n", "main.py": "import anthropic"})
    assert not bare["otel"] and bare["framework"] == "anthropic"
    told = setup.advice(bare, "http://127.0.0.1:6006", "evalution-x")
    assert not told["traced"] and "openinference-instrumentation-anthropic" in told["lines"]

    # With the built-in source: the standard lines naming the target, and no Phoenix package.
    lines = setup.advice(node, "http://127.0.0.1:8080", "evalution-x", builtin=True)["lines"]
    assert "OTEL_RESOURCE_ATTRIBUTES=evalution.target=x" in lines
    told = setup.advice(bare, "http://127.0.0.1:8080", "evalution-x", builtin=True)
    assert 'OTLPSpanExporter(endpoint="http://127.0.0.1:8080/v1/traces")' in told["lines"]
    assert '"evalution.target": "x"' in told["lines"] and "phoenix" not in told["lines"]


def _span(sid, trace, start, end, kind="AGENT", parent=None, **attrs):
    return Span(sid, trace, parent, sid, kind, start, end, "OK", nest(attrs))


def test_the_digest_is_per_ask_and_leaves_out_eval_traffic() -> None:
    day = 86_400.0
    tools = json.dumps({"messages": [], "tools": [{"name": "mcp__shop__search", "parameters": {}}]})
    # One conversation that lives for three days: three asks of 30, 60 and 10 seconds.
    session = []
    for n, (at, seconds) in enumerate([(0.0, 30.0), (day, 60.0), (3 * day, 10.0)]):
        session.append(_span(f"r{n}", f"t{n}", at, at + seconds, **{"session.id": "c1"}))
        session.append(
            _span(
                f"m{n}",
                f"t{n}",
                at + 1,
                at + 2,
                "LLM",
                f"r{n}",
                **{
                    "input.value": tools,
                    "llm.token_count.prompt": 100,
                    "llm.cost.total": 0.01,
                },
            )
        )
        if n < 2:
            session.append(
                _span(
                    f"x{n}",
                    f"t{n}",
                    at + 3,
                    at + 4,
                    "TOOL",
                    f"r{n}",
                    **{
                        "tool.name": "mcp__shop__search",
                    },
                )
            )
    eval_try = [_span("e", "te", 5.0, 9.0, **{"session.id": "s9", "evalution.run": "r1"})]
    found = conversations(session + eval_try, "session.id")
    assert list(found) == ["c1"]

    made = digest(list(found.values()))
    assert made["sessions"] == 1 and made["asks"] == 3 and made["no_tool_asks"] == 1
    assert made["typical"]["seconds"]["median"] == 30.0
    assert made["typical"]["usd"]["median"] == 0.01
    # Offered and called tools are the same tool, without the MCP prefix.
    assert list(made["tools"]) == ["search"] and made["tools"]["search"]["offered"]
    assert "1 of 3 asks used no tools" in render(made)
    assert [a["tools"] for a in made["per_ask"]] == [["search"], ["search"], []]


async def test_drift_needs_two_checks_in_a_row(monkeypatch, tmp_path) -> None:
    class Folder:
        def folder(self, name):
            return tmp_path

    (tmp_path / "fingerprint.json").write_text(
        json.dumps(
            {
                "sessions": 5,
                "tools_offered_known": True,
                "tools": {"search": ["q"]},
                "prompt": {},
            }
        )
    )

    new = {
        "sessions": 5,
        "tools_offered_known": True,
        "tools": {"search": ["q", "page"]},
        "prompt": {},
    }

    async def take(targets, name, sessions):
        return {}, new

    monkeypatch.setattr(drift, "take", take)
    folder = Folder()
    first = await drift.check(folder, "bot")
    assert first["changes"] == [] and len(first["pending"]) == 1
    second = await drift.check(folder, "bot")
    assert [c["kind"] for c in second["changes"]] == ["args_changed"]


TRACE = "ab" * 16
ROOT, AGENT, MODEL, TOOL, HEALTH = (f"{n:016x}" for n in range(1, 6))
ASKED = [{"role": "user", "content": "find shoes"}]
OFFERED = [{"type": "function", "name": "search", "parameters": {"properties": {"q": {}}}}]
#: One ask, in each convention: ``(span id, parent, name, seconds in, seconds long, attributes)``.
OPENINFERENCE = [
    (
        AGENT,
        ROOT,
        "agent",
        1,
        8,
        {"openinference.span.kind": "AGENT", "session.id": "c1"},
    ),
    (
        MODEL,
        AGENT,
        "call",
        2,
        2,
        {
            "openinference.span.kind": "LLM",
            "llm.model_name": "some-model",
            "llm.token_count.prompt": 100,
            "llm.token_count.completion": 20,
            "llm.input_messages.0.message.role": "system",
            "llm.input_messages.0.message.content": "Be brief",
            "llm.input_messages.1.message.role": "user",
            "llm.input_messages.1.message.content": "find shoes",
            "llm.output_messages.0.message.role": "assistant",
            "llm.output_messages.0.message.content": "Searching",
            "llm.tools.0.tool.json_schema": json.dumps(OFFERED[0]),
        },
    ),
    (
        TOOL,
        AGENT,
        "search",
        5,
        1,
        {
            "openinference.span.kind": "TOOL",
            "tool.name": "search",
            "tool.parameters": json.dumps({"q": "shoes"}),
            "output.value": "two pairs",
        },
    ),
]
GENAI = [
    (
        AGENT,
        ROOT,
        "agent",
        1,
        8,
        {"gen_ai.operation.name": "invoke_agent", "gen_ai.conversation.id": "c1"},
    ),
    (
        MODEL,
        AGENT,
        "call",
        2,
        2,
        {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "some-model",
            "gen_ai.usage.input_tokens": 100,
            "gen_ai.usage.output_tokens": 20,
            "gen_ai.system_instructions": json.dumps([{"type": "text", "content": "Be brief"}]),
            "gen_ai.input.messages": json.dumps(
                [{"role": "user", "parts": [{"type": "text", "content": "find shoes"}]}]
            ),
            "gen_ai.output.messages": json.dumps(
                [{"role": "assistant", "parts": [{"type": "text", "content": "Searching"}]}]
            ),
            "gen_ai.tool.definitions": json.dumps(OFFERED),
        },
    ),
    (
        TOOL,
        AGENT,
        "search",
        5,
        1,
        {
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": "search",
            "gen_ai.tool.call.arguments": json.dumps({"q": "shoes"}),
            "gen_ai.tool.call.result": "two pairs",
        },
    ),
]
#: What frameworks send around them: never in the view, and the root links nothing.
PLAIN = [
    (ROOT, None, "POST /chat", 0, 10, {"http.route": "/chat"}),
    (HEALTH, None, "GET /health", 3, 1, {"http.route": "/health", "gen_ai.conversation.id": "c1"}),
]


def _nanos(seconds: float) -> str:
    return str(int((1_000 + seconds) * 1e9))


def _otlp_json(spans, resource=None) -> dict:
    def value(v):
        if isinstance(v, int):
            return {"intValue": str(v)}
        return {"stringValue": v}

    def attrs(mapping):
        return [{"key": k, "value": value(v)} for k, v in mapping.items()]

    rows = [
        {
            "traceId": TRACE,
            "spanId": sid,
            "parentSpanId": parent or "",
            "name": name,
            "startTimeUnixNano": _nanos(at),
            "endTimeUnixNano": _nanos(at + long),
            "status": {"code": 1},
            "attributes": attrs(mapping),
        }
        for sid, parent, name, at, long, mapping in spans
    ]
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": attrs(resource or {})},
                "scopeSpans": [{"spans": rows}],
            }
        ]
    }


def _otlp_proto(spans, resource=None) -> ExportTraceServiceRequest:
    request = ExportTraceServiceRequest()
    block = request.resource_spans.add()
    for key, text in (resource or {}).items():
        block.resource.attributes.add(key=key).value.string_value = text
    scope = block.scope_spans.add()
    for sid, parent, name, at, long, mapping in spans:
        span = scope.spans.add(
            trace_id=bytes.fromhex(TRACE),
            span_id=bytes.fromhex(sid),
            parent_span_id=bytes.fromhex(parent or ""),
            name=name,
            start_time_unix_nano=int(_nanos(at)),
            end_time_unix_nano=int(_nanos(at + long)),
        )
        span.status.code = 1
        for key, found in mapping.items():
            field = span.attributes.add(key=key).value
            if isinstance(found, int):
                field.int_value = found
            else:
                field.string_value = found
    return request


def _phoenix_row(sid, parent, name, at, long, mapping) -> dict:
    return {
        "context": {"span_id": sid, "trace_id": TRACE},
        "parent_id": parent,
        "name": name,
        "span_kind": mapping.get("openinference.span.kind", "UNKNOWN"),
        "start_time": 1_000.0 + at,
        "end_time": 1_000.0 + at + long,
        "status_code": "OK",
        "attributes": mapping,
    }


async def _built_in_spans(store: SpanStore, target: str = "default") -> list[Span]:
    return await Builtin(store, target).spans(0, 1e10)


def _client(tmp_path) -> TestClient:
    file = tmp_path / "evalution.yaml"
    file.write_text(yaml.safe_dump({"targets": {}, "traces": {"managed": "builtin"}}))
    return TestClient(create_app(load(file)))


async def test_both_conventions_give_the_same_view_over_plain_spans(tmp_path) -> None:
    def price(model, tokens_in, tokens_out):
        return 0.5 if model == "some-model" else 0.0

    from_phoenix = [span_of(_phoenix_row(*s)) for s in [*PLAIN, *OPENINFERENCE]]
    with _client(tmp_path) as client:
        sent = client.post("/v1/traces", json=_otlp_json([*PLAIN, *GENAI]))
    assert sent.status_code == 200 and sent.json() == {}
    store = SpanStore(tmp_path / "data" / "traces" / "spans.db")
    from_otlp = await _built_in_spans(store)
    assert {s.kind for s in from_otlp if s.id in (ROOT, HEALTH)} == {"UNKNOWN"}

    one = build(from_phoenix, 1_000.0, price=price)
    other = build(from_otlp, 1_000.0, price=price)
    assert other == one
    assert [row["id"] for row in one.timeline] == [AGENT, MODEL, TOOL]
    assert one.timeline[0]["parent"] is None  # its parent is a plain span, so it starts the tree
    assert one.tokens == 120 and one.usd == 0.5
    assert one.calls[0].name == "search" and one.calls[0].args == {"q": "shoes"}
    assert [step["kind"] for step in one.trajectory] == ["said", "called", "result"]
    assert one.context[0]["system"] == "Be brief"

    # The older token names read the same, and an all-plain read is no view at all.
    older = _span("m", "t", 1.0, 2.0, "LLM")
    older.attrs = nest({"gen_ai.usage.prompt_tokens": 7, "gen_ai.usage.completion_tokens": 3})
    assert build([older], 0.0).tokens == 10
    assert build([s for s in from_otlp if s.id == ROOT], 0.0).timeline == []


async def test_plain_spans_do_not_count_against_a_read() -> None:
    flood = [_phoenix_row(f"f{n:015x}", None, "GET /health", n, 1, {}) for n in range(1_000)]
    ai = _phoenix_row(MODEL, None, "call", 2, 1, {"gen_ai.operation.name": "chat"})

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor"):
            return httpx.Response(200, json={"data": [ai]})
        return httpx.Response(200, json={"data": flood, "next_cursor": "next"})

    phoenix = Phoenix(PhoenixConfig("http://phoenix"), transport=httpx.MockTransport(handler))
    found = await phoenix.spans(0, 10_000, max_spans=1)
    assert [s.id for s in found if s.kind == "LLM"] == [MODEL]
    await phoenix.close()


async def test_otlp_over_protobuf_and_json_with_gzip_and_limits(tmp_path) -> None:
    named = {"evalution.target": "shop"}
    binary = {"Content-Type": "application/x-protobuf"}
    with _client(tmp_path) as client:
        body = _otlp_proto(GENAI, named).SerializeToString()
        taken = client.post(
            "/v1/traces",
            content=gzip.compress(body),
            headers={**binary, "Content-Encoding": "gzip"},
        )
        assert taken.status_code == 200 and taken.headers["content-type"] == binary["Content-Type"]
        assert not ExportTraceServiceResponse.FromString(taken.content).HasField("partial_success")

        # The same spans again as JSON: an exporter's retry replaces them, under another target.
        doc = _otlp_json([*GENAI, PLAIN[0]], {"openinference.project.name": "evalution-bar"})
        doc["resourceSpans"][0]["scopeSpans"][0]["spans"][-1]["traceId"] = "not-hex"
        partly = client.post("/v1/traces", json=doc)
        assert partly.status_code == 200
        assert partly.json()["partialSuccess"]["rejectedSpans"] == "1"

        refused = {
            "not a type we take": client.post(
                "/v1/traces", content=b"{}", headers={"Content-Type": "text/plain"}
            ),
            "not protobuf": client.post("/v1/traces", content=b"\xff\xff\xff", headers=binary),
            "not gzip": client.post(
                "/v1/traces", content=b"plain", headers={**binary, "Content-Encoding": "gzip"}
            ),
            "too big unzipped": client.post(
                "/v1/traces",
                content=gzip.compress(b"\0" * (MAX_BODY + 1)),
                headers={**binary, "Content-Encoding": "gzip"},
            ),
            "too big": client.post("/v1/traces", content=b"\0" * (MAX_BODY + 1), headers=binary),
        }
        assert [r.status_code for r in refused.values()] == [415, 400, 400, 413, 413]

    store = SpanStore(tmp_path / "data" / "traces" / "spans.db")
    assert await _built_in_spans(store, "shop") == []
    spans = await _built_in_spans(store, "bar")
    assert sorted(s.id for s in spans) == sorted([AGENT, MODEL, TOOL])


def test_the_intake_is_off_without_the_built_in_source(tmp_path) -> None:
    file = tmp_path / "evalution.yaml"
    file.write_text(yaml.safe_dump({"targets": {}, "traces": {"managed": False}}))
    with TestClient(create_app(load(file))) as client:
        assert client.post("/v1/traces", json={}).status_code == 404


async def test_retention_drops_by_age_then_size_keeping_ai_longest(tmp_path) -> None:
    store = SpanStore(tmp_path / "traces" / "spans.db", keep_mb=1, keep_days=1)
    now = time.time()

    def spans(prefix: str, count: int, attrs: dict) -> list[Received]:
        return [
            Received("t", TRACE, f"{prefix}{n:015x}", None, "x", 1.0, 2.0, "OK", attrs)
            for n in range(count)
        ]

    filler = "x" * 1_000
    store.add(spans("a", 1, {"gen_ai.operation.name": "chat"}), now=now - 2 * 86_400)
    store.add(spans("b", 1, {"gen_ai.operation.name": "chat"}), now=now)
    assert [s.id[0] for s in await _built_in_spans(store, "t")] == ["b"]

    store.add(spans("c", 20, {"gen_ai.operation.name": "chat", "note": "keep"}), now=now)
    store.add(spans("d", 2_000, {"http.route": "/health", "note": filler}), now=now)
    left = await _built_in_spans(store, "t")
    assert store.path.stat().st_size < 2 * store.keep_bytes
    assert any(s.id[0] == "d" for s in left)
    assert sum(s.id[0] == "c" for s in left) == 20  # plain spans went first
    assert len([s for s in left if s.id[0] == "d"]) < 2_000


def test_an_agent_wrapped_on_one_session_is_not_a_helper() -> None:
    def span(id_, kind, parent=None, session="s1"):
        return Span(
            id=id_,
            trace_id="t",
            parent=parent,
            name=id_,
            kind=kind,
            start=0.0,
            end=1.0,
            status="OK",
            attrs={"session": {"id": session}},
        )

    wrapped = [
        span("round", "AGENT"),
        span("agent", "AGENT", "round"),
        span("tool", "TOOL", "agent"),
    ]
    assert helper_of(wrapped[2], {s.id: s for s in wrapped}) is None

    child = [
        span("top", "AGENT"),
        span("kid", "AGENT", "top", "s2"),
        span("tool", "TOOL", "kid", "s2"),
    ]
    assert helper_of(child[2], {s.id: s for s in child}) == "kid"


async def test_a_quiet_read_spaces_out_and_still_settles(monkeypatch) -> None:
    """Each read costs the whole window: an app sending nothing isn't asked every 2 s for 20 s."""
    monkeypatch.setattr(collect_module, "_POLL", 0.01)
    monkeypatch.setattr(collect_module, "TRACE_WAIT", 0.16)
    reads = 0

    class Counts:
        async def window(self, start, end):
            nonlocal reads
            reads += 1
            return SpanRead([])

    wanted = Wanted("session.id", ["s1"], (), (), time.time(), time.time() + 1, [])
    found = await collect(Counts(), wanted, settle=0.08)
    assert found.status is TraceStatus.EMPTY
    assert 2 <= reads <= 6, "at a steady 0.01 s it would be 16"
    # Control: spans there settle in about the same time as before, not after the cap.
    t0 = time.time()
    spans = [_span("a", "t", t0, t0 + 1, "LLM", **{"session.id": "s1"})]
    began = time.time()
    found = await collect(_Spans(spans), replace(wanted, t0=t0, end=t0 + 1), settle=0.05)
    assert found.status is TraceStatus.COMPLETE and time.time() - began < 0.16


class _Spans(SpanReader):
    """Whatever spans it holds, read the way any source's are."""

    def __init__(self, spans) -> None:
        self.held = spans

    async def own_pages(self, start, end, trace_id):
        yield [s for s in self.held if start <= s.start <= end]

    def trace_url(self, trace_id):
        return None


async def test_a_try_is_its_ids_and_its_delegated_work_not_its_time_window() -> None:
    """Catches a read by time window: all of these started while the try was going."""
    t0 = time.time()
    mine = {"session.id": "s1"}
    spans = [
        _span("root", "t1", t0, t0 + 9, "AGENT", **mine),
        _span("ask", "t1", t0 + 1, t0 + 2, "LLM", "root", **mine),
        _span("start", "t1", t0 + 2, t0 + 8, "TOOL", "root", **mine),
        # Handed off in a new trace, a new session: linked by the parent id alone...
        _span("kid", "t2", t0 + 3, t0 + 7, "AGENT", "start", **{"session.id": "child"}),
        _span("kid_tool", "t2", t0 + 3, t0 + 4, "TOOL", "kid", **{"session.id": "child"}),
        # ...then the same child session's next trace, with no link of its own.
        _span("kid_again", "t3", t0 + 5, t0 + 6, "TOOL", **{"session.id": "child"}),
        # A child session that only names its parent session.
        _span("named", "t4", t0 + 4, t0 + 5, "TOOL", **{"session.parent_id": "s1"}),
        # The control: another session in the same window.
        _span("other", "t5", t0 + 1, t0 + 8, "AGENT", **{"session.id": "someone"}),
        _span("other_tool", "t5", t0 + 2, t0 + 3, "TOOL", "other", **{"session.id": "someone"}),
    ]
    wanted = Wanted("session.id", ["s1"], (), (), t0, t0 + 9, [(t0, t0 + 9)])
    found = await collect(_Spans(spans), wanted, settle=0.0)
    assert found.status is TraceStatus.COMPLETE
    ids = {s.id for s in found.spans}
    assert {"kid", "kid_tool", "kid_again", "named"} <= ids
    assert not {"other", "other_tool"} & ids, "another session in the window is not this try"

    view = build(found.spans, t0)
    helpers = {c.name: c.helper for c in view.calls}
    assert helpers["start"] is None, "the app's own call is not helper work"
    assert helpers["kid_tool"] == "kid" and helpers["named"] and helpers["kid_again"]
    assert next(r for r in view.timeline if r["id"] == "kid_tool")["helper"] is True


async def test_marks_on_spans_say_whose_they_are_before_any_session_or_clock() -> None:
    t0 = time.time()
    shared = {"session.id": "kept"}

    def marked(n: int) -> dict:
        return {**shared, "evalution.run": "r1", "evalution.try": n}

    spans = [
        _span("before", "t1", t0 - 3, t0 - 2, "TOOL", **marked(1)),
        _span("this", "t2", t0 - 1, t0 + 1, "TOOL", **marked(2)),
        _span("next", "t3", t0 + 1, t0 + 2, "TOOL", **marked(3)),
        _span("other", "t4", t0 + 1, t0 + 2, "TOOL", **{"evalution.run": "r2", "evalution.try": 2}),
    ]
    # The session is one an earlier try played on, so the clock would drop "this" and keep "next".
    wanted = Wanted("session.id", ["kept"], {"kept"}, (), t0, t0 + 2, (), ("r1", "2"))
    found = await collect(_Spans(spans), wanted, settle=0.0, once=True)
    assert {s.id for s in found.spans} == {"this"}
    # The control: with no marks to go by the session and the clock are all there is.
    bare = Wanted("session.id", ["kept"], {"kept"}, (), t0, t0 + 2, ())
    found = await collect(_Spans(spans), bare, settle=0.0, once=True)
    assert {s.id for s in found.spans} == {"next"}


async def test_a_mirror_is_one_client_closed_with_its_target(tmp_path, monkeypatch) -> None:
    mirrored = {"adapter": "http", "http": {"send": {"url": "x"}}}
    mirrored["phoenix"] = {"url": "http://127.0.0.1:1", "mirror": True}
    plain = {"adapter": "http", "http": {"send": {"url": "x"}}}
    file = tmp_path / "evalution.yaml"
    file.write_text(yaml.safe_dump({"targets": {"bot": mirrored, "plain": plain}}))
    config = load(file)
    targets = Targets(config)
    assert targets.sink("plain") is None
    sink = targets.sink("bot")
    assert targets.sink("bot") is sink

    saved = []

    async def save(run) -> None:
        saved.append(run["status"])

    monkeypatch.setattr(sink, "save", save)
    runs = Runs(targets, FileStore(config.data_dir), None)
    run = {"run": "r1", "target": "bot", "set": "main", "started_at": 1.0, "cases": {}}
    await runs._save({**run, "status": "running"})
    await runs._save({**run, "status": "done"})
    await runs._save({**run, "status": "done"})
    assert saved == ["done", "done"] and targets.sink("bot") is sink

    await targets.reload("bot")
    assert sink._client.is_closed
    fresh = targets.sink("bot")
    assert fresh is not sink and not fresh._client.is_closed
    await targets.close()
    assert fresh._client.is_closed


def test_eval_traffic_keeps_its_marks() -> None:
    # Apps send these to mark eval traffic: renaming one would count evals as real use.
    assert {"evalution.run"} <= set(own_traffic().attributes)


def test_a_sub_agent_known_only_by_the_id_a_tool_returned_is_followed() -> None:
    def span(id_, session, kind="LLM", output=None, trace=None):
        attrs = {"session": {"id": session}, "output": {"value": output or "ok"}}
        return Span(id_, trace or id_, None, id_, kind, 10.0, 11.0, "OK", attrs)

    window = [
        span("start", "main-session", "TOOL", '{"started": true, "helper_id": "child-session-1"}'),
        span("work", "child-session-1", "TOOL", "wrote it"),
        span("later", "child-session-1", "LLM", "done"),
        span("stranger", "someone-elses-session", "TOOL", "not linked"),
    ]
    wanted = Wanted("session.id", ["main-session"], set(), (), 0.0, 20.0, ())
    assert {s.id for s in own_spans(window, wanted, follow=True)} == {"start", "work", "later"}
    assert {s.id for s in own_spans(window, wanted)} == {"start"}, "control: only when following"
    calls = {c.name: c.helper for c in build(own_spans(window, wanted, follow=True), 0.0).calls}
    assert calls["start"] is None and calls["work"], "the sub-agent's call is marked as its own"
    # A sub-agent's result naming the session that started it doesn't make that one a sub-agent.
    earlier = span("start", "main-session", "TOOL", '{"helper_id": "child-session-1"}')
    later = span("work", "child-session-1", "TOOL", '{"parent_session_id": "main-session"}')
    later = replace(later, start=12.0, end=13.0)
    named_back = {c.name: c.helper for c in build([earlier, later], 0.0).calls}
    assert named_back["start"] is None and named_back["work"]
    # A short value could be in a result by chance, so it says nothing.
    short = [span("start", "main-session", "TOOL", "abc"), span("work", "abc", "TOOL", "x")]
    assert {s.id for s in own_spans(short, wanted, follow=True)} == {"start"}
