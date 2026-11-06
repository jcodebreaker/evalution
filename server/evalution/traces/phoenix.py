"""A target's spans from a Phoenix project (docs/architecture.md, "The Target protocol"): Phoenix
REST v1 only, no client library, no GraphQL. This is only the translation of Phoenix's REST; what
spans mean is :mod:`evalution.traces.view`'s.

Calls used:

* ``GET /v1/projects/{project}/spans?start_time=&end_time=&limit=&cursor=[&trace_id=]``: spans
  in a time window, paged by ``next_cursor``. Every filter is also applied here, so a Phoenix
  that ignores ``trace_id`` still gives the right answer.
* ``GET /v1/projects/{project}``: whether the project exists (the connection check). A
  project that does not exist (404) reads as no spans.
* ``/redirects/traces/{trace_id}``: the browser link to a trace (no Phoenix-internal id).

A session's spans are every span whose ``trace_attribute`` (default ``session.id``) equals the
session's value in the window, plus the rest of the traces they belong to (OpenInference sets
the session on the root, not always on children).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from evalution.config import PhoenixConfig
from evalution.core import net
from evalution.traces.base import Span, SpanReader, kind_of, nest, with_events

PAGE = 1_000


def _epoch(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)).timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


def span_of(row: Mapping[str, Any]) -> Span:
    """A span from a Phoenix REST row."""
    events = [e.get("attributes") or {} for e in row.get("events") or []]
    attrs = nest(with_events(row.get("attributes") or {}, events))
    context = row.get("context") or {}
    return Span(
        id=str(context.get("span_id") or row.get("span_id") or row.get("id")),
        trace_id=str(context.get("trace_id") or row.get("trace_id") or ""),
        parent=row.get("parent_id") or None,
        name=str(row.get("name") or ""),
        kind=kind_of(attrs, row.get("span_kind")),
        start=_epoch(row.get("start_time")),
        end=_epoch(row.get("end_time") or row.get("start_time")),
        status=str(row.get("status_code") or ""),
        attrs=attrs,
    )


class Phoenix(SpanReader):
    """One target's Phoenix project, and the *also* sources read with it (evalution's own
    Phoenix reads a target's project, the default project and the app's own Phoenix). A missing
    project reads as empty."""

    def __init__(
        self,
        conf: PhoenixConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        api_key: str | None = None,
        also: Sequence[SpanReader] = (),
    ):
        self.conf = conf
        self.also = list(also)
        key = api_key or conf.keys.get(conf.api_key_env)
        self._client = net.client(
            base_url=conf.url,
            headers={"Authorization": f"Bearer {key}"} if key else {},
            timeout=30.0,
            transport=transport,
        )

    async def own_pages(
        self, start: float, end: float, trace_id: str | None
    ) -> AsyncIterator[list[Span]]:
        """This project's spans only, newest first."""
        params: dict[str, Any] = {
            "start_time": _iso(start),
            "end_time": _iso(end),
            "limit": PAGE,
        }
        if trace_id:
            params["trace_id"] = trace_id
        while True:
            response = await self._client.get(
                f"/v1/projects/{self.conf.project}/spans", params=params
            )
            if response.status_code == 404:
                return
            response.raise_for_status()
            page = response.json()
            rows = page.get("data") or []
            yield [s for s in map(span_of, rows) if not trace_id or s.trace_id == trace_id]
            cursor = page.get("next_cursor")
            if not cursor or not rows:
                return
            params["cursor"] = cursor

    async def project_exists(self) -> bool:
        """:raises httpx.HTTPError: Phoenix did not answer, or refused the key."""
        response = await self._client.get(f"/v1/projects/{self.conf.project}")
        if response.status_code == 404:
            return False
        response.raise_for_status()
        return True

    def trace_url(self, trace_id: str | None) -> str | None:
        base = self.conf.public_url or self.conf.url
        return f"{base}/redirects/traces/{trace_id}" if trace_id else None

    async def close(self) -> None:
        await self._client.aclose()
        await super().close()
