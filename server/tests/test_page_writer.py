"""The test writer learns a target's page map through typed tools, with a scripted model and no
browser: every tool is refused without evalution's own copy, a proposal must read as a map and
rest on evidence, a rehearsal needs the admin's approval and stops after three failed revisions, a
map that worked waits for one approval, which saves it into the target's page: block (and can be
undone), and a commit under its evidence marks it stale for the next run to check.

What the tools do in a real browser is in ``test_page_learn.py``; a run that checks a stale map
on the made-up app's page is in ``acceptance/test_page_runs.py``."""

import json
import subprocess
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from evalution import llm
from evalution.api.app import create_app
from evalution.config import ModelConfig, load, page_targets, save_page_target
from evalution.connect import page as page_connect
from evalution.project import pagemaps
from evalution.project.pagemaps import KeptMap, PageMapStore
from evalution.runs.page.learn import Rehearsed
from evalution.runs.page.map import page_map_of
from evalution.runs.runner import Runs
from evalution.storage import Database
from evalution.storage.files import LocalFiles
from evalution.suite.store import save_set
from evalution.targets import Targets
from evalution.writer import paging
from evalution.writer.agent import Agent
from evalution.writer.sessions import SessionStore
from evalution.writer.tools import AgentTurn

#: The page's code: a chat page, as a frontend writes it.
CHAT = """export function Chat() {
  return (
    <main>
      <div role="log" aria-label="Messages" aria-busy={busy}></div>
      <form>
        <textarea aria-label="Message" />
        <button type="submit">Send</button>
        <button type="button">New chat</button>
      </form>
    </main>
  );
}
"""
ROUTES = 'const routes = [{ path: "/chat/:id", element: <Chat /> }];\n'
MAP = {
    "url": "{copy_page}/chat",
    "ready": {"role": "textbox", "name": "Message"},
    "open_session": {"url": "{copy_page}/chat/{session}"},
    "actions": {
        "type": {"fill": {"role": "textbox", "name": "Message"}},
        "send": {"click": {"role": "button", "name": "Send"}},
    },
    "replies": {"in": {"role": "log", "name": "Messages"}},
    "busy": [{"attr": "aria-busy", "equals": "true"}],
    "evidence": {
        "ready": {"file": "web/Chat.tsx", "line": 6},
        "actions.type": {"file": "web/Chat.tsx", "line": 6},
        "actions.send": {"file": "web/Chat.tsx", "line": 7},
        "replies.in": {"file": "web/Chat.tsx", "line": 4},
    },
}


def _git(root, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _agent(tmp_path, model: llm.LLM | None = None) -> Agent:
    """A target connected from the page (so its block can be written), with its code in git."""
    repo = tmp_path / "app"
    (repo / "web").mkdir(parents=True)
    (repo / "web" / "Chat.tsx").write_text(CHAT)
    (repo / "web" / "routes.tsx").write_text(ROUTES)
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "add", ".")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "chat")
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"web_dir": "none"}))
    docs = Database.open(tmp_path / "data").docs()
    block = {"adapter": "http", "http": {"send": {"url": "x"}}, "code": {"path": str(repo)}}
    save_page_target(docs, "bot", block)
    config = load(tmp_path / "evalution.yaml")
    runs = Runs(Targets(config), model)
    save_set(runs.targets.docs, "bot", "main", {"name": "Main"})
    return Agent(runs, SessionStore(runs.targets.docs), model)


@pytest.fixture
async def agent(tmp_path):
    made = _agent(tmp_path)
    yield made
    for task in list(made._tasks.values()):
        await task


class FakeCopy:
    """The copy's page as the tools reach it, without a browser: what each was asked, and a
    rehearsal that fails *failures* times, then shows its reply."""

    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.looked: list[tuple[str, Any]] = []
        self.tried: list[str] = []
        self.rehearsed: list[dict] = []

    async def look(self, config, target, capabilities, url, sign_in) -> dict:
        self.looked.append((url, sign_in))
        return {"at": "/chat", "snapshot": '- textbox "Message"\n- button "Send"', "blocked": []}

    async def try_on_copy(self, config, target, capabilities, page, entry) -> dict:
        self.tried.append(entry)
        return {"entry": entry, "found": True, "blocked": []}

    async def rehearse_map(self, config, target, capabilities, page, files) -> Rehearsed:
        self.rehearsed.append(page.model_dump(by_alias=True))
        if len(self.rehearsed) <= self.failures:
            return Rehearsed(passed=False, reply="Hi!", instead="Couldn't load this reply")
        return Rehearsed(
            passed=True,
            reply="Hi! How can I help?",
            shown="hi! how can i help?",
            shots=["turn-1.png"],
            at={"run": "page-map-bot", "case": "rehearsal", "n": 1},
        )


