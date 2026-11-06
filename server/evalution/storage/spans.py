"""The spans apps send evalution: the table behind :mod:`evalution.traces.builtin`.

A table of its own rather than documents: a try's spans are read by time window, trace id and
target, deduplicated by span id as exporters retry, and pruned by age, which JSON bodies under a
key can't be indexed for. Hosted, every workspace's spans share it, apart by ``workspace``.
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)

#: Made by :meth:`evalution.storage.database.Database.open` with the other tables.
traces = MetaData()

spans = Table(
    "spans",
    traces,
    # SQLite counts up only on an INTEGER key; Postgres needs the room of a bigint.
    Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
    Column("workspace", String(64), nullable=False),
    Column("target", String(128), nullable=False),
    Column("trace_id", String(32), nullable=False),
    Column("span_id", String(16), nullable=False),
    Column("parent", String(16)),
    Column("name", Text, nullable=False),
    Column("started", Float, nullable=False),
    Column("ended", Float, nullable=False),
    Column("status", String(16), nullable=False),
    Column("attrs", Text, nullable=False),
    Column("ai", Boolean, nullable=False),
    #: When it arrived, which is what retention goes by: a span's own times are the app's word.
    Column("received", Float, nullable=False),
    UniqueConstraint("workspace", "trace_id", "span_id"),
    Index("spans_window", "workspace", "target", "started", "id"),
    Index("spans_received", "received"),
)
