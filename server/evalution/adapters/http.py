"""The generic chat adapter (ADR 0001, "http"): any chat endpoint, configured in YAML.

.. code-block:: yaml

    http:
      send: {method: POST, url: "http://localhost:3000/chat",
             body: {"message": "{message}", "session": "{session}"}, headers: {}}
      headers_env: {Authorization: MY_BOT_AUTH}   # header values read from env vars
      reply: "$.reply"                # JSON path into the response
      session_header: null            # or a header name that carries the session id
      open: {method: POST, url: "http://localhost:3000/sessions", id: "$.id"}
                                      # optional: the app makes the session, and says its id
      background: false               # true when the app answers first and works on; needs
                                      # done.poll, which waits for the work to end
      done: response                  # or {poll: {url: ".../{session}", reply: "$.reply",
                                      #   until: "$.status == 'idle'", interval: 1,
                                      #   failed_when: "$.status == 'failed'",
                                      #   blocked_when: "$.status == 'waiting'",
                                      #   why: "$.error"}}
      cancel: {method: POST, url: "http://localhost:3000/sessions/{session}/stop"}
                                      # optional: stops the work in flight
      trace_attribute: session.id     # how spans are found for this session
      trace_id: null                  # optional JSON path to a trace id in the response

``{message}``, ``{session}`` and ``{fresh}`` (a new token each try of a case with a ``fresh``
turn, else empty) are replaced in the URL, headers and every string of the body, and so are
``{person_name}``, ``{person_language}`` and ``{person_timezone}``: the test person's, as the
session was opened (empty when not chosen). The target honors the ones its templates use.
``{url}`` is the target's ``url``, the address evalution points it at: on evalution's own copy of
the app (ADR 0009), the copy's, so a target written with ``{url}/chat`` plays on the copy. On a
copy, a route written in full on the target's own ``url`` (same scheme, host and port) is moved to
the copy's address too; a route on any other address can't be, and the run is refused, naming it
(:func:`_on_copy`).
Each try gets a new session id, ``evalution-<run>-t<try>-<random>``, and every request carries
``X-Evalution-Run`` and ``X-Evalution-Try`` (an app may copy them onto its spans as
``evalution.run`` / ``evalution.try``), so tries may play side by side: no reset, no state checks,
no context sections. Files and Stop are not supported (the turn errors).

Each poll checks ``failed_when``, then ``blocked_when``, then ``until``, all in the same grammar;
``why`` is where the app says why. With ``done.poll`` it also waits on a turn the app's own page
sent (``watch``), by the same polls; with ``done: response`` it can't. ``close`` does nothing, so
what a try leaves in the app stays; ``cancel`` is called only with the session id of a try this run
opened.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from evalution.adapters.base import (
    NO_FILES,
    NO_STOP,
    PERSON_FIELDS,
    AdapterInfo,
    Capabilities,
    Isolation,
    OwnTraffic,
    Session,
    Target,
    Turn,
    failure_of,
    lost_connection,
    may_have_heard,
    part,
)
from evalution.config import ConfigError, TargetConfig
from evalution.core import net
from evalution.core.evidence import Failure, TurnEnd

OPEN_SECONDS = 30.0
#: Every eval session id starts with this, so eval traffic is told apart from real traffic.
SESSION_PREFIX = "evalution-"
#: Sent with every eval message, for an app that copies them onto its spans as
#: ``evalution.run`` / ``evalution.try``.
RUN_HEADER, TRY_HEADER = "X-Evalution-Run", "X-Evalution-Try"
_PATH_PART = re.compile(
    r"\.([A-Za-z_][\w-]*)|\[(-?\d+)\]|\[['\"]([^'\"]+)['\"]\]"
    r"|\[\?([\w.-]+)\s*==\s*['\"]([^'\"]*)['\"]\]"
)
_UNTIL = re.compile(r"^\s*(\$\S*)\s*(==|!=)\s*(.+?)\s*$")
#: An app that answers first and works on: its first answer isn't the end of the turn.
_NEEDS_POLL = "needs done.poll, to wait for the work to end"
#: The app's last answers kept for :meth:`HttpTarget.answers`, and how much of each.
ANSWERS_KEPT, ANSWER_BYTES = 6, 200_000


def json_path(data: Any, path: str) -> Any:
    """The value at *path* in *data*, ``None`` when it is not there: ``$``, ``$.a.b``, ``$['a b']``,
    an index from either end (``$.items[0]``, ``$.items[-1]``) and the items of a list where a field
    has a value (``$.items[?data.role=='assistant'][-1].data.content``)."""
    path = path.strip()
    if not path.startswith("$"):
        raise ValueError(f"{path}: a JSON path starts with $")
    rest, value = path[1:], data
    while rest:
        part = _PATH_PART.match(rest)
        if part is None:
            raise ValueError(f"{path}: cannot read {rest!r}")
        key, index, quoted, field, wanted = part.groups()
        if field is not None:
            items = value if isinstance(value, list) else []
            value = [item for item in items if json_path(item, f"$.{field}") == wanted]
        elif index is not None:
            at = int(index)
            fits = isinstance(value, list) and -len(value) <= at < len(value)
            value = value[at] if fits else None
        else:
            value = value.get(key or quoted) if isinstance(value, dict) else None
        rest = rest[part.end() :]
    return value


def _literal(text: str) -> Any:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    try:
        return json.loads(text)
    except ValueError:
        return text


def until(data: Any, expression: str) -> bool:
    """Whether ``<path> == <literal>`` (or ``!=``) holds for *data*."""
    found = _UNTIL.match(expression)
    if found is None:
        raise ValueError(f"until: {expression!r} is not '<path> == <value>'")
    path, op, literal = found.groups()
    equal = json_path(data, path) == _literal(literal)
    return equal if op == "==" else not equal


def _fill(value: Any, fields: dict[str, str]) -> Any:
    if isinstance(value, str):
        if value in ("{message}", "{session}", "{fresh}"):
            return fields[value[1:-1]]
        for key, text in fields.items():
            value = value.replace("{" + key + "}", text)
        return value
    if isinstance(value, dict):
        return {k: _fill(v, fields) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, fields) for v in value]
    return value


def person_used(*templates: Any) -> frozenset[str]:
    """The test person's fields *templates* name, as ``{person_<field>}`` anywhere in them."""
    text = json.dumps([t for t in templates if t], ensure_ascii=False, default=str)
    return frozenset(key for key in PERSON_FIELDS if "{person_" + key + "}" in text)