@pytest.fixture
def copy(monkeypatch) -> FakeCopy:
    """A copy the tools can reach: the target counts as having a proven plan."""
    fake = FakeCopy()
    monkeypatch.setattr(paging, "refusal", lambda config, target, capabilities: None)
    monkeypatch.setattr(paging, "look", fake.look)
    monkeypatch.setattr(paging, "try_on_copy", fake.try_on_copy)
    monkeypatch.setattr(paging, "rehearse_map", fake.rehearse_map)
    return fake


async def _session(agent: Agent) -> dict:
    return await agent._session((await agent.create("bot", "main"))["id"])


async def _shown_and_approved(agent: Agent, session: dict, pid: str) -> dict:
    """Proposal *pid* shown and approved on the page: the session as it is now."""
    await agent.tools.reply(AgentTurn(session), {"text": "This map.", "page_map": pid})
    await agent.approve(session["id"], pid)
    await agent._tasks[session["id"]]
    return await agent._session(session["id"])


async def test_every_page_tool_is_refused_without_evalutions_own_copy(agent) -> None:
    session = await _session(agent)
    turn = AgentTurn(session)
    looked = await agent.tools.look_at_page(turn, {"url": "/chat"})
    tried = await agent.tools.try_page_entry(turn, {"entry": "actions.send", "map": MAP})
    assert looked.startswith("Refused: bot's page is only ever opened on evalution's own copy")
    assert "it has no isolation plan yet" in looked and "Test environment" in looked
    assert tried.startswith("Refused: bot's page is only ever opened on evalution's own copy")
    found = await agent.tools.propose_page_map(turn, {"map": MAP})
    pid = found["proposal"]
    session = await _shown_and_approved(agent, session, pid)
    rehearsed = await agent.tools.rehearse_page_map(AgentTurn(session), {"plan_id": pid})
    assert rehearsed.startswith("Refused: bot's page is only ever opened on evalution's own copy")
    assert PageMapStore(agent.targets.config.data_dir).proposed("bot") is None


async def test_the_code_read_finds_each_entry_with_its_file_and_line(agent) -> None:
    found = await agent.tools.read_page_code(AgentTurn(await _session(agent)), {})
    candidates = found["candidates"]
    assert found["files_read"] == 2
    send = candidates["actions.send"][0]
    assert (send["file"], send["line"]) == ("web/Chat.tsx", 7)
    assert send["suggest"] == {"role": "button", "name": "Send"}
    assert candidates["actions.type"][0]["suggest"] == {"role": "textbox", "name": "Message"}
    assert candidates["actions.new_chat"][0]["suggest"] == {"role": "button", "name": "New chat"}
    assert {"role": "log", "name": "Messages"} in [
        c.get("suggest") for c in candidates["replies.in"]
    ]
    assert {"attr": "aria-busy", "equals": "true"} in [c.get("suggest") for c in candidates["busy"]]
    assert {"path": "/chat/:id", "file": "web/routes.tsx", "line": 1, "session": True} in found[
        "routes"
    ]


async def test_a_proposal_must_read_as_a_map_and_rest_on_evidence(agent, copy) -> None:
    turn = AgentTurn(await _session(agent))

    async def propose(**changes: Any) -> Any:
        return await agent.tools.propose_page_map(turn, {"map": {**MAP, **changes}})

    css = await propose(ready={"css": "#message"})
    assert css.startswith("Refused, nothing proposed.") and "CSS selectors aren't allowed" in css
    away = await propose(url="https://example.com/chat")
    assert "must start with {copy_page}" in away
    unsupported = await propose(evidence={"ready": MAP["evidence"]["ready"]})
    assert "actions.send: nothing shows it" in unsupported
    assert "replies.in: nothing shows it" in unsupported
    nowhere = await propose(
        evidence={**MAP["evidence"], "actions.send": {"file": "web/Chat.tsx", "line": 99}}
    )
    assert "web/Chat.tsx has no line 99" in nowhere
    assert paging.state(turn.session)["proposals"] == {}

    # A try this session that found an entry is evidence too, for that same element only.
    await agent.tools.try_page_entry(turn, {"entry": "actions.send", "map": MAP})
    evidence = {k: v for k, v in MAP["evidence"].items() if k != "actions.send"}
    found = await propose(evidence=evidence)
    assert found["status"] == "draft"
    kept = paging.state(turn.session)["proposals"][found["proposal"]]["map"]
    assert kept["evidence"]["actions.send"]["tried"] > 0
    renamed = {"click": {"role": "button", "name": "Ask"}}
    other = await propose(evidence=evidence, actions={**MAP["actions"], "send": renamed})
    assert "actions.send: nothing shows it" in other
    assert {"what": "Send", "where": "button named “Send”"} in found["described"]


