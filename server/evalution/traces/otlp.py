"""Taking spans in (docs/architecture.md, "Traces"): ``POST /v1/traces``, standard OTLP over HTTP,
``application/x-protobuf`` or ``application/json``, optionally gzipped, into the built-in source.
Hosted, the request carries a workspace token (a traces token is made for this); locally, none.

This is a network input, so nothing is trusted: the body is capped before and after gunzip, the
content type must be one of the two (a browser can't send either across sites without asking),
and ids, times and attributes are checked or cut, a bad span being counted as rejected rather than
failing the rest. Answers follow the OTLP spec: the response in the request's encoding,
``partial_success`` for rejected spans, a ``Status`` body on an error, 503 for what a retry may
fix. No span content is ever logged or echoed.

The target comes from the resource attribute ``evalution.target``, else
``openinference.project.name`` (which :func:`phoenix.otel.register` sets, with or without the
``evalution-`` prefix); else ``default``, which every target reads, finding its own by session
and trace id.

A target with a ``phoenix`` block also gets every span it is sent forwarded to its Phoenix
(:class:`Forward`): the same spans as OTLP protobuf, under the target's project, in the
background, so the intake is never slowed or failed by it.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import re
import time
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any
from weakref import WeakKeyDictionary

import httpx
from fastapi import Request, Response
from google.protobuf.json_format import ParseDict, ParseError
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from sqlalchemy.exc import SQLAlchemyError

from evalution.config import PhoenixConfig
from evalution.core import net
from evalution.traces.base import with_events
from evalution.traces.builtin import (
    DEFAULT_TARGET,
    PROJECT_ATTRIBUTE,
    TARGET_ATTRIBUTE,
    TARGET_PREFIX,
    Received,
    SpanStore,
)

_logger = logging.getLogger(__name__)

PROTOBUF = "application/x-protobuf"
JSON = "application/json"
#: Bytes of a request, on the wire and after gunzip.
MAX_BODY = 32 * 1024 * 1024
MAX_ATTRIBUTES = 4_096
#: Short enough that a dotted key can't nest absurdly deep when it is read.
MAX_KEY_CHARS = 256
#: Long enough to keep a whole prompt.
MAX_VALUE_CHARS = 2_000_000
MAX_NAME_CHARS = 1_024
MAX_TARGET_CHARS = 128
#: How deep an attribute's value may nest; anything deeper is left out.
MAX_DEPTH = 16
#: Times past this (the year 2262) are not times.
MAX_NANOS = 2**63
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_SPAN_ID = re.compile(r"[0-9a-f]{16}")
_STATUS = {0: "UNSET", 1: "OK", 2: "ERROR"}
_STATUS_NAMES = {
    "STATUS_CODE_UNSET": "UNSET",
    "STATUS_CODE_OK": "OK",
    "STATUS_CODE_ERROR": "ERROR",
}
#: ``google.rpc.Code`` for what is wrong with a request, and for a store that failed.
_INVALID, _UNAVAILABLE = 3, 14


class Refused(Exception):
    """The request can't be taken. *message* is fixed text: it never holds what was sent."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def _gunzip(body: bytes) -> bytes:
    """*body* unzipped, at most :data:`MAX_BODY` bytes of it.

    :raises Refused: Not gzip, cut short, or bigger than that once unzipped.
    """
    inflate = zlib.decompressobj(wbits=31)
    try:
        out = inflate.decompress(body, MAX_BODY + 1)
    except zlib.error as exc:
        raise Refused(400, "not valid gzip") from exc
    if len(out) > MAX_BODY:
        raise Refused(413, "too large")
    if not inflate.eof:
        raise Refused(400, "not valid gzip")
    return out


def _text(value: str, limit: int) -> str:
    """*value* cut to *limit*, without the lone surrogates JSON can carry and UTF-8 can't."""
    return value[:limit].encode("utf-8", "replace").decode("utf-8")