def _check_poll(poll: dict[str, Any]) -> None:
    """:raises ValueError: An expression or path of *poll* that doesn't read."""
    for key in ("failed_when", "blocked_when"):
        if poll.get(key):
            try:
                until(None, str(poll[key]))
            except ValueError as exc:
                raise ValueError(f"{key}: {exc}") from exc
    if poll.get("why"):
        json_path(None, str(poll["why"]))


def _text(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _read(response: httpx.Response) -> Any:
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


class HttpTarget(Target):
    """A chat endpoint played over HTTP."""

    capabilities = Capabilities(
        isolation=Isolation.SESSION,
        isolation_note="Each try gets a new session id; what carries over is up to your app.",
        parallel=True,
    )

    def __init__(self, target: TargetConfig, transport: httpx.AsyncBaseTransport | None = None):
        self.name = target.name
        conf = target.options.get("http")
        if not isinstance(conf, dict) or not isinstance(conf.get("send"), dict):
            raise ConfigError(f"targets.{target.name}.http.send: required")
        send = conf["send"]
        if not isinstance(send.get("url"), str):
            raise ConfigError(f"targets.{target.name}.http.send.url: required")
        self.send_conf = send
        self.open_conf = conf.get("open")
        if self.open_conf is not None and not (
            isinstance(self.open_conf, dict)
            and isinstance(self.open_conf.get("url"), str)
            and isinstance(self.open_conf.get("id"), str)
        ):
            raise ConfigError(f"targets.{target.name}.http.open: needs url and id")
        self.cancel_conf = conf.get("cancel")
        if self.cancel_conf is not None and not (
            isinstance(self.cancel_conf, dict) and isinstance(self.cancel_conf.get("url"), str)
        ):
            raise ConfigError(f"targets.{target.name}.http.cancel: needs url")
        done = conf.get("done")
        poll_conf = done.get("poll") if isinstance(done, dict) else None
        self.capabilities = replace(
            self.capabilities,
            background=bool(conf.get("background")),
            cancel=self.cancel_conf is not None,
            person=person_used(send, self.open_conf, poll_conf, self.cancel_conf),
        )
        self.reply_path = str(conf.get("reply") or "$")
        self.session_header = conf.get("session_header")
        self.done = conf.get("done") or "response"
        if self.done != "response" and not (
            isinstance(self.done, dict) and isinstance(self.done.get("poll"), dict)
        ):
            raise ConfigError(f"targets.{target.name}.http.done: 'response' or {{poll: ...}}")
        if self.done != "response":
            poll = self.done["poll"]
            try:
                _check_poll(poll)
            except ValueError as exc:
                raise ConfigError(f"targets.{target.name}.http.done.poll: {exc}") from exc
            # It polls the session until done, so it can wait on a turn the page sent too.
            self.capabilities = replace(
                self.capabilities, blocks=bool(poll.get("blocked_when")), watch=True
            )
        elif self.capabilities.background:
            raise ConfigError(f"targets.{target.name}.http.background: {_NEEDS_POLL}")
        self.trace_attribute = str(conf.get("trace_attribute") or "session.id")
        self.trace_id_path = conf.get("trace_id")
        self.headers_env = dict(conf.get("headers_env") or {})
        self.keys = target.keys
        #: ``{url}``: where evalution points the target, its copy's address on a copy.
        self.url = str(target.options.get("url") or "").rstrip("/")
        self._client = net.client(timeout=60.0, transport=transport)
        self._tries: dict[str, int] = {}
        self._answers: deque[dict[str, Any]] = deque(maxlen=ANSWERS_KEPT)

    def answers(self) -> list[dict[str, Any]]:
        return list(self._answers)

    def _headers(self, extra: Any, fields: dict[str, str]) -> dict[str, str]:
        headers = {str(k): str(v) for k, v in _fill(dict(extra or {}), fields).items()}
        for header, env in self.headers_env.items():
            if value := self.keys.get(env):
                headers[str(header)] = value
        if self.session_header:
            headers[str(self.session_header)] = fields["session"]
        return headers

    def _fields(self, session: Session, message: str = "", fresh: str = "") -> dict[str, str]:
        """What the templates are filled with: the test person's as the session was opened."""
        person = session.extra.get("person") or {}
        fields = {"message": message, "session": session.id, "fresh": fresh, "url": self.url}
        return fields | {f"person_{key}": str(person.get(key) or "") for key in PERSON_FIELDS}

    async def open(self, run: str) -> Session:
        self._tries[run] = self._tries.get(run, 0) + 1
        n = self._tries[run]
        session = f"{SESSION_PREFIX}{run}-t{n}-{secrets.token_hex(2)}"
        extra: dict[str, Any] = {"run": run, "try": n}
        # What the templates use of the test person, kept so every request of the session plays
        # the person it opened as.
        chosen = self.person
        person = {
            key: value
            for key in sorted(self.capabilities.person)
            if chosen is not None and (value := getattr(chosen, key))
        }
        if person:
            extra["person"] = person
        if self.open_conf is not None:
            session = await self._new_session(Session(session, "", session, extra), run, n)
        return Session(session, self.trace_attribute, session, extra)

    async def _new_session(self, made: Session, run: str, n: int) -> str:
        """The id the app gives a session it makes for this try, *made* named as evalution would.

        :raises ValueError: Its answer doesn't hold one at the configured path.
        """
        conf = {"method": "POST", **self.open_conf}
        fields = self._fields(made)
        tags = {RUN_HEADER: run, TRY_HEADER: str(n)}
        data = await self._request(conf, fields, time.time() + OPEN_SECONDS, tags)
        made = json_path(data, str(self.open_conf["id"]))
        if not made:
            raise ValueError(f"http.open: no session id at {self.open_conf['id']}")
        return str(made)

    async def send(
        self,
        session: Session,
        message: str,
        *,
        files: list[Path] = (),  # type: ignore[assignment]
        stop_after: float | None = None,
        deadline: float,
    ) -> Turn:
        started = time.time()
        if files:
            return Turn("", started, started, error=NO_FILES)
        if stop_after:
            return Turn("", started, started, error=NO_STOP)
        fields = self._fields(session, message, str(session.extra.get("fresh") or ""))
        tags = {
            RUN_HEADER: str(session.extra.get("run", "")),
            TRY_HEADER: str(session.extra.get("try", "")),
        }
        return await self._turn(fields, started, deadline, tags)

    async def watch(self, session: Session, since: float, *, deadline: float) -> Turn:
        """The turn the page sent at *since*: ``done.poll`` until it is done, as :meth:`send` waits.
        A session already done at the first poll may still show the turn before, so that end is
        taken only once the session has moved since: out of done, or to another reply. A turn
        with the same reply as the one before, done before the first poll, waits to *deadline*.

        :raises NotImplementedError: The target sets no ``done.poll``.
        """
        if self.done == "response":
            return await super().watch(session, since, deadline=deadline)
        fields = self._fields(session, "", str(session.extra.get("fresh") or ""))
        return await self._turn(fields, since, deadline, None)

    async def _turn(
        self,
        fields: dict[str, str],
        started: float,
        deadline: float,
        tags: dict[str, str] | None,
    ) -> Turn:
        """Send the message, unless *tags* is ``None`` (something else sent it), then read the turn
        as ``done`` says."""
        heard = tags is None
        data: Any = None
        trace_ids: list[str] = []
        try:
            if tags is not None:
                data = await self._request(self.send_conf, fields, deadline, tags)
                heard = True
                trace_ids = self._trace_ids(data)
            ended_as, why = TurnEnd.ANSWERED, None
            if self.done != "response":
                poll = self.done["poll"]
                # Watched, a turn already done may be the one before: wait for it to move.
                data, ended_as = await self._poll(poll, fields, deadline, moved=tags is not None)
                trace_ids += [t for t in self._trace_ids(data) if t not in trace_ids]
                path = str(poll.get("reply") or self.reply_path)
                if ended_as is not TurnEnd.ANSWERED and poll.get("why"):
                    why = _text(json_path(data, str(poll["why"]))) or None
            else:
                path = self.reply_path
            reply = _text(json_path(data, path))
            return Turn(reply, started, time.time(), trace_ids, error=why, ended_as=ended_as)
        except TimeoutError:
            error = "the turn did not finish in time"
            return Turn(
                "", started, time.time(), error=error, failure=Failure.TIMED_OUT, heard=heard
            )
        except (httpx.HTTPError, ValueError) as exc:
            error = net.said(exc)
            return Turn(
                "",
                started,
                time.time(),
                error=error,
                failure=failure_of(exc),
                heard=heard or may_have_heard(exc),
                lost=lost_connection(exc),
            )

    def _trace_ids(self, data: Any) -> list[str]:
        if not self.trace_id_path:
            return []
        found = json_path(data, str(self.trace_id_path))
        return [str(found)] if found else []

    async def _request(
        self,
        conf: dict[str, Any],
        fields: dict[str, str],
        deadline: float,
        tags: dict[str, str] | None = None,
    ) -> Any:
        left = deadline - time.time()
        if left <= 0:
            raise TimeoutError
        response = await self._client.request(
            str(conf.get("method") or "POST").upper(),
            _fill(str(conf["url"]), fields),
            json=_fill(conf["body"], fields) if "body" in conf else None,
            headers={**(tags or {}), **self._headers(conf.get("headers"), fields)},
            timeout=left,
        )
        data = _read(response)
        self._answers.append(
            {
                "route": f"{response.request.method} {response.request.url.path}",
                "status": response.status_code,
                "body": data if len(response.content) <= ANSWER_BYTES else "(too long)",
            }
        )
        response.raise_for_status()
        return data

    async def _poll(
        self, conf: dict[str, Any], fields: dict[str, str], deadline: float, *, moved: bool = True
    ) -> tuple[Any, TurnEnd]:
        """Poll until the work ends, and say how. Not *moved*: an end the first poll already finds
        is taken only once the session has moved since, out of that end or to another reply, as
        it may be the turn before's."""
        conf = {"method": "GET", **conf}
        interval = float(conf.get("interval") or 1.0)
        path = str(conf.get("reply") or self.reply_path)
        first: Any = None
        polls = 0
        while True:
            data = await self._request(conf, fields, deadline)
            ended = self._ended(conf, data)
            polls += 1
            if not moved:
                if polls == 1:
                    first = json_path(data, path)
                moved = ended is None or json_path(data, path) != first
            if moved and ended is not None:
                return data, ended
            if time.time() + interval >= deadline:
                raise TimeoutError
            await asyncio.sleep(interval)

    @staticmethod
    def _ended(conf: dict[str, Any], data: Any) -> TurnEnd | None:
        """How the work ended, as *data* says; ``None`` while it goes on."""
        if conf.get("failed_when") and until(data, str(conf["failed_when"])):
            return TurnEnd.FAILED
        if conf.get("blocked_when") and until(data, str(conf["blocked_when"])):
            return TurnEnd.BLOCKED
        return TurnEnd.ANSWERED if until(data, str(conf.get("until") or "")) else None

    async def cancel(self, session: Session) -> None:
        """Stop the work in flight in this session.

        :raises NotImplementedError: The target sets no ``cancel``.
        """
        if self.cancel_conf is None:
            await super().cancel(session)
            return
        conf = {"method": "POST", **self.cancel_conf}
        fields = self._fields(session)
        tags = {
            RUN_HEADER: str(session.extra.get("run", "")),
            TRY_HEADER: str(session.extra.get("try", "")),
        }
        await self._request(conf, fields, time.time() + OPEN_SECONDS, tags)

    async def close(self, session: Session) -> None:
        return None

    async def dispose(self) -> None:
        await self._client.aclose()


def create(target: TargetConfig) -> HttpTarget:
    return HttpTarget(target)


async def _check(
    client: httpx.AsyncClient, options: dict[str, Any], lookup: Callable[[Any], str | None]
) -> dict[str, Any]:
    http = options.get("http") or {}
    base = str(options.get("url") or "").rstrip("/")
    headers = {
        str(h): v for h, ref in (http.get("headers_env") or {}).items() if (v := lookup(ref))
    }
    if http.get("health"):
        response = await client.get(str(http["health"]).replace("{url}", base), headers=headers)
        ok = response.is_success
        return part(ok, f"Health answered {response.status_code}")
    url = str((http.get("send") or {}).get("url") or "")
    url = url.replace("{url}", base).replace("{session}", "check").replace("{message}", "")
    for key in PERSON_FIELDS:
        url = url.replace("{person_" + key + "}", "")
    # A harmless call: OPTIONS asks what the endpoint allows and sends no message.
    response = await client.options(url, headers=headers)
    ok = response.status_code < 500 and response.status_code not in (401, 403)
    detail = f"Reachable ({response.status_code})" if ok else f"Answered {response.status_code}"
    return part(ok, detail)


def _validate(options: dict[str, Any]) -> None:
    send = options["send"]
    if not isinstance(send, dict) or not isinstance(send.get("url"), str):
        raise ValueError("options.send.url: required")
    done = options["done"]
    poll = done.get("poll") if isinstance(done, dict) else None
    if done != "response" and not (
        isinstance(poll, dict) and isinstance(poll.get("url"), str) and poll.get("until")
    ):
        raise ValueError("options.done: 'response' or {poll: {url, until}}")
    if poll is not None:
        _check_poll(poll)
    elif options.get("background"):
        raise ValueError(f"options.background: {_NEEDS_POLL}")
    cancel = options.get("cancel")
    if cancel is not None and not (isinstance(cancel, dict) and isinstance(cancel.get("url"), str)):
        raise ValueError("options.cancel: needs url")


#: Where the block names an address: ``(route, key)``, ``route`` a path of keys into the block.
_ADDRESSES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("send",), "url"),
    (("open",), "url"),
    (("done", "poll"), "url"),
    (("cancel",), "url"),
    ((), "health"),
)