async def test_rehearsing_needs_approval_and_a_map_that_worked_waits_for_one(agent, copy) -> None:
    session = await _session(agent)
    turn = AgentTurn(session)
    looked = await agent.tools.look_at_page(
        turn, {"url": "/chat", "sign_in": {"headers": {"X-Forwarded-Email": "{person.email}"}}}
    )
    assert 'button "Send"' in looked["snapshot"]
    assert copy.looked == [
        ("{copy_page}/chat", {"headers": {"X-Forwarded-Email": "{person.email}"}})
    ]
    found = await agent.tools.propose_page_map(turn, {"map": MAP, "why": "From Chat.tsx."})
    pid = found["proposal"]
    early = await agent.tools.rehearse_page_map(turn, {"plan_id": pid})
    assert early.startswith(f"Refused: page map {pid} is not approved")
    assert copy.rehearsed == []

    shown = await agent.tools.reply(turn, {"text": "Here is how.", "page_map": pid})
    assert "End your turn" in shown
    said = session["messages"][-1]
    assert said["page_map"]["id"] == pid and said["page_map"]["status"] == "draft"
    assert "How tests would reach the page:\n- Opens: /chat" in said["text"]
    session = await _shown_and_approved(agent, session, pid)
    asked = [m for m in session["messages"] if m["role"] == "admin"][-1]
    assert asked["text"] == "Try this page map with one message" and asked["approves"] == pid

    done = await agent.tools.rehearse_page_map(AgentTurn(session), {"plan_id": pid})
    assert done["status"] == "tried" and done["screenshots"] == 1
    assert "Test environment" in done["next"]
    waiting = PageMapStore(agent.targets.config.data_dir).proposed("bot")
    assert waiting is not None and waiting.status == "tried" and waiting.shots == ["turn-1.png"]
    assert waiting.page["status"] == "tried"
    assert waiting.page["evidence"]["actions.send"]["tried"] > 0
    assert set(waiting.evidence_hashes) == {"web/Chat.tsx"}
    again = await agent.tools.rehearse_page_map(AgentTurn(session), {"plan_id": pid})
    assert "was rehearsed already" in again
    # Nothing is saved into the target until the admin approves it.
    assert "page" not in agent.targets.conf("bot").options


async def test_after_three_failed_revisions_the_admin_is_asked(agent, copy) -> None:
    copy.failures = 99
    session = await _session(agent)
    for n in range(1, 5):
        found = await agent.tools.propose_page_map(AgentTurn(session), {"map": MAP})
        session = await _shown_and_approved(agent, session, found["proposal"])
        done = await agent.tools.rehearse_page_map(AgentTurn(session), {})
        assert done["status"] == "failed" and done["failed_rehearsals"] == n
        assert "Couldn't load this reply" in done["said"]
    assert done["revisions_left"] == 0 and "Ask the admin" in done["next"]
    refused = await agent.tools.propose_page_map(AgentTurn(session), {"map": MAP})
    assert refused.startswith("Refused: 4 rehearsals failed") and "Map 2:" in refused
    assert len(copy.rehearsed) == 4
    assert PageMapStore(agent.targets.config.data_dir).proposed("bot") is None


Step = tuple[str, dict] | str | Callable[[dict], Any]


def _model(script: list[Step], sessions: dict[str, dict]) -> llm.LLM:
    """A model whose every writer step is the next of *script*."""
    count = [0]

    def answer(request: httpx.Request) -> httpx.Response:
        said = script.pop(0)
        if callable(said):
            said = said(next(iter(sessions.values())))
        count[0] += 1
        blocks = (
            [{"type": "text", "text": said}]
            if isinstance(said, str)
            else [{"type": "tool_use", "id": f"t{count[0]}", "name": said[0], "input": said[1]}]
        )
        usage = {"input_tokens": 10, "output_tokens": 5}
        return httpx.Response(200, json={"content": blocks, "usage": usage, "model": "m"})

    config = ModelConfig(
        kind="anthropic", base_url="http://model", api_key_env=None, models={"strong": "m"}
    )
    return llm.LLM(config, transport=httpx.MockTransport(answer))


