"""Who may sign in and where they belong: the tables behind :mod:`evalution.accounts`.

Kept apart from the documents so a workspace's data and the people in it don't share a shape.
Nothing secret is stored as given: an invite code, an API token and a login's cookie value are
kept only as their SHA-256, so a copy of the database lets no one in.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, Float, ForeignKey, MetaData, String, Table, UniqueConstraint

#: Made by :meth:`evalution.storage.database.Database.open` with the documents' tables.
identity = MetaData()

users = Table(
    "users",
    identity,
    Column("id", String(64), primary_key=True),
    Column("issuer", String(512), nullable=False),
    Column("subject", String(255), nullable=False),
    Column("email", String(320), nullable=False, default=""),
    Column("name", String(255), nullable=False, default=""),
    Column("created_at", Float, nullable=False),
    UniqueConstraint("issuer", "subject"),
)
workspaces = Table(
    "workspaces",
    identity,
    Column("id", String(64), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("personal", Boolean, nullable=False),
    Column("created_at", Float, nullable=False),
)
members = Table(
    "members",
    identity,
    Column("workspace_id", String(64), ForeignKey("workspaces.id"), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id"), primary_key=True, index=True),
    Column("joined_at", Float, nullable=False),
)
invites = Table(
    "invites",
    identity,
    Column("code_hash", String(64), primary_key=True),
    Column("workspace_id", String(64), ForeignKey("workspaces.id"), nullable=False),
    Column("created_by", String(64), nullable=False),
    Column("expires_at", Float, nullable=False),
)
api_tokens = Table(
    "api_tokens",
    identity,
    Column("id", String(64), primary_key=True),
    Column("workspace_id", String(64), ForeignKey("workspaces.id"), nullable=False, index=True),
    Column("name", String(255), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("prefix", String(16), nullable=False),
    Column("created_by", String(64), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("last_used_at", Float),
)
logins = Table(
    "logins",
    identity,
    Column("id_hash", String(64), primary_key=True),
    Column("user_id", String(64), ForeignKey("users.id"), nullable=False),
    Column("created_at", Float, nullable=False),
    Column("expires_at", Float, nullable=False),
)
