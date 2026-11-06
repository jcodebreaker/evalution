"""People, their workspaces and who belongs where.

Every member of a workspace is equal: anyone may rename it, invite, or remove anyone, themselves
included. A person's personal workspace is made at their first sign-in, is theirs alone, and
can't be left or shared, so "the user's personal workspace" always names one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import and_, delete, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from evalution.accounts.ids import new_id
from evalution.storage.database import Database
from evalution.storage.identity import members, users, workspaces


class AccountError(ValueError):
    """What was asked can't be done; the message says why, for the person asking."""


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    personal: bool


@dataclass(frozen=True)
class Member:
    id: str
    email: str
    name: str
    joined_at: float


def clean_name(value: object) -> str:
    """A workspace's name as given, trimmed. :raises AccountError: Not a short text."""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100:
        raise AccountError("name: 1 to 100 characters")
    return value.strip()


def sign_in(database: Database, issuer: str, subject: str, email: str, name: str) -> User:
    """The person the issuer vouched for: made, with their personal workspace, the first time;
    their email and name kept current after that."""
    found = _find(database, issuer, subject)
    if found is not None:
        if (found.email, found.name) != (email, name):
            with database.engine.begin() as db:
                db.execute(
                    update(users).where(users.c.id == found.id).values(email=email, name=name)
                )
        return User(found.id, email, name)
    now = time.time()
    user_id, workspace_id = new_id(), new_id()
    try:
        with database.engine.begin() as db:
            db.execute(
                insert(users).values(
                    id=user_id,
                    issuer=issuer,
                    subject=subject,
                    email=email,
                    name=name,
                    created_at=now,
                )
            )
            _make_workspace(db, workspace_id, _personal_name(name, email), True, user_id, now)
    except IntegrityError:  # the same person's other first sign-in, at the same moment, won
        if (found := _find(database, issuer, subject)) is None:
            raise
        return found
    return User(user_id, email, name)


def _find(database: Database, issuer: str, subject: str) -> User | None:
    c = users.c
    with database.engine.begin() as db:
        row = db.execute(
            select(c.id, c.email, c.name).where(c.issuer == issuer, c.subject == subject)
        ).first()
    return User(*row) if row else None


def _personal_name(name: str, email: str) -> str:
    return (name.strip() or email.split("@")[0].strip() or "Personal")[:100]


def _make_workspace(
    db: Connection, workspace_id: str, name: str, personal: bool, user_id: str, now: float
) -> None:
    db.execute(
        insert(workspaces).values(id=workspace_id, name=name, personal=personal, created_at=now)
    )
    db.execute(insert(members).values(workspace_id=workspace_id, user_id=user_id, joined_at=now))


def user(database: Database, user_id: str) -> User | None:
    c = users.c
    with database.engine.begin() as db:
        row = db.execute(select(c.id, c.email, c.name).where(c.id == user_id)).first()
    return User(*row) if row else None


def create_workspace(database: Database, user_id: str, name: str) -> Workspace:
    """A shared workspace, *user_id* its first member."""
    workspace = Workspace(new_id(), clean_name(name), False)
    with database.engine.begin() as db:
        _make_workspace(db, workspace.id, workspace.name, False, user_id, time.time())
    return workspace


def rename(database: Database, workspace_id: str, name: str) -> Workspace:
    name = clean_name(name)
    with database.engine.begin() as db:
        db.execute(update(workspaces).where(workspaces.c.id == workspace_id).values(name=name))
    found = workspace(database, workspace_id)
    assert found is not None
    return found


def workspace(database: Database, workspace_id: str) -> Workspace | None:
    c = workspaces.c
    with database.engine.begin() as db:
        row = db.execute(select(c.id, c.name, c.personal).where(c.id == workspace_id)).first()
    return Workspace(row[0], row[1], bool(row[2])) if row else None


def workspaces_of(database: Database, user_id: str) -> list[Workspace]:
    """Where *user_id* belongs: their personal workspace first, then in the order they joined."""
    w, m = workspaces.c, members.c
    with database.engine.begin() as db:
        rows = db.execute(
            select(w.id, w.name, w.personal)
            .join(members, m.workspace_id == w.id)
            .where(m.user_id == user_id)
            .order_by(w.personal.desc(), m.joined_at, w.id)
        ).all()
    return [Workspace(i, name, bool(personal)) for i, name, personal in rows]


def personal_workspace(database: Database, user_id: str) -> Workspace | None:
    return next((w for w in workspaces_of(database, user_id) if w.personal), None)


def is_member(database: Database, workspace_id: str, user_id: str) -> bool:
    m = members.c
    with database.engine.begin() as db:
        found = db.scalar(
            select(m.user_id).where(m.workspace_id == workspace_id, m.user_id == user_id)
        )
    return found is not None


def members_of(database: Database, workspace_id: str) -> list[Member]:
    """The members, earliest first."""
    m, u = members.c, users.c
    with database.engine.begin() as db:
        rows = db.execute(
            select(u.id, u.email, u.name, m.joined_at)
            .join(users, u.id == m.user_id)
            .where(m.workspace_id == workspace_id)
            .order_by(m.joined_at, u.id)
        ).all()
    return [Member(*row) for row in rows]


def join(database: Database, workspace_id: str, user_id: str) -> None:
    """Make *user_id* a member; one already is stays as they were.

    :raises AccountError: The workspace is someone's personal one.
    """
    found = workspace(database, workspace_id)
    if found is None or found.personal:
        raise AccountError("a personal workspace can't be shared")
    try:
        with database.engine.begin() as db:
            db.execute(
                insert(members).values(
                    workspace_id=workspace_id, user_id=user_id, joined_at=time.time()
                )
            )
    except IntegrityError:  # already a member
        pass


def remove(database: Database, workspace_id: str, user_id: str) -> None:
    """Take *user_id* out of the workspace (leaving, when it is the one asking).

    :raises LookupError: Not a member.
    :raises AccountError: A personal workspace, or the last member.
    """
    m = members.c
    found = workspace(database, workspace_id)
    if found is None or not is_member(database, workspace_id, user_id):
        raise LookupError(user_id)
    if found.personal:
        raise AccountError("a personal workspace can't be left")
    with database.engine.begin() as db:
        count = db.scalar(select(func.count()).where(m.workspace_id == workspace_id))
        if count <= 1:
            raise AccountError("the last member can't leave")
        db.execute(
            delete(members).where(and_(m.workspace_id == workspace_id, m.user_id == user_id))
        )