def _latest(session: dict) -> str:
    return paging.state(session)["latest"]


async def test_the_loop_learns_the_page_and_the_admin_approves_it_once(tmp_path, copy) -> None:
    script: list[Step] = []
    agent = _agent(tmp_path)
    agent.llm = _model(script, agent._live)
    sid = (await agent.create("bot", "main"))["id"]
    script += [
        ("read_page_code", {}),
        ("look_at_page", {"url": "/chat"}),
        ("try_page_entry", {"entry": "actions.send", "map": MAP}),
        ("propose_page_map", {"map": MAP}),
        ("rehearse_page_map", {}),
        lambda s: ("reply", {"text": "This is how tests reach it.", "page_map": _latest(s)}),
        "Waiting for the admin.",
    ]
    await agent.message(sid, "Learn the page.")
    await agent._tasks[sid]
    session = await agent._session(sid)
    results = [m["content"] for m in session["history"] if m["role"] == "tool"]
    assert "web/Chat.tsx" in results[0] and "Refused" not in results[1]
    assert '"clicked": false' not in results[2], "the fake tries without a browser"
    assert '"status": "draft"' in results[3]
    assert "not approved" in results[4], "rehearsing waits for the admin"
    assert copy.tried == ["actions.send"] and copy.rehearsed == []

    script += [lambda s: ("rehearse_page_map", {"plan_id": _latest(s)}), "It showed its reply."]
    await agent.approve(sid, _latest(session))
    await agent._tasks[sid]
    session = await agent._session(sid)
    assert '"status": "tried"' in session["history"][-2]["content"]
    assert len(copy.rehearsed) == 1

    # The admin approves it once: saved into the target's page: block.
    targets = agent.targets
    page_connect.approve(targets, "bot")
    await targets.reload("bot")
    saved = targets.conf("bot").options["page"]
    assert saved["status"] == "approved" and saved["url"] == "{copy_page}/chat"
    assert page_map_of(targets.conf("bot")).status == "approved"
    assert page_targets(targets.docs)["bot"]["page"] == saved
    kept = PageMapStore(targets.config.data_dir)
    assert kept.proposed("bot") is None and kept.load("bot").status == "approved"
    view = page_connect.view(targets, "bot")
    assert view["plays"] == "page" and view["current"]["status"] == "approved"
    assert view["current"]["shots"] == ["/v1/targets/bot/page/shots/turn-1.png"]
    assert {"what": "Send", "where": "button named “Send”"} in view["current"]["map"]
    with pytest.raises(LookupError):
        page_connect.approve(targets, "bot")

    # Undo goes back to before: tests reach the app through its API again.
    page_connect.undo(targets, "bot")
    await targets.reload("bot")
    assert "page" not in targets.conf("bot").options
    assert page_connect.view(targets, "bot")["plays"] == "api"


async def test_a_target_set_in_the_config_file_is_never_written(tmp_path) -> None:
    block = {"adapter": "http", "http": {"send": {"url": "x"}}}
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {"bot": block}}))
    targets = Targets(load(tmp_path / "evalution.yaml"))
    PageMapStore(targets.config.data_dir).propose(KeptMap("bot", {**MAP, "status": "tried"}))
    with pytest.raises(page_connect.PageMapError, match="evalution never writes"):
        page_connect.approve(targets, "bot")
    view = page_connect.view(targets, "bot")
    assert view["proposed"]["status"] == "tried" and not view["managed"]