def _clean(key: Any, value: Any) -> tuple[str, Any] | None:
    """An attribute as kept: a value with structure as JSON text, which is how the conventions
    say to record it on a span and keeps its keys as sent."""
    if not isinstance(key, str) or not key or len(key) > MAX_KEY_CHARS or value is None:
        return None
    if isinstance(value, dict) or (
        isinstance(value, list) and any(isinstance(v, (dict, list)) for v in value)
    ):
        value = json.dumps(value, ensure_ascii=False)
    return _text(key, MAX_KEY_CHARS), (
        _text(value, MAX_VALUE_CHARS) if isinstance(value, str) else value
    )


def _attributes(pairs: Iterable[tuple[Any, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if len(out) >= MAX_ATTRIBUTES:
            break
        if kept := _clean(key, value):
            out[kept[0]] = kept[1]
    return out


def _target(resource: Mapping[str, Any]) -> str:
    for key in (TARGET_ATTRIBUTE, PROJECT_ATTRIBUTE):
        named = resource.get(key)
        if isinstance(named, str) and named.strip():
            return named.strip().removeprefix(TARGET_PREFIX)[:MAX_TARGET_CHARS] or DEFAULT_TARGET
    return DEFAULT_TARGET


def _received(
    target: str,
    trace_id: str,
    span_id: str,
    parent: str,
    name: str,
    start: Any,
    end: Any,
    status: str,
    attrs: dict[str, Any],
) -> Received:
    """A span to keep.

    :raises ValueError: Its ids or start time aren't what OTLP says they are.
    """
    if (
        not _TRACE_ID.fullmatch(trace_id)
        or not _SPAN_ID.fullmatch(span_id)
        or not trace_id.strip("0")
        or not span_id.strip("0")
    ):
        raise ValueError("ids")
    began, ended = int(start), int(end or 0)
    if not 0 < began < MAX_NANOS or not 0 <= ended < MAX_NANOS:
        raise ValueError("times")
    return Received(
        target=target,
        trace_id=trace_id,
        span_id=span_id,
        parent=parent if _SPAN_ID.fullmatch(parent) and parent.strip("0") else None,
        name=_text(name, MAX_NAME_CHARS),
        start=began / 1e9,
        end=max(began, ended) / 1e9,
        status=status,
        attrs=attrs,
    )


# Protobuf


def _proto_value(value: AnyValue, depth: int = 0) -> Any:
    kind = value.WhichOneof("value")
    if kind == "array_value" or kind == "kvlist_value":
        if depth >= MAX_DEPTH:
            return None
        if kind == "array_value":
            return [_proto_value(v, depth + 1) for v in value.array_value.values]
        return {kv.key: _proto_value(kv.value, depth + 1) for kv in value.kvlist_value.values}
    if kind == "bytes_value":
        return base64.b64encode(value.bytes_value).decode()
    return getattr(value, kind) if kind else None


def _proto_attributes(pairs: Iterable[Any]) -> dict[str, Any]:
    return _attributes((kv.key, _proto_value(kv.value)) for kv in pairs)


def _proto_span(target: str, span: ProtoSpan) -> Received:
    events = [_proto_attributes(e.attributes) for e in span.events]
    return _received(
        target,
        span.trace_id.hex(),
        span.span_id.hex(),
        span.parent_span_id.hex(),
        span.name,
        span.start_time_unix_nano,
        span.end_time_unix_nano,
        _STATUS.get(span.status.code, "UNSET"),
        with_events(_proto_attributes(span.attributes), events),
    )


def _proto_spans(body: bytes) -> Iterator[tuple[str, ProtoSpan]]:
    try:
        request = ExportTraceServiceRequest.FromString(body)
    except DecodeError as exc:
        raise Refused(400, "not an OTLP trace request") from exc
    for resource in request.resource_spans:
        target = _target(_proto_attributes(resource.resource.attributes))
        for scope in resource.scope_spans:
            for span in scope.spans:
                yield target, span


# JSON: lowerCamelCase keys, hex ids, 64-bit integers as strings or numbers


def _json_value(value: Any, depth: int = 0) -> Any:
    if not isinstance(value, dict):
        return None
    if "stringValue" in value:
        text = value["stringValue"]
        return _text(text, MAX_VALUE_CHARS) if isinstance(text, str) else None
    if "boolValue" in value:
        return value["boolValue"] if isinstance(value["boolValue"], bool) else None
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "bytesValue" in value:
        return value["bytesValue"] if isinstance(value["bytesValue"], str) else None
    if depth >= MAX_DEPTH:
        return None
    if "arrayValue" in value:
        return [_json_value(v, depth + 1) for v in value["arrayValue"].get("values") or []]
    if "kvlistValue" in value:
        return {
            _text(str(kv.get("key")), MAX_KEY_CHARS): _json_value(kv.get("value"), depth + 1)
            for kv in value["kvlistValue"].get("values") or []
        }
    return None


def _json_attributes(pairs: Any) -> dict[str, Any]:
    return _attributes((kv.get("key"), _json_value(kv.get("value"))) for kv in pairs or [])


def _json_status(status: Any) -> str:
    code = status.get("code") if isinstance(status, dict) else 0
    return _STATUS_NAMES.get(code, "UNSET") if isinstance(code, str) else _STATUS.get(code, "UNSET")


def _json_span(target: str, span: Any) -> Received:
    events = [_json_attributes(e.get("attributes")) for e in span.get("events") or []]
    return _received(
        target,
        str(span.get("traceId")).lower(),
        str(span.get("spanId")).lower(),
        str(span.get("parentSpanId") or "").lower(),
        str(span.get("name") or ""),
        span.get("startTimeUnixNano"),
        span.get("endTimeUnixNano"),
        _json_status(span.get("status")),
        with_events(_json_attributes(span.get("attributes")), events),
    )


def _json_spans(body: bytes) -> Iterator[tuple[str, Any]]:
    try:
        request = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise Refused(400, "not an OTLP trace request") from exc
    for resource in request.get("resourceSpans") or []:
        target = _target(_json_attributes((resource.get("resource") or {}).get("attributes")))
        for scope in resource.get("scopeSpans") or []:
            for span in scope.get("spans") or []:
                yield target, span


def decode(body: bytes, content_type: str, encoding: str = "") -> tuple[list[Received], int]:
    """The spans in a request, and how many were rejected.

    :raises Refused: The request as a whole can't be read.
    """
    if encoding == "gzip":
        body = _gunzip(body)
    elif encoding not in ("", "identity"):
        raise Refused(415, "unsupported content encoding")
    spans: Iterator[tuple[str, Any]]
    make: Callable[[str, Any], Received]
    if content_type == PROTOBUF:
        spans, make = _proto_spans(body), _proto_span
    else:
        spans, make = _json_spans(body), _json_span
    rows, rejected = [], 0
    try:
        for target, span in spans:
            try:
                rows.append(make(target, span))
            except (AttributeError, TypeError, ValueError, OverflowError):
                rejected += 1
    except (AttributeError, TypeError, ValueError, OverflowError) as exc:
        raise Refused(400, "not an OTLP trace request") from exc
    return rows, rejected


async def _body(request: Request) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdecimal() and int(declared) > MAX_BODY:
        raise Refused(413, "too large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            raise Refused(413, "too large")
        chunks.append(chunk)
    return b"".join(chunks)


def _answer(rejected: int, binary: bool) -> Response:
    if binary:
        answer = ExportTraceServiceResponse()
        if rejected:
            answer.partial_success.rejected_spans = rejected
            answer.partial_success.error_message = "spans without valid ids or times"
        return Response(answer.SerializeToString(), media_type=PROTOBUF)
    found = (
        {
            "partialSuccess": {
                "rejectedSpans": str(rejected),
                "errorMessage": "spans without valid ids or times",
            }
        }
        if rejected
        else {}
    )
    return Response(json.dumps(found), media_type=JSON)


def _error(status: int, message: str, binary: bool) -> Response:
    """A ``google.rpc.Status``, hand-encoded: it is two fields, and the message is short."""
    code = _UNAVAILABLE if status == 503 else _INVALID
    if binary:
        text = message.encode()
        body = bytes([0x08, code, 0x12, len(text)]) + text
        return Response(body, status_code=status, media_type=PROTOBUF)
    body = json.dumps({"code": code, "message": message}).encode()
    return Response(body, status_code=status, media_type=JSON)


# Forwarding to a target's Phoenix

#: Requests waiting to be forwarded, enough for a busy run; past this, a new one is dropped.
FORWARD_QUEUE = 1_000
_IDS = ("traceId", "spanId", "parentSpanId")


def _hex_ids(span: dict[str, Any]) -> None:
    """OTLP JSON's hex ids as the base64 protobuf's JSON mapping reads them, in place."""
    for item in (span, *(span.get("links") or [])):
        for key in _IDS:
            if isinstance(item.get(key), str):
                item[key] = base64.b64encode(bytes.fromhex(item[key])).decode()


def as_proto(body: bytes, content_type: str, encoding: str = "") -> ExportTraceServiceRequest:
    """A request's body as OTLP protobuf, whichever encoding it came in.

    :raises Refused: It can't be read.
    """
    if encoding == "gzip":
        body = _gunzip(body)
    if content_type == PROTOBUF:
        try:
            return ExportTraceServiceRequest.FromString(body)
        except DecodeError as exc:
            raise Refused(400, "not an OTLP trace request") from exc
    try:
        doc = json.loads(body)
        for resource in doc.get("resourceSpans") or []:
            for scope in resource.get("scopeSpans") or []:
                for span in scope.get("spans") or []:
                    _hex_ids(span)
        return ParseDict(doc, ExportTraceServiceRequest(), ignore_unknown_fields=True)
    except (ParseError, ValueError, TypeError, AttributeError, RecursionError) as exc:
        raise Refused(400, "not an OTLP trace request") from exc


def for_project(request: ExportTraceServiceRequest, target: str, project: str) -> bytes:
    """*target*'s spans in *request*, each resource named as Phoenix's *project*."""
    out = ExportTraceServiceRequest()
    for resource in request.resource_spans:
        if _target(_proto_attributes(resource.resource.attributes)) != target:
            continue
        kept = out.resource_spans.add()
        kept.CopyFrom(resource)
        attrs = kept.resource.attributes
        for n in reversed(range(len(attrs))):
            if attrs[n].key == PROJECT_ATTRIBUTE:
                del attrs[n]
        named = attrs.add()
        named.key = PROJECT_ATTRIBUTE
        named.value.string_value = project
    return out.SerializeToString()


#: Seconds between tries of one batch Phoenix didn't take: about a minute in all, then it's dropped.
BACKOFF = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0)
#: At most one warning about dropped spans this often, in seconds.
WARN_EVERY = 60.0
#: Seconds :meth:`Forward.close` waits for what is queued to be sent.
FLUSH_SECONDS = 5.0

#: A batch to forward: the body as received, and each target's Phoenix and span count in it.
_Batch = tuple[bytes, str, str, dict[str, tuple[PhoenixConfig, int]]]


class _Retry(Exception):
    """Phoenix may take the batch if it is sent again."""


class Forward:
    """Spans a target is sent, forwarded to its Phoenix (when *phoenix* gives one for it) in the
    background: a queue sized for a busy run, one worker, each batch tried again with *backoff*
    on a network error, a 5xx or a 429. A batch is dropped only when the queue is full or its
    tries run out, counted per target in :attr:`dropped` (no span content is ever logged)."""

    def __init__(
        self,
        phoenix: Callable[[str], PhoenixConfig | None],
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        size: int = FORWARD_QUEUE,
        backoff: tuple[float, ...] = BACKOFF,
    ) -> None:
        self._phoenix = phoenix
        self._transport = transport
        self._size = size
        self._backoff = backoff
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[_Batch]
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None
        #: Spans not forwarded, by target.
        self.dropped: dict[str, int] = {}
        self._warned = -WARN_EVERY

    def offer(self, body: bytes, content_type: str, encoding: str, targets: Iterable[str]) -> None:
        """Forward *body*'s spans of each of *targets* (one entry per span) that has a Phoenix,
        later."""
        counts: dict[str, int] = {}
        for t in targets:
            counts[t] = counts.get(t, 0) + 1
        wanted = {t: (c, n) for t, n in counts.items() if (c := self._phoenix(t)) is not None}
        if not wanted:
            return
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop, self._queue = loop, asyncio.Queue(self._size)
            self._client = net.client(timeout=10, transport=self._transport)
            self._task = loop.create_task(self._work(self._queue, self._client))
        try:
            self._queue.put_nowait((body, content_type, encoding, wanted))
        except asyncio.QueueFull:
            for target, (_, n) in wanted.items():
                self._drop(target, n)

    def _drop(self, target: str, spans: int) -> None:
        self.dropped[target] = self.dropped.get(target, 0) + spans
        now = time.monotonic()
        if now - self._warned >= WARN_EVERY:
            self._warned = now
            _logger.warning(
                "traces: %d spans not forwarded to Phoenix so far (%s)",
                sum(self.dropped.values()),
                ", ".join(f"{t}: {n}" for t, n in sorted(self.dropped.items())),
            )

    async def _work(self, queue: asyncio.Queue[_Batch], client: httpx.AsyncClient) -> None:
        while True:
            body, content_type, encoding, wanted = await queue.get()
            try:
                try:
                    request = await asyncio.to_thread(as_proto, body, content_type, encoding)
                except Refused:
                    request = None
                for target, (conf, n) in wanted.items():
                    if request is None or not await self._deliver(client, request, target, conf):
                        self._drop(target, n)
            finally:
                queue.task_done()

    async def _deliver(
        self,
        client: httpx.AsyncClient,
        request: ExportTraceServiceRequest,
        target: str,
        conf: PhoenixConfig,
    ) -> bool:
        """Whether Phoenix took *target*'s spans of *request*, tried again as long as that may
        help."""
        body = for_project(request, target, conf.project)
        key = conf.keys.get(conf.api_key_env)
        headers = {"Content-Type": PROTOBUF}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        for wait in (*self._backoff, None):
            try:
                response = await client.post(f"{conf.url}/v1/traces", content=body, headers=headers)
                if response.status_code == 429 or response.status_code >= 500:
                    raise _Retry
                return response.is_success
            except (httpx.TransportError, _Retry):
                if wait is None:
                    return False
                await asyncio.sleep(wait)
            except Exception:
                return False
        return False

    async def drain(self) -> None:
        """Wait until what was offered on this loop is sent or dropped."""
        if self._loop is asyncio.get_running_loop():
            await self._queue.join()

    async def close(self, flush: float = FLUSH_SECONDS) -> None:
        """Send what is queued for up to *flush* seconds, then stop."""
        if self._task is not None and self._loop is asyncio.get_running_loop():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._queue.join(), flush)
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            if self._client is not None:
                await self._client.aclose()
        self._loop = self._task = self._client = None


#: The forwarder of each store's targets, set by whoever knows their config (:class:`Targets`).
_FORWARDS: WeakKeyDictionary[SpanStore, Forward] = WeakKeyDictionary()


def forward_with(store: SpanStore, forward: Forward | None) -> None:
    """Spans received into *store* are also offered to *forward* (``None``: to none)."""
    if forward is None:
        _FORWARDS.pop(store, None)
    else:
        _FORWARDS[store] = forward


def dropped(store: SpanStore, target: str) -> int:
    """Spans received into *store* for *target* that never reached its Phoenix."""
    forward = _FORWARDS.get(store)
    return forward.dropped.get(target, 0) if forward is not None else 0


async def receive(request: Request, store: SpanStore) -> Response:
    """Take one OTLP request into *store*."""
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    encoding = request.headers.get("content-encoding", "").strip().lower()
    binary = content_type != JSON
    try:
        if content_type not in (PROTOBUF, JSON):
            raise Refused(415, "send application/x-protobuf or application/json")
        body = await _body(request)
        rows, rejected = await asyncio.to_thread(decode, body, content_type, encoding)
        await asyncio.to_thread(store.add, rows)
        if (forward := _FORWARDS.get(store)) is not None:
            try:
                forward.offer(body, content_type, encoding, (r.target for r in rows))
            except Exception as exc:
                _logger.warning("traces: spans not forwarded: %s", type(exc).__name__)
    except Refused as exc:
        return _error(exc.status, exc.message, binary)
    except SQLAlchemyError as exc:
        _logger.warning("traces: spans not stored: %s", type(exc).__name__)
        return _error(503, "the trace store failed", binary)
    return _answer(rejected, binary)
