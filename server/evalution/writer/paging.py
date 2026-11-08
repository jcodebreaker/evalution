"""The test writer's part in learning a target's page map (ADR 0010, after v1): reading the
frontend's code, looking at the page and trying one entry at a time on evalution's own copy,
proposing a map as data with evidence per entry, and rehearsing it with one real message once the
admin approved that.

The model never drives the page. It reads (:func:`read_code`), looks (:func:`look`: an
accessibility snapshot, nothing clicked), tries (:func:`try_entry`: one entry, locating only; the
message box takes a fixed harmless text, Send is never clicked and nothing but a read leaves the
page), and proposes (:func:`propose`), and deterministic code does the rest
(:mod:`evalution.runs.page.learn`). All of it happens only on a copy of the app that a proven
isolation plan starts: without one, every tool is refused in plain words.

A proposal is kept on the session as a draft. Every entry a turn needs (its ready sign, the
message box, Send and the replies) rests on evidence: a file and line in the target's code, or a
try this session that found it. Rehearsing it (:func:`rehearse`) spends one turn of the app, so it
runs only once the admin approved the proposal, as an isolation plan's rehearsal does. A map that
showed its reply is kept as ``tried`` (:meth:`~evalution.project.pagemaps.PageMapStore.propose`),
with the rehearsal's screenshots, for the admin to approve once on the Test environment page. A
failed rehearsal stays on the session as evidence for a revision; after :data:`MAX_REVISIONS`
revisions failed too, no more are run: the admin is asked.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from evalution.code import HASH_CAP, HASH_SIZE, READ_CAP, SKIP_DIRS, CodeError, is_secret
from evalution.config import ConfigError
from evalution.project import pagemaps
from evalution.project.pagemaps import KeptMap
from evalution.runs.page.map import ENTRIES, PageMap, as_block, described, entry_of, read_map

if TYPE_CHECKING:
    from evalution.adapters.base import Capabilities
    from evalution.config import Config
    from evalution.runs.page.learn import Rehearsed
    from evalution.runs.runner import Runs
    from evalution.storage.files import Files
    from evalution.targets import Targets

#: Revisions after a failed rehearsal one session may try; then the admin is asked.
MAX_REVISIONS = 3
#: The entries every turn needs, each resting on code or on a try that found it.
CORE = ("ready", "actions.type", "actions.send", "replies.in")
#: Entries a rehearsal that showed its reply proves were there.
PROVEN = ("url", "ready", "sign_in", "open_session", "actions.type", "actions.send", "replies.in")
#: Files read as a page's code: by their kind, or any source holding markup.
FRONTEND = re.compile(r"\.(tsx|jsx|ts|js|mjs|vue|svelte|astro|html?|erb|ejs|hbs|jinja2?|j2)$")
_MARKUP = re.compile(r"<(button|textarea|input|form|main)\b", re.IGNORECASE)
#: Files the code read looks through at most, and candidates shown per entry.
SCAN_FILES = 400
SHOWN_PER_ENTRY = 8
_LINE_KEPT = 200

_ATTR = r"""=\s*\{?\s*["'`]([^"'`]{1,80})["'`]"""
_ARIA_LABEL = re.compile(r"aria-label" + _ATTR)
_PLACEHOLDER = re.compile(r"placeholder" + _ATTR)
_TEST_ID = re.compile(r"data-test-?id" + _ATTR, re.IGNORECASE)
_ROLE = re.compile(r"""role=["']([a-z]+)["']""")
_BOX = re.compile(
    r"<(textarea|input)\b(?![^>]*type=[\"'](hidden|checkbox|radio|submit|file))", re.I
)
_PASSWORD = re.compile(r"""type=["']password["']""")
_EMAIL = re.compile(r"""type=["']email["']|autocomplete=["']username["']""")
_BUTTON = re.compile(r"<(button|Button|IconButton)\b|role=[\"']button[\"']")
_BUTTON_TEXT = re.compile(r">\s*([A-Za-z][A-Za-z .!?'-]{0,40}?)\s*<")
_SEND = re.compile(r"\b(send|submit|ask)\b", re.IGNORECASE)
_NEW_CHAT = re.compile(r"\bnew\s+(chat|conversation|thread|session)\b", re.IGNORECASE)
_STOP = re.compile(r"\b(stop|cancel)\b", re.IGNORECASE)
_SIGN_IN = re.compile(r"\b(sign|log)\s?in\b", re.IGNORECASE)
_SUBMIT = re.compile(r"""type=["']submit["']""")
_LIVE = re.compile(r"""role=["']log["']|aria-live""")
_MAIN = re.compile(r"<main\b")
_BUSY = re.compile(r"aria-busy")
_PROXY = re.compile(
    r"X-Forwarded-(Email|User)|X-Auth-Request-(Email|User)|Remote-User|Proxy-Secret",
    re.IGNORECASE,
)
_ROUTE = re.compile(
    r"""(?:\bpath\s*[:=]\s*|<Route[^>]*\bpath=|@\w+\.(?:get|route)\(\s*|\bapp\.get\(\s*|"""
    r"""\bhref=\{?|location\.(?:assign|replace)\(\s*|\bnavigate\(\s*|replaceState\([^,]*,[^,]*,\s*)"""
    r"""["'`](/[^"'`\s]{0,80})"""
)
_SESSIONISH = re.compile(r":\w+|\{\w+\}|\$\{|\[\w+\]|<\w+>|\" ?\+")


async def look(
    config: Config, target: str, capabilities: Capabilities, url: str, sign_in: Any
) -> dict[str, Any]:
    """The copy's page as :func:`evalution.runs.page.learn.look` shows it. Imported when first
    used, so the writer loads without it; swapped in tests."""
    from evalution.runs.page import learn

    return await learn.look(config, target, capabilities, url, sign_in)


async def try_on_copy(
    config: Config, target: str, capabilities: Capabilities, page: PageMap, entry: str
) -> dict[str, Any]:
    """One entry tried as :func:`evalution.runs.page.learn.try_entry` does; swapped in tests."""
    from evalution.runs.page import learn

    return await learn.try_entry(config, target, capabilities, page, entry)


async def rehearse_map(
    config: Config, target: str, capabilities: Capabilities, page: PageMap, files: Files | None
) -> Rehearsed:
    """One real message through *page*, as :func:`evalution.runs.page.learn.rehearse` plays it;
    swapped in tests."""
    from evalution.runs.page import learn

    return await learn.rehearse(config, target, capabilities, page, files)


def refusal(config: Config, target: str, capabilities: Capabilities) -> str | None:
    """Why the page can't be opened (:func:`evalution.runs.page.learn.refusal`); swapped in
    tests."""
    from evalution.runs.page import learn

    return learn.refusal(config, target, capabilities)


def state(session: dict[str, Any]) -> dict[str, Any]:
    """This session's work on its target's page map: proposals, what tries found, rehearsals."""
    found = session.setdefault("page_map", {})
    return found.setdefault(
        str(session["target"]),
        {"proposals": {}, "latest": None, "rehearsals": [], "failed": 0, "found": {}},
    )


class _Refused(Exception):
    """A tool that can't act now; its message is what the model is told."""


def _capabilities(targets: Targets, target: str) -> Capabilities:
    try:
        return targets.adapter(target).capabilities
    except Exception as exc:
        raise _Refused(f"Refused: {target}'s adapter can't be made: {exc}") from exc


def _on_copy(runs: Runs, target: str) -> Capabilities:
    """What the target can do, once it's clear its page can be opened on evalution's copy.

    :raises _Refused: It can't, in plain words.
    """
    capabilities = _capabilities(runs.targets, target)
    if (why := refusal(runs.targets.config, target, capabilities)) is not None:
        raise _Refused(f"Refused: {why} Tell the admin in plain words; nothing was opened.")
    return capabilities


@contextlib.asynccontextmanager
async def _claimed(runs: Runs) -> AsyncIterator[None]:
    """The copy is evalution's to start: not while a run or a test message is going, and no run
    starts until this ends.

    :raises _Refused: One is going.
    """
    if runs.busy() or runs.probing or not runs.lock.acquire():
        raise _Refused("Refused: a run is going, and it plays on the same copy. Try when it ends.")
    try:
        yield
    finally:
        runs.lock.release()


async def guarded(work: Any) -> Any:
    """*work*'s result, or a refusal's words."""
    try:
        return await work
    except _Refused as exc:
        return str(exc)


# Reading the code


def _suggest(line: str, role: str | None) -> dict[str, str] | None:
    """The locator *line* suggests for an element of *role*: its test id, else its role with the
    accessible name its label, placeholder or text gives."""
    if (found := _TEST_ID.search(line)) is not None:
        return {"test_id": found.group(1)}
    if role is None:
        return None
    for pattern in (_ARIA_LABEL, _PLACEHOLDER):
        if (found := pattern.search(line)) is not None:
            return {"role": role, "name": found.group(1)}
    if role == "button" and (found := _BUTTON_TEXT.search(line)) is not None:
        return {"role": role, "name": found.group(1).strip()}
    return None


def _kinds(line: str) -> list[tuple[str, dict[str, str] | None]]:
    """What a line of a page's code may be, as ``(entry, suggested locator)``."""
    out: list[tuple[str, dict[str, str] | None]] = []
    if _PASSWORD.search(line) or _EMAIL.search(line):
        out.append(("sign_in", _suggest(line, "textbox")))
    elif _BOX.search(line):
        out.append(("actions.type", _suggest(line, "textbox")))
    if _BUTTON.search(line):
        named = _suggest(line, "button")
        said = " ".join([line, *(named or {}).values()])
        if _NEW_CHAT.search(said):
            out.append(("actions.new_chat", named))
        elif _SIGN_IN.search(said):
            out.append(("sign_in", named))
        elif _STOP.search(said):
            out.append(("busy", named))
        elif _SEND.search(said) or _SUBMIT.search(line):
            out.append(("actions.send", named))
    if _LIVE.search(line):
        role = _ROLE.search(line)
        out.append(("replies.in", _suggest(line, role.group(1) if role else "log")))
    elif _MAIN.search(line):
        out.append(("replies.in", {"role": "main"}))
    if _BUSY.search(line):
        out.append(("busy", {"attr": "aria-busy", "equals": "true"}))
    if _PROXY.search(line):
        out.append(("sign_in", None))
    return out


def scan(files: dict[str, str]) -> dict[str, Any]:
    """Candidates for each entry of a page map in *files* (``path -> text``), with ``file:line``
    evidence, and the page's routes."""
    candidates: dict[str, list[dict[str, Any]]] = {}
    routes: list[dict[str, Any]] = []
    for path, text in files.items():
        for number, raw in enumerate(text.splitlines(), 1):
            line = raw.strip()
            if not line or len(line) > 2000:
                continue
            for entry, suggest in _kinds(line):
                found = {"file": path, "line": number, "said": line[:_LINE_KEPT]}
                if suggest:
                    found["suggest"] = suggest
                candidates.setdefault(entry, []).append(found)
            for route in _ROUTE.findall(line):
                if not route.startswith(("/v1/", "/api/", "/static/", "/assets/")):
                    session = bool(_SESSIONISH.search(route)) or route.endswith("/")
                    routes.append({"path": route, "file": path, "line": number, "session": session})
    shown = {e: c[:SHOWN_PER_ENTRY] for e, c in candidates.items()}
    more = {e: len(c) - SHOWN_PER_ENTRY for e, c in candidates.items() if len(c) > SHOWN_PER_ENTRY}
    return {"candidates": shown, "more": more, "routes": routes[:40]}


def _frontend(targets: Targets, target: str) -> dict[str, str]:
    """The target's page code: files of a page's kind, or any source holding markup."""
    code = targets.code(target)
    if code is None:
        return {}
    found: dict[str, str] = {}
    for rel in code.files(HASH_CAP):
        if len(found) >= SCAN_FILES:
            break
        try:
            text = code.read(rel)
        except (CodeError, OSError):
            continue
        if FRONTEND.search(rel) or _MARKUP.search(text):
            found[rel] = text
    return found


#: Where a frontend usually is in a code folder, looked through for a chat page even when the
#: target's code settings leave it out (``code.include`` set to its prompts, say).
FRONTEND_ROOTS = ("web", "frontend", "client", "app", "src")
#: Files of a page's kind, as a frontend's own; the files looked through at most.
PAGE_FILES = re.compile(r"\.(tsx|jsx|vue|svelte|html?)$")
BEYOND_FILES = 400
#: Folders never looked in: built, copied or tests.
_NOT_FRONTEND = SKIP_DIRS | {"out", "coverage", "vendor", "test", "tests", "__tests__", "e2e"}
_TEST_FILE = re.compile(r"\.(test|spec|stories)\.[a-z]+$")


def frontend_beyond(root: Path) -> dict[str, str]:
    """A frontend's page files under *root*'s usual frontend folders (:data:`FRONTEND_ROOTS`),
    whatever the target's code settings include: read only, at most :data:`BEYOND_FILES`, each
    file cut at the code read's limit, nothing hidden, built, a test or a secret, and nothing
    that leads out of *root*."""
    root = root.resolve()
    found: dict[str, str] = {}
    for top in FRONTEND_ROOTS:
        start = root / top
        if not start.is_dir() or not start.resolve().is_relative_to(root):
            continue
        for folder, dirs, names in os.walk(start):
            dirs[:] = sorted(d for d in dirs if d not in _NOT_FRONTEND and not d.startswith("."))
            for name in sorted(names):
                if len(found) >= BEYOND_FILES:
                    return found
                path = Path(folder) / name
                rel = path.relative_to(root).as_posix()
                if not PAGE_FILES.search(name) or _TEST_FILE.search(name) or is_secret(rel):
                    continue
                try:
                    if not path.resolve().is_relative_to(root) or path.stat().st_size > HASH_SIZE:
                        continue
                    with path.open("rb") as handle:
                        found[rel] = handle.read(READ_CAP).decode("utf-8", errors="replace")
                except OSError:
                    continue
    return found


def outside_code(targets: Targets, target: str) -> list[str]:
    """The frontend folders of *target*'s code folder that hold a page's files its code
    settings leave out."""
    code = targets.code(target)
    if code is None:
        return []
    beyond = frontend_beyond(code.root)
    return sorted({rel.split("/")[0] for rel in beyond if not code.allowed(rel)})


#: Seconds what :func:`chat_page` found in a code folder is taken as still true.
CHAT_PAGE_SECONDS = 300.0
_chat_pages: dict[tuple[str, str], tuple[float, str | None]] = {}


def _chat_in(files: dict[str, str]) -> str | None:
    candidates = scan(files)["candidates"]
    boxes, sends = candidates.get("actions.type"), candidates.get("actions.send")
    return str(boxes[0]["file"]) if boxes and sends else None


def chat_page(targets: Targets, target: str) -> str | None:
    """Where *target*'s code has a chat page: the file with a message box, when the code also has
    a Send. Read as ``read_page_code`` reads it, else in the code folder's usual frontend folders
    even when its code settings leave them out (:func:`frontend_beyond`): an app whose settings
    name only its prompts still has a page. ``None`` without a code folder, or when its code has
    no such page. Read again at most every :data:`CHAT_PAGE_SECONDS`."""
    code = targets.code(target)
    if code is None:
        return None
    key = (target, str(code.root))
    now = time.monotonic()
    if (kept := _chat_pages.get(key)) is not None and now - kept[0] < CHAT_PAGE_SECONDS:
        return kept[1]
    found = _chat_in(_frontend(targets, target)) or _chat_in(frontend_beyond(code.root))
    _chat_pages[key] = (now, found)
    return found


async def read_code(targets: Targets, session: dict[str, Any]) -> Any:
    """The ``read_page_code`` result: candidates for every entry of a map, from the code."""
    target = str(session["target"])
    if targets.code(target) is None:
        return (
            f"Refused: {target} has no code folder, so there is no page code to read. Tell the "
            "admin they can say where it is on the Connect page; look_at_page still shows the page."
        )
    files = await asyncio.to_thread(_frontend, targets, target)
    if not files:
        left_out = await asyncio.to_thread(outside_code, targets, target)
        if left_out:
            return {
                "files_read": 0,
                "left_out": left_out,
                "note": (
                    f"The target's code settings leave its page's code out ({', '.join(left_out)}"
                    "/), so none can be read or cited. Learn the page from the page itself: "
                    "look_at_page, then find each entry with try_page_entry, which is its "
                    "evidence."
                ),
            }
        return {"files_read": 0, "note": "No page code found: no markup in the code folder."}
    found = await asyncio.to_thread(scan, files)
    folders = sorted({rel.split("/")[0] for rel in files if "/" in rel})[:20]
    return {
        "files_read": len(files),
        "folders": folders,
        **found,
        "note": (
            "Candidates from the code, not the page: look at the page (look_at_page) for the "
            "names it really shows, and cite file and line as each entry's evidence."
        ),
    }


# Looking and trying


def _url(value: Any) -> str:
    """A page address as a map says it: ``{copy_page}`` then a path; a bare path is put after it.

    :raises ValueError: Anything else.
    """
    url = str(value or "/").strip()
    if url.startswith("/"):
        url = "{copy_page}" + url
    if not url.startswith("{copy_page}"):
        raise ValueError("url: a path on the copy's page, like /chat")
    return url


async def look_at(runs: Runs, session: dict[str, Any], args: dict[str, Any]) -> Any:
    """The ``look_at_page`` result: where the page landed and its accessibility snapshot."""
    target = str(session["target"])
    url = _url(args.get("url"))
    sign_in = args.get("sign_in")
    from evalution.runs.page.learn import scaffold

    try:
        scaffold(url, sign_in)
    except ConfigError as exc:
        raise ValueError(str(exc).replace("page.", "", 1)) from exc
    capabilities = _on_copy(runs, target)
    async with _claimed(runs):
        found = await look(runs.targets.config, target, capabilities, url, sign_in)
    return {
        **found,
        "note": "What the page showed, by role and name. Nothing was clicked or typed.",
    }


def _map_of(work: dict[str, Any], given: Any) -> PageMap:
    """The map to try: *given*, else this session's latest proposal.

    :raises ValueError: Neither, or one that doesn't read.
    """
    if given is None:
        latest = work["proposals"].get(work.get("latest") or "")
        if latest is None:
            raise ValueError("map: give the whole map to try an entry of, or propose one first")
        given = latest["map"]
    try:
        return read_map(given, "map")
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc


async def try_entry(runs: Runs, session: dict[str, Any], args: dict[str, Any]) -> Any:
    """The ``try_page_entry`` result: found or not, and what changed on the page."""
    from evalution.runs.page.target import TRIED

    target = str(session["target"])
    work = state(session)
    entry = args.get("entry")
    if entry not in TRIED:
        raise ValueError(f"entry: one of {', '.join(sorted(TRIED))}")
    page = _map_of(work, args.get("map"))
    capabilities = _on_copy(runs, target)
    async with _claimed(runs):
        found = await try_on_copy(runs.targets.config, target, capabilities, page, str(entry))
    if found.get("found"):
        work["found"][entry] = {"at": time.time(), "value": entry_of(page, str(entry))}
    return {
        **found,
        "note": "Only looked: Send was never clicked and nothing but a read left the page.",
    }


# Proposing


def _evidence(
    targets: Targets, target: str, block: dict[str, Any], work: dict[str, Any], page: PageMap
) -> list[str]:
    """Check each entry's evidence and fill in when this session's tries found it; what's wrong."""
    code = targets.code(target)
    evidence: dict[str, dict[str, Any]] = block.setdefault("evidence", {})
    problems = []
    for entry, said in list(evidence.items()):
        said.pop("tried", None)
        if "file" not in said:
            continue
        if code is None:
            problems.append(f"{entry}: {target} has no code folder, so no file can be cited")
            continue
        try:
            text = code.read(str(said["file"]))
        except CodeError as exc:
            problems.append(f"{entry}: {exc}")
            continue
        line = int(said.get("line") or 1)
        if line > text.count("\n") + 1:
            problems.append(f"{entry}: {said['file']} has no line {line}")
    for entry, found in work["found"].items():
        if entry in ENTRIES and found["value"] == entry_of(page, entry):
            evidence.setdefault(entry, {})["tried"] = round(found["at"], 3)
    for entry in CORE:
        said = evidence.get(entry) or {}
        if "file" not in said and "tried" not in said:
            problems.append(
                f"{entry}: nothing shows it. Cite the file and line it comes from "
                "(read_page_code), or find it with try_page_entry first."
            )
    for entry in list(evidence):
        if not evidence[entry]:
            del evidence[entry]
    return problems


async def propose(targets: Targets, session: dict[str, Any], args: dict[str, Any]) -> Any:
    """The ``propose_page_map`` result: a draft kept on the session, or why not."""
    target = str(session["target"])
    work = state(session)
    if work["failed"] > MAX_REVISIONS:
        return (
            f"Refused: {work['failed']} rehearsals failed in this session, so no more maps are "
            "proposed. Ask the admin what to do, telling them in plain words what showed:\n"
            + _tried(work)
        )
    try:
        page = read_map(args.get("map"), "map")
    except ConfigError as exc:
        return f"Refused, nothing proposed. The map doesn't read: {exc}"
    block = {**as_block(page), "status": "draft"}
    problems = await asyncio.to_thread(_evidence, targets, target, block, work, page)
    if problems:
        return "Refused, nothing proposed. Fix these and propose again:\n- " + "\n- ".join(problems)
    page = read_map(block, "map")
    pid = f"g{secrets.token_hex(4)}"
    version = len(work["proposals"]) + 1
    why = args.get("why") if isinstance(args.get("why"), str) else ""
    work["proposals"][pid] = {
        "id": pid,
        "version": version,
        "status": "draft",
        "map": block,
        "described": described(page),
        "why": why.strip(),
        "revision": work["failed"] > 0,
        "rehearsed": None,
    }
    work["latest"] = pid
    return {
        "proposal": pid,
        "version": version,
        "status": "draft",
        "described": described(page),
        "next": (
            f"Show it to the admin with reply(text=..., page_map={pid!r}), in plain words, and "
            "end your turn. Trying it sends one real message, so it runs only once they approve."
        ),
    }


def shown(session: dict[str, Any], pid: str) -> tuple[dict[str, Any], str]:
    """Proposal *pid* as the admin is shown it: the data on the message, and the map in words.

    :raises LookupError: No such proposal in this session.
    """
    proposal = state(session)["proposals"].get(pid)
    if proposal is None:
        raise LookupError(f"no page map proposal {pid}: propose it with propose_page_map")
    evidence = proposal["map"].get("evidence") or {}
    rows = [
        {
            "entry": entry,
            "file": said.get("file"),
            "line": said.get("line"),
            "tried": said.get("tried") is not None,
        }
        for entry, said in evidence.items()
    ]
    lines = "\n".join(f"- {row['what']}: {row['where']}" for row in proposal["described"])
    words = f"How tests would reach the page:\n{lines}"
    data = {
        "id": pid,
        "version": proposal["version"],
        "status": proposal["status"],
        "described": proposal["described"],
        "evidence": rows,
        "target": str(session["target"]),
    }
    return data, words


# Rehearsing


def _tried(work: dict[str, Any]) -> str:
    """What each failed rehearsal showed, in plain words, for asking the admin."""
    return "\n".join(
        f"- Map {done['version']}: {done['said']}"
        for done in work["rehearsals"]
        if not done["passed"]
    )


async def rehearse(runs: Runs, session: dict[str, Any], args: dict[str, Any]) -> Any:
    """The ``rehearse_page_map`` result: what the page showed of one real message, or why it
    didn't run."""
    target = str(session["target"])
    work = state(session)
    if work["failed"] > MAX_REVISIONS:
        return (
            f"Refused: {work['failed']} rehearsals failed in this session; no more are run. Ask "
            "the admin what to do, telling them in plain words what showed and what you tried:\n"
            + _tried(work)
        )
    pid = str(args.get("plan_id") or work.get("latest") or "")
    proposal = work["proposals"].get(pid)
    if proposal is None:
        return "Refused: no page map proposal to rehearse. Propose one with propose_page_map."
    asked = (session.get("plans") or {}).get(pid)
    if asked is None or not asked.get("approved"):
        return (
            f"Refused: page map {pid} is not approved. It sends one real message, so show it with "
            f"reply(page_map={pid!r}) and end your turn; rehearse it only once the admin approves."
        )
    if proposal["rehearsed"]:
        return f"Refused: page map {pid} was rehearsed already. Propose a revision to try again."
    page = read_map(proposal["map"], "map")
    capabilities = _on_copy(runs, target)
    async with _claimed(runs):
        try:
            done = await rehearse_map(
                runs.targets.config, target, capabilities, page, runs.targets.files
            )
        except Exception as exc:  # the rehearsal couldn't run: no verdict on the map
            return f"Error: the rehearsal didn't finish: {str(exc)[:500]}. Tell the admin."
    said = done.said()
    work["rehearsals"].append(
        {"version": proposal["version"], "passed": done.passed, "said": said, "shots": done.shots}
    )
    proposal["rehearsed"] = "tried" if done.passed else "failed"
    proposal["status"] = proposal["rehearsed"]
    if done.passed:
        block = proposal["map"]
        block["status"] = "tried"
        evidence = block.setdefault("evidence", {})
        for entry in PROVEN:
            if entry_of(page, entry) is not None or entry in ("url", "sign_in"):
                evidence.setdefault(entry, {})["tried"] = round(time.time(), 3)
        code = runs.targets.code(target)
        kept = KeptMap(
            target,
            block,
            status="tried",
            evidence_hashes=pagemaps.hashes_of(block, code.root.resolve()) if code else {},
            report=said,
            shots=done.shots,
            shots_at=done.at,
        )
        await asyncio.to_thread(pagemaps.store(runs.targets).propose, kept)
        return {
            "status": "tried",
            "said": said,
            "screenshots": len(done.shots),
            "next": (
                "Tell the admin in plain words what the page showed. They approve the map once, "
                f"under How tests reach {target} on the Test environment page; until then tests "
                "reach it through its API."
            ),
        }
    work["failed"] += 1
    left = max(0, MAX_REVISIONS + 1 - work["failed"])
    return {
        "status": "failed",
        "said": said,
        "failed_rehearsals": work["failed"],
        "revisions_left": left,
        "next": (
            "Look at what showed (look_at_page, try_page_entry), propose a revision that fixes "
            "it, and show it to the admin."
            if left
            else "No revisions are left in this session. Ask the admin what to do, telling them "
            "in plain words what showed and what you tried:\n" + _tried(work)
        ),
    }
