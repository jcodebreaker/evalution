"""The version of the code a run played, by name, and whether evalution can vouch for it (ADR 0011).

A run plays a **version**: for now always ``disk``, the target's checkout as it stands, uncommitted
changes included. What it records of it:

- ``branch`` and ``commit``: the checkout's branch (``None`` when it isn't on one) and last commit.
- ``dirty``: whether a watched file differs from that commit; ``None`` when nobody looked.
- ``played``: what actually played. On a compose copy, each service's image, with its id and when
  it was built (``{"kind": "images", "images": [...]}``); on a process copy, the hash of the
  working tree it ran (``{"kind": "tree", "code": ...}``); on the person's own app, reached as it
  runs, ``None``: evalution didn't start it.
- ``verified``: whether what played is the checkout, ``None`` when evalution can't tell, and
  ``why_unverified`` in plain words. A compose copy plays whatever images are on the computer, so
  an image the app's own files build that is older than the last commit, or than a change not yet
  committed, may be an earlier version.

All pure: the checkout comes from :mod:`evalution.code`, the images from
:mod:`evalution.isolation.launch`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: The version that is the target's checkout as it stands: the only one there is for now.
DISK = "disk"
#: How much of a commit a person is shown.
SHORT = 7
#: Why a run on the person's own app can't be vouched for.
NOT_STARTED = "the app as it's running; evalution didn't start it"
#: What an old run says of its version.
NOT_RECORDED = "not recorded: run before evalution kept what played"
NO_CHECKOUT = "the code folder isn't a git checkout, so there is no commit to compare with"
NO_BUILT = "the app's compose files build no image of their own, so its code isn't in them"


@dataclass(frozen=True)
class Checkout:
    """The target's code folder as it stands.

    :param committed_at: When the last commit was made, seconds since the epoch.
    :param changed_at: The newest change to a watched file not yet committed.
    """

    branch: str | None = None
    commit: str | None = None
    committed_at: float | None = None
    dirty: bool | None = None
    changed_at: float | None = None


@dataclass(frozen=True)
class Image:
    """One service's image, as a copy plays it.

    :param image: Its name, as the compose files give it.
    :param id: Docker's id for it: the same id is the same image, whatever it is called.
    :param created: When it was built, seconds since the epoch.
    :param built: The app's own files build it (a ``build:`` section), as against an image pulled
        from elsewhere (a database): only those hold the app's code.
    """

    service: str
    image: str
    id: str
    created: float
    built: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "service": self.service,
            "image": self.image,
            "id": self.id,
            "created": self.created,
            "built": self.built,
        }


@dataclass(frozen=True)
class Version:
    """What a run played (see the module's docstring)."""

    ref: str = DISK
    branch: str | None = None
    commit: str | None = None
    dirty: bool | None = None
    played: dict[str, Any] | None = None
    verified: bool | None = None
    why_unverified: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "branch": self.branch,
            "commit": self.commit,
            "dirty": self.dirty,
            "played": self.played,
            "verified": self.verified,
            "why_unverified": self.why_unverified,
        }


def short(commit: Any) -> str:
    return str(commit)[:SHORT] if commit else ""


def _said(images: Sequence[Image]) -> tuple[str, str, str]:
    """Their names, and the verb and pronoun that go with how many."""
    names = list(dict.fromkeys(i.image for i in images))
    one = len(names) == 1
    listed = names[0] if one else ", ".join(names[:-1]) + " and " + names[-1]
    return listed, "was" if one else "were", "it" if one else "they"


def _from(
    checkout: Checkout, played: dict[str, Any] | None, verified: bool | None, why: str | None
) -> Version:
    return Version(
        ref=DISK,
        branch=checkout.branch,
        commit=checkout.commit,
        dirty=checkout.dirty,
        played=played,
        verified=verified,
        why_unverified=why,
    )


def judge_images(checkout: Checkout, images: Sequence[Image]) -> tuple[bool | None, str | None]:
    """Whether *images* are the checkout: ``(verified, why not)``."""
    built = [i for i in images if i.built]
    if not built:
        return None, NO_BUILT
    if checkout.commit is None or checkout.committed_at is None:
        return None, NO_CHECKOUT
    older = [i for i in built if i.created < checkout.committed_at]
    if older:
        names, was, it = _said(older)
        return False, (
            f"{names} {was} built before the last commit ({short(checkout.commit)}), "
            f"so {it} may be an earlier version"
        )
    since = checkout.changed_at
    stale = [i for i in built if since is not None and i.created < since] if checkout.dirty else []
    if stale:
        names, was, _ = _said(stale)
        return False, f"files changed since {names} {was} built"
    return True, None


def on_images(checkout: Checkout, images: Sequence[Image]) -> Version:
    """A compose copy's version: the images it plays, vouched for when none is older than the
    checkout."""
    verified, why = judge_images(checkout, images)
    played = {"kind": "images", "images": [i.as_dict() for i in images]}
    return _from(checkout, played, verified, why)


def on_tree(checkout: Checkout, code: str | None) -> Version:
    """A process copy's version: it runs the working tree as it is, so what played is the
    checkout, uncommitted changes and all."""
    return _from(checkout, {"kind": "tree", "code": code}, True, None)


def unstarted(checkout: Checkout) -> Version:
    """The person's own app, reached as it runs: whatever it was started from."""
    return _from(checkout, None, None, NOT_STARTED)


def not_recorded(commit: Any) -> dict[str, Any]:
    """An old run's version: what was on disk, its commit as it recorded it, nothing vouched."""
    return {
        "ref": DISK,
        "branch": None,
        "commit": commit or None,
        "dirty": None,
        "played": None,
        "verified": None,
        "why_unverified": NOT_RECORDED,
    }


def described(version: Mapping[str, Any] | None, *, now: bool = False) -> str:
    """The version in words: "what was on disk: page-testing @ e535c4d, with uncommitted
    changes"; *now*, before a run, "what's on disk now: ...". Parts that don't apply are left
    out."""
    found = version or {}
    head = "what's on disk now" if now else "what was on disk"
    where = " @ ".join(p for p in (found.get("branch"), short(found.get("commit"))) if p)
    said = f"{head}: {where}" if where else head
    return said + (", with uncommitted changes" if found.get("dirty") else "")


def _built_ids(played: Mapping[str, Any]) -> dict[str, str]:
    return {
        str(i.get("service")): str(i.get("id"))
        for i in played.get("images") or []
        if i.get("built") and i.get("id")
    }


def same_played(a: Mapping[str, Any] | None, b: Mapping[str, Any] | None) -> bool | None:
    """Whether two runs played the same code, by what played: the same images the app builds
    (whatever the checkout said), or the same working tree. ``None`` when either didn't keep it,
    or they played in different ways."""
    one, two = (a or {}).get("played"), (b or {}).get("played")
    if not one or not two or one.get("kind") != two.get("kind"):
        return None
    if one["kind"] == "images":
        first, second = _built_ids(one), _built_ids(two)
        return (first == second) if first and second else None
    if one["kind"] == "tree":
        return (one.get("code") == two.get("code")) if one.get("code") and two.get("code") else None
    return None
