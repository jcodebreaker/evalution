"""Model calls stream (next-level C8): a gateway that cuts a silent request keeps it open while
bytes flow, and a 504 or a silence asks for a smaller answer instead of the same call again."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

import httpx
import pytest

from evalution import llm
from evalution.config import ConfigError, ModelConfig, _model
from evalution.config import KeyRing as _KeyRing


def _config(kind: str = "anthropic", **more: Any) -> ModelConfig:
    return ModelConfig(
        kind=kind, base_url="http://model", api_key_env=None, models={"strong": "m"}, **more
    )


def _sse(events: Iterable[dict[str, Any] | str], named: bool = True) -> bytes:
    out = []
    for event in events:
        if isinstance(event, str):
            out.append(f"data: {event}\n\n")
        elif named:
            out.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
        else:
            out.append(f"data: {json.dumps(event)}\n\n")
    return "".join(out).encode()


class _Stream(httpx.AsyncByteStream):
    """Bytes sent in chunks, then, when *then* is given, that raised instead of an end."""

    def __init__(self, chunks: list[bytes], then: Exception | None = None) -> None:
        self.chunks, self.then = chunks, then

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
        if self.then is not None:
            raise self.then


def _streamed(body: bytes, then: Exception | None = None) -> httpx.Response:
    half = len(body) // 2
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=_Stream([body[:half], body[half:]], then),
    )


ANTHROPIC_EVENTS = [
    {
        "type": "message_start",
        "message": {
            "model": "m-1",
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 4},
        },
    },
    {"type": "ping"},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "lo"}},
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "tool_use", "id": "t1", "name": "look", "input": {}},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '{"q": '},
    },
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "input_json_delta", "partial_json": '"x"}'},
    },
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}},
    {"type": "message_stop"},
]


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    async def pause(_seconds: float) -> None:
        return None

    monkeypatch.setattr(llm, "_pause", pause)


async def test_an_anthropic_call_streams_and_reads_as_before() -> None:
    seen: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _streamed(_sse(ANTHROPIC_EVENTS))

    model = llm.LLM(_config(), transport=httpx.MockTransport(answer))
    reply = await model.chat("strong", "sys", [{"role": "user", "content": "hi"}])
    assert seen[0]["stream"] is True
    assert reply.text == "Hello"
    assert reply.tool_calls == [{"id": "t1", "name": "look", "arguments": {"q": "x"}}]
    assert (reply.tokens_in, reply.tokens_out, reply.cache_read) == (14, 7, 4)
    assert reply.model == "m-1" and not reply.cut


async def test_an_anthropic_stream_cut_at_the_limit_says_so() -> None:
    events = [
        ANTHROPIC_EVENTS[0],
        *ANTHROPIC_EVENTS[6:8],
        {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {}},
        {"type": "message_stop"},
    ]
    model = llm.LLM(_config(), transport=httpx.MockTransport(lambda r: _streamed(_sse(events))))
    reply = await model.chat("strong", "sys", [{"role": "user", "content": "hi"}])
    assert reply.cut
    assert reply.tool_calls[0]["arguments"] == {"_unreadable": '{"q": '}


OPENAI_EVENTS = [
    {"model": "o-1", "choices": [{"index": 0, "delta": {"role": "assistant", "content": "He"}}]},
    {"choices": [{"index": 0, "delta": {"content": "llo"}}]},
    {
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": "c1", "function": {"name": "look", "arguments": ""}}
                    ]
                },
            }
        ]
    },
    {
        "choices": [
            {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"q"'}}]}}
        ]
    },
    {
        "choices": [
            {"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]}}
        ]
    },
    {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    {
        "choices": [],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 8},
        },
    },
    "[DONE]",
]


async def test_an_openai_compatible_call_streams_and_reads_as_before() -> None:
    seen: list[dict[str, Any]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _streamed(_sse(OPENAI_EVENTS, named=False))

    model = llm.LLM(_config("openai"), transport=httpx.MockTransport(answer))
    reply = await model.chat("strong", "sys", [{"role": "user", "content": "hi"}])
    assert seen[0]["stream"] is True and seen[0]["stream_options"] == {"include_usage": True}
    assert reply.text == "Hello"
    assert reply.tool_calls == [{"id": "c1", "name": "look", "arguments": {"q": 1}}]
    assert (reply.tokens_in, reply.tokens_out, reply.cache_read) == (20, 5, 8)
    assert reply.model == "o-1" and not reply.cut


async def test_a_provider_that_answers_whole_is_still_read() -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        body = {"content": [{"type": "text", "text": "ok"}], "usage": {"output_tokens": 1}}
        return httpx.Response(200, json=body)

    model = llm.LLM(_config(), transport=httpx.MockTransport(answer))
    assert (await model.chat("strong", "s", [{"role": "user", "content": "x"}])).text == "ok"


def _gateway(fail: int, how: str) -> tuple[httpx.MockTransport, list[int]]:
    """A gateway that cuts the first *fail* calls (*how*: ``504``, ``silent`` (bytes, then a
    read timeout) or ``closed`` (bytes, then the end with no message_stop)), then answers."""
    asked: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content)["max_tokens"])
        if len(asked) <= fail:
            if how == "504":
                return httpx.Response(504, text="Gateway Timeout")
            then = httpx.ReadTimeout("silent") if how == "silent" else None
            return _streamed(_sse(ANTHROPIC_EVENTS[:3]), then)
        return _streamed(_sse(ANTHROPIC_EVENTS))

    return httpx.MockTransport(answer), asked


@pytest.mark.parametrize("how", ["504", "silent", "closed"])
async def test_a_cut_call_asks_for_a_smaller_answer_not_the_same_one(how: str) -> None:
    transport, asked = _gateway(1, how)
    model = llm.LLM(_config(), transport=transport)
    reply = await model.chat("strong", "s", [{"role": "user", "content": "x"}], max_tokens=4096)
    assert reply.text == "Hello"
    assert asked == [4096, 2048]


@pytest.mark.parametrize(
    ("how", "said"),
    [
        ("504", "the model's gateway timed out (504)"),
        ("silent", "the model said nothing for"),
        ("closed", "the model's answer stopped before its end"),
    ],
)
async def test_a_gateway_that_always_cuts_is_named(how: str, said: str) -> None:
    transport, asked = _gateway(99, how)
    model = llm.LLM(_config(), transport=transport)
    with pytest.raises(llm.LLMError) as caught:
        await model.chat("strong", "s", [{"role": "user", "content": "x"}], max_tokens=4096)
    assert said in str(caught.value) and caught.value.transient
    assert asked == [4096, 2048, 1024]


async def test_a_busy_provider_is_asked_the_same_again() -> None:
    asked: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content)["max_tokens"])
        if len(asked) == 1:
            return httpx.Response(529, text="Overloaded")
        return _streamed(_sse(ANTHROPIC_EVENTS))

    model = llm.LLM(_config(), transport=httpx.MockTransport(answer))
    await model.chat("strong", "s", [{"role": "user", "content": "x"}], max_tokens=4096)
    assert asked == [4096, 4096]


async def test_an_error_inside_the_stream_is_tried_again() -> None:
    asked: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(1)
        if len(asked) == 1:
            error = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
            return _streamed(_sse([ANTHROPIC_EVENTS[0], error]))
        return _streamed(_sse(ANTHROPIC_EVENTS))

    model = llm.LLM(_config(), transport=httpx.MockTransport(answer))
    assert (await model.chat("strong", "s", [{"role": "user", "content": "x"}])).text == "Hello"
    assert len(asked) == 2


async def test_the_timeout_is_a_setting() -> None:
    seen: list[Any] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return _streamed(_sse(ANTHROPIC_EVENTS))

    model = llm.LLM(_config(timeout_seconds=42.0), transport=httpx.MockTransport(answer))
    await model.chat("strong", "s", [{"role": "user", "content": "x"}])
    assert seen[0]["read"] == 42.0
    model.use(_config())
    await model.chat("strong", "s", [{"role": "user", "content": "x"}])
    assert seen[1]["read"] == llm.TIMEOUT


def test_the_timeout_setting_is_read_from_the_config() -> None:
    block = {"kind": "anthropic", "models": {"strong": "a", "light": "b"}, "timeout_seconds": 90}
    assert _model(block, _KeyRing()).timeout_seconds == 90.0
    assert _model({**block, "timeout_seconds": None}, _KeyRing()).timeout_seconds is None
    for bad in (0, -1, "soon", True, 100_000):
        with pytest.raises(ConfigError, match=r"model\.timeout_seconds"):
            _model({**block, "timeout_seconds": bad}, _KeyRing())