def _origin(url: str) -> tuple[str, str, int] | None:
    """*url*'s scheme, host and port (the scheme's own when it names none); ``None`` when it
    names no http(s) host."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:  # a port that isn't a number, a placeholder say
        return None
    return parts.scheme, parts.hostname, port or (443 if parts.scheme == "https" else 80)


def _on_copy(options: dict[str, Any], url: str) -> dict[str, Any]:
    """*options* pointed at evalution's copy of the app at *url*: ``url`` set to it, and every
    route written on the target's own ``url`` (same scheme, host and port, never another host)
    moved onto the copy's, its path kept. A route that starts with ``{url}`` needs nothing.

    :raises ValueError: A route is written on another address, or the target has no ``url`` to
        tell its own from another: on the copy it would still reach that address.
    """
    http = options.get("http")
    moved: dict[str, Any] = {**options, "url": url}
    if not isinstance(http, dict):
        return moved
    own = _origin(str(options.get("url") or ""))
    copy = urlsplit(url)
    block = dict(http)
    off: list[str] = []
    for route, key in _ADDRESSES:
        holder = block
        for step in route:
            inner = holder.get(step)
            if not isinstance(inner, dict):
                break
            copied = dict(inner)
            holder[step] = copied
            holder = copied
        else:
            text = holder.get(key)
            if not isinstance(text, str) or text.startswith("{url}"):
                continue
            name = ".".join(("http", *route, key))
            if own is None or _origin(text) != own:
                off.append(f"{name} ({text})")
                continue
            if _origin(url) is None:
                raise ValueError(f"evalution's copy of the app has no address to move {name} onto")
            parts = urlsplit(text)
            rest = text[len(parts.scheme) + 3 + len(parts.netloc) :]
            holder[key] = f"{copy.scheme}://{copy.netloc}{rest}"
    if off:
        one = len(off) == 1
        why = (
            f"not on the target's own address ({options['url']})"
            if own
            else "and the target has no `url` to tell its own address by"
        )
        tail = "" if own else ", and give the target its app's `url`"
        raise ValueError(
            f"{', '.join(off)} {'is' if one else 'are'} written in full, {why}, so on "
            f"evalution's copy of the app {'it' if one else 'they'} would still reach "
            f"{'that address' if one else 'those addresses'}. Start "
            f"{'it' if one else 'each'} with `{{url}}` instead, like `{{url}}/chat`"
            f"{tail}."
        )
    moved["http"] = block
    return moved


def _block(options: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    http = dict(options)
    if "authorization" in secrets:
        http["headers_env"] = {"Authorization": secrets["authorization"]}
    return {"http": http}


def _page_options(block: dict[str, Any]) -> dict[str, Any]:
    return dict(block.get("http") or {})


def _propose(base: str, found: dict[str, Any], answers: dict[str, str]) -> dict[str, Any]:
    """The target for what discovery read of an app's routes (``path``, ``body``, ``reply``,
    ``health``), or was told (``chat``)."""

    def absolute(route: str) -> str:
        """On the app's own address, whatever *route* says: only its owner names another."""
        return base + "/" + route.lstrip("/")

    chat = str(answers.get("chat") or "").strip()
    url = chat if chat.startswith("http") else absolute(chat or str(found.get("path") or "/"))

    options: dict[str, Any] = {
        "send": {"method": "POST", "url": url, "body": found.get("body")},
        "reply": found.get("reply") or "$",
        "done": "response",
    }
    made = found.get("open")
    if isinstance(made, dict) and made.get("path") and made.get("id"):
        options["open"] = {
            "url": absolute(str(made["path"])),
            "id": str(made["id"]),
            **({"body": made["body"]} if made.get("body") else {}),
        }
    poll = found.get("poll")
    if isinstance(poll, dict) and poll.get("path") and poll.get("until"):
        options["done"] = {
            "poll": {
                "url": absolute(str(poll["path"])),
                "until": str(poll["until"]),
                "reply": found.get("reply") or "$",
            }
        }
    if found.get("background"):
        options["background"] = True
    if found.get("health"):
        options["health"] = absolute(str(found["health"]))
    return options


#: No detector: this is what discovery falls back on for any app no other adapter claims.
INFO = AdapterInfo(
    name="http",
    label="HTTP adapter",
    options=(
        "send",
        "reply",
        "done",
        "open",
        "background",
        "cancel",
        "session_header",
        "trace_attribute",
        "trace_id",
        "health",
    ),
    required=("send", "reply", "done"),
    secrets=("authorization",),
    check=_check,
    # ``eval-`` is the older prefix of these session ids.
    own_traffic=OwnTraffic(sessions=(SESSION_PREFIX, "eval-"), attributes=("evalution.run",)),
    validate=_validate,
    block=_block,
    page_options=_page_options,
    propose=_propose,
    on_copy=_on_copy,
)
