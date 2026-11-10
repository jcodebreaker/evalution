"""UI checks read on a real page (``page.shows``, ``page.hides``): after each turn's reply settles
the page target counts each element under the turn's reply, as the page map finds replies, or on
the whole page, and keeps what was there instead when it is missing. The readings go into the
try's page record, and code judges the checks from them.

Skipped without the page extra or a browser, as ``test_page_target.py`` is."""

import json
import time

import pytest

pytest.importorskip(
    "playwright", reason="page mode's tests need the page extra: uv sync --extra page"
)

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from test_page_target import Served

from evalution.core.evidence import Clipped, TryEvidence, TurnRecord
from evalution.core.judgement import Outcome, Role
from evalution.judging.checks import element_checks
from evalution.runs.page.map import PageMap
from evalution.runs.page.target import PageTarget
from evalution.suite.cases import parse_case

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Chat</title></head><body><main>
<div role="log" aria-label="Messages" id="log"></div>
<form id="f"><input aria-label="Message" id="box"><button type="submit">Send</button></form>
<button>Settings</button>
</main><script>
const without = WITHOUT;
let n = 0;
document.getElementById("f").addEventListener("submit", async (event) => {
  event.preventDefault();
  const box = document.getElementById("box"), message = box.value;
  box.value = "";
  const log = document.getElementById("log");
  log.insertAdjacentHTML("beforeend", `<article data-role="person"><p>${message}</p></article>`);
  await fetch("/api/send", {method: "POST", body: JSON.stringify({message})});
  n += 1;
  const rate = without.includes(n)
    ? '<button>Copy</button><button aria-label="Thumbs down">-</button>'
    : '<button aria-label="Thumbs up">+</button><button>Copy</button>';
  log.insertAdjacentHTML(
    "beforeend",
    `<article data-role="assistant"><p>Echo: ${message}</p>${rate}</article>`,
  );
});
</script></body></html>"""

MAP = {
    "url": "{copy_page}/chat/",
    "ready": {"role": "textbox", "name": "Message"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {
        "in": {"role": "log", "name": "Messages"},
        "item": {"role": "article"},
        "mine": {"attr": "data-role", "equals": "assistant"},
    },
    "quiet_ms": 300,
}


def _app(without: list[int]) -> FastAPI:
    app = FastAPI()

    @app.get("/chat/")
    async def chat() -> HTMLResponse:
        return HTMLResponse(PAGE.replace("WITHOUT", json.dumps(without)))

    @app.post("/api/send")
    async def send(request: Request) -> dict[str, bool]:
        await request.body()
        return {"ok": True}

    return app


CASE = {
    "name": "Rates each reply",
    "turns": [{"message": "Make an HTML page about Acme"}, {"message": "And another"}],
    "page": {
        "shows": [
            {"role": "button", "name": "Thumbs up", "within": "reply"},
            {"role": "button", "name": "Settings", "within": "page"},
            {"role": "button", "name": "Copy", "within": "reply", "turn": 2, "count": 1},
        ],
        "hides": [{"text": "Something went wrong", "within": "page"}],
    },
}


async def _played(without: list[int]) -> TryEvidence:
    """The test played through the page, page-only, into a try's evidence."""
    case = parse_case(CASE, "set/case", "case")
    with Served(_app(without)) as served:
        target = PageTarget(None, PageMap.model_validate(MAP), served.url, element_seconds=3)
        target.look_for((*case.shows, *case.hides))
        try:
            session = await target.open("r")
            target.begin(session, None)
            t0 = time.time()
            turns = []
            for turn in case.turns:
                played = await target.send(session, turn.message, deadline=time.time() + 20)
                assert played.error is None, played.error
                turns.append(
                    TurnRecord(
                        kind="message",
                        message=Clipped(text=turn.message),
                        reply=Clipped(text=played.reply),
                        started=played.started,
                        ended=played.ended,
                        shown=Clipped(text=played.shown or ""),
                    )
                )
            record = await target.record(session, t0)
        finally:
            await target.dispose()
    return TryEvidence(case="case", n=1, t0=t0, seconds=1.0, turns=tuple(turns), page=record)


async def test_the_page_target_reads_each_element_under_the_reply_and_on_the_page() -> None:
    evidence = await _played(without=[])
    first = evidence.page.turns[0].elements
    assert len(first) == 4, "each element read once after the turn"
    counts = {
        json.loads(r.key).get("name") or json.loads(r.key).get("text"): r.count for r in first
    }
    assert counts == {"Thumbs up": 1, "Settings": 1, "Copy": 1, "Something went wrong": 0}
    case = parse_case(CASE, "set/case", "case")
    checks = element_checks(case, evidence, Role.GUIDE)
    assert [c.outcome for c in checks] == [Outcome.PASSED] * 4, [c.why for c in checks]
    assert checks[0].why == "found button 'Thumbs up' under the reply of each of turns 1, 2"


async def test_a_reply_without_the_thumbs_up_fails_naming_what_was_there() -> None:
    evidence = await _played(without=[2])
    case = parse_case(CASE, "set/case", "case")
    shows = element_checks(case, evidence, Role.GUIDE)[0]
    assert shows.outcome is Outcome.FAILED
    assert shows.why.startswith("no button named 'Thumbs up' under turn 2's reply; nearest: ")
    assert "button 'Thumbs down'" in shows.why
    # Only the newest reply is under: turn 1's thumbs-up, still on the page, doesn't count.
    reading = next(r for r in evidence.page.turns[1].elements if "Thumbs up" in r.key)
    assert reading.count == 0 and reading.scope