async def test_a_commit_under_its_evidence_marks_the_map_stale(agent, copy) -> None:
    targets = agent.targets
    kept_maps = PageMapStore(targets.config.data_dir)
    root = targets.code("bot").root
    tried = {**MAP, "status": "tried"}
    hashes = pagemaps.hashes_of(tried, root.resolve())
    kept_maps.propose(KeptMap("bot", tried, evidence_hashes=hashes))
    page_connect.approve(targets, "bot")
    await targets.reload("bot")
    assert pagemaps.mark_stale(targets, "bot") == []
    assert pagemaps.to_check(targets, "bot") is None

    # Edited, not committed: nothing is marked, but a run checks it before it plays.
    chat = root / "web" / "Chat.tsx"
    chat.write_text(CHAT.replace(">Send<", ">Ask<"))
    assert pagemaps.mark_stale(targets, "bot") == []
    assert pagemaps.brief(targets, "bot") is None
    assert pagemaps.to_check(targets, "bot") is not None

    _git(root, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qam", "rename send")
    assert pagemaps.mark_stale(targets, "bot") == ["web/Chat.tsx"]
    assert pagemaps.brief(targets, "bot") == {
        "status": "stale",
        "stale_files": ["web/Chat.tsx"],
        "missing": [],
        "shot": None,
    }
    view = page_connect.view(targets, "bot")
    assert (
        view["current"]["status"] == "stale"
        and "a commit changed web/Chat.tsx" in (view["current"]["report"])
    )

    # The run's check finds what's missing: broken, saying what, for Home to show.
    found = pagemaps.to_check(targets, "bot")
    at = {"run": "page-map-bot", "case": "check", "n": 1}
    pagemaps.checked(targets, "bot", found, ["button 'Send'"], "page-map-check.png", at)
    said = pagemaps.brief(targets, "bot")
    assert said["missing"] == ["button 'Send'"] and said["shot"] == "page-map-check.png"
    shown = page_connect.view(targets, "bot")["current"]
    assert shown["missing_shot"] == "/v1/targets/bot/page/shots/page-map-check.png"
    assert pagemaps.to_check(targets, "bot") is not None, "a broken map is checked again"
    # Undo still goes back version by version.
    assert [k.status for _, k in kept_maps.history("bot")] == ["approved", "stale", "broken"]
    # A try that meets the same says so too, with its own screenshot.
    tried = {"run": "r1", "case": "c", "n": 1}
    again = pagemaps.found_broken(targets, "bot", ["button 'Send'"], "turn-1.png", tried)
    assert again is not None and again.missing_shot_at == tried
    assert "A run found the page without" in again.report

    # A block edited by hand since is the person's: nothing here follows it.
    edited = {**targets.conf("bot").options["page"], "catch_up_seconds": 20}
    block = {**page_targets(targets.docs)["bot"], "page": edited}
    save_page_target(targets.docs, "bot", block)
    await targets.reload("bot")
    assert pagemaps.in_use(targets, "bot") is None and pagemaps.brief(targets, "bot") is None
    assert page_connect.view(targets, "bot")["current"]["by_hand"] is True
    assert pagemaps.found_broken(targets, "bot", ["button 'Send'"]) is None


def test_the_page_approves_a_tried_map_shows_its_screenshot_and_keeps_it(tmp_path) -> None:
    """Through the routes: the map waiting for approval and its screenshot, one approval that
    saves the block, an edit of the target from the form that keeps it, and undo."""
    _agent(tmp_path)
    data = tmp_path / "data"
    LocalFiles(data).put("page-map-bot", "rehearsal", 7, "turn-1.png", b"\x89PNG-shot")
    at = {"run": "page-map-bot", "case": "rehearsal", "n": 7}
    waiting = KeptMap("bot", {**MAP, "status": "tried"}, shots=["turn-1.png"], shots_at=at)
    PageMapStore(data).propose(waiting)
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        view = client.get("/v1/targets/bot/page").json()
        assert view["plays"] == "api" and view["proposed"]["status"] == "tried"
        assert view["can_learn"] is False and "own copy" in view["why_not"]
        shot = client.get(view["proposed"]["shots"][0])
        assert shot.status_code == 200 and shot.content == b"\x89PNG-shot"
        assert client.get("/v1/targets/bot/page/shots/other.png").status_code == 404

        approved = client.post("/v1/targets/bot/page/approve").json()
        assert approved["plays"] == "page" and approved["current"]["status"] == "approved"
        assert approved["proposed"] is None and approved["can_undo"] is True
        assert client.post("/v1/targets/bot/page/approve").status_code == 409
        assert client.get("/v1/targets/bot/health").json()["page"] is None

        # Editing the target in the form keeps its page map.
        send = {"url": "http://bot.example.test/c", "body": {"m": "{message}"}}
        options = {"send": send, "reply": "$", "done": "response"}
        body = {"name": "bot", "adapter": "http", "options": options}
        edited = client.put("/v1/targets/bot", json=body)
        assert edited.status_code == 200, edited.text
        assert client.get("/v1/targets/bot/page").json()["current"]["status"] == "approved"

        back = client.post("/v1/targets/bot/page/undo").json()
        assert back["plays"] == "api" and back["current"] is None


def test_a_kept_map_reads_back() -> None:
    kept = KeptMap("bot", MAP, status="approved", shots=["turn-1.png"])
    assert KeptMap.from_json(kept.to_json()) == kept
    with pytest.raises(ValueError, match="newer"):
        KeptMap.from_json(json.dumps({"version": 99, "target": "bot", "page": {}}))
