"""Keeping what it knows current: the import graph, how a commit reaches claims, the owner's word,
what spends and what does not. Offline: temp git repos and a fake model."""

import asyncio
import json
import subprocess

import pytest
import yaml

from evalution.code import CodeError
from evalution.config import load
from evalution.project import hooks, impact, imports, knowledge, understanding
from evalution.project.profile import read_profile, set_updates, updates
from evalution.runs.runner import Runs
from evalution.targets import Targets


def _git(folder, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def _commit(folder, files, message="change"):
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    _git(folder, "add", "-A")
    _git(folder, "commit", "-qm", message)


def _doc():
    claim = lambda cid, text, ref, kind="code": {  # noqa: E731
        "id": cid,
        "text": text,
        "evidence": [{"kind": kind, "ref": ref}],
    }
    return {
        "one_line": "A bot.",
        "nodes": [
            {
                "id": "agent",
                "kind": "agent",
                "title": "The agent",
                "sub": "Answers",
                "tools": [],
                "evidence": [{"kind": "code", "ref": "tools/a.py"}],
            }
        ],
        "edges": [],
        "paths": [],
        "sections": [
            {
                "id": "can_do",
                "title": "Can do",
                "claims": [
                    claim("direct", "It refunds.", "tools/a.py (run)"),
                    claim("near", "It greets.", "docs/guide.md (Greeting)", "docs"),
                    claim("traced", "It is fast.", "the last 50 asks", "traces"),
                ],
            }
        ],
        "priorities": [],
        "starters": [],
        "step_labels": {},
        "expected_paths": [],
    }


@pytest.fixture
def app(tmp_path, monkeypatch):
    repo = tmp_path / "app"
    repo.mkdir()
    _git(repo, "init", "-q")
    _commit(
        repo,
        {
            "tools/a.py": "from lib.b import run\n",
            "lib/b.py": "def run(): return 1\n",
            "docs/guide.md": "Greets people.\n",
            ".env": "KEY=hush\n",
        },
    )
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    config = load(tmp_path / "evalution.yaml")
    runs = Runs(Targets(config), None)
    understanding.save(runs.targets, "bot", _doc())
    runs.repo = repo
    runs.calls = []
    runs.answer = json.dumps({"claims": []})

    async def model(*, instructions, input_text):
        runs.calls.append(input_text)
        if isinstance(runs.answer, Exception):
            raise runs.answer
        return runs.answer

    monkeypatch.setattr(runs, "caller", lambda _key: model)
    return runs


def _check(runs, **kwargs):
    return asyncio.run(knowledge.refresh(runs, "bot", **kwargs))


def _claim(runs, cid):
    doc = understanding.load(runs.targets, "bot")
    return next((c for s in doc["sections"] for c in s["claims"] if c["id"] == cid), None)


def _state(runs):
    return knowledge.load(runs.targets, "bot")


def _says(*entries, new=()):
    return json.dumps({"claims": list(entries), "new": list(new)})


def _edit_b(runs):
    _commit(runs.repo, {"lib/b.py": "def run(): return 2\n"})


def _edit_a(runs, text="from lib.b import run\nprint('new')\n"):
    _commit(runs.repo, {"tools/a.py": text})


def test_the_graph_reads_each_language_and_never_fails(tmp_path) -> None:
    files = {
        "go.mod": "module example.test/app\n",
        "svc/main.go": 'package main\nimport (\n  "fmt"\n  x "example.test/app/lib"\n)\n',
        "lib/l.go": "package lib\n",
        "py/pkg/__init__.py": "",
        "py/pkg/one.py": "from . import two\nfrom .three import f\nimport pkg.four\nimport os\n",
        "py/pkg/two.py": "def broken(:\n",
        "py/pkg/three.py": "",
        "py/pkg/four.py": "",
        "web/app.tsx": (
            "import a from './a';\n// import b from './b';\nexport * from '../shared/c.js';\n"
            "const d = require('./d');\nimport react from 'react';\nconst e = import('./e');\n"
        ),
        "web/a.ts": "",
        "web/b.ts": "",
        "web/d/index.js": "",
        "web/e.jsx": "",
        "shared/c.ts": "",
        "notes.md": "See [a](web/a.ts) and `lib/l.go` and [out](https://example.test/x.py).\n",
        "run.rb": "require 'x'\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    hashed = {rel: f"h{n}" for n, rel in enumerate(files)}
    edges, cache = imports.graph(tmp_path, hashed, {})
    assert edges["svc/main.go"] == ["lib/l.go"]
    assert edges["py/pkg/one.py"] == ["py/pkg/four.py", "py/pkg/three.py", "py/pkg/two.py"]
    assert edges["web/app.tsx"] == ["shared/c.ts", "web/a.ts", "web/d/index.js", "web/e.jsx"]
    assert edges["notes.md"] == ["lib/l.go", "web/a.ts"]
    assert "py/pkg/two.py" not in edges and "run.rb" not in edges

    # By hash: an unchanged file is not read again.
    (tmp_path / "web/app.tsx").unlink()
    assert imports.graph(tmp_path, hashed, cache)[0] == edges


def test_reach_stops_at_the_bound() -> None:
    edges = {"a": ["b"], "b": ["c"], "c": ["d"], "d": ["a"]}
    assert imports.reach(edges, ["a"]) == {"b", "c"}
    assert imports.reach(edges, ["a"], hops=3) == {"b", "c", "d"}


def test_links_come_from_evidence_without_a_model_and_stay_inside(app) -> None:
    doc = understanding.load(app.targets, "bot")
    doc["sections"][0]["claims"][2]["evidence"] = [
        {"kind": "code", "ref": "../../etc/passwd"},
        {"kind": "code", "ref": ".env"},
        {"kind": "code", "ref": "/etc/hosts and tests/x.py"},
    ]
    understanding.save(app.targets, "bot", doc)
    _check(app)
    linked = _state(app)["links"]
    assert set(linked) == {"claim:direct", "claim:near", "node:agent"}
    assert set(linked["claim:direct"]) == {"tools/a.py"}
    assert not app.calls and not _state(app)["stale"]
    # A partial ref finds the one file of that name the agent read.
    assert knowledge.paths_in("a.py:12", {"tools/a.py": "x"}, ["tools/a.py"]) == ["tools/a.py"]
    assert knowledge.paths_in("a.py", {"tools/a.py": "x"}) == []


def test_manual_marks_for_free_and_update_makes_exactly_one_call(app) -> None:
    _check(app)
    assert updates(app.targets, "bot") == "manual"
    _edit_b(app)
    assert _check(app) == "marked"
    assert _check(app) == "marked"
    stale = _state(app)["stale"]
    assert not app.calls
    assert stale["claim:direct"]["level"] == "indirect"
    assert stale["node:agent"]["level"] == "indirect"
    assert "claim:near" not in stale and "claim:traced" not in stale
    head = app.targets.code("bot").commit()
    assert _state(app)["checked"] != head

    app.answer = _says(
        {
            "id": "claim:direct",
            "verdict": "rewrite",
            "text": "It refunds twice.",
            "evidence": [{"kind": "code", "ref": "lib/b.py (run)"}],
        },
        {"id": "node:agent", "verdict": "still_true"},
    )
    assert _check(app, asked=True) == "updated"
    assert len(app.calls) == 1
    assert "hush" not in app.calls[0] and "It refunds." in app.calls[0]
    claim = _claim(app, "direct")
    assert claim["text"] == "It refunds twice." and claim["updated_from"] == head
    assert _state(app)["checked"] == head and not _state(app)["stale"]
    assert understanding.load(app.targets, "bot")["by"] == "refresh"
    assert "It refunds twice." in read_profile(app.targets, "bot")
    assert set(_state(app)["links"]["claim:direct"]) == {"lib/b.py"}
    # Nothing left to do: another press spends nothing.
    assert _check(app, asked=True) == "idle" and len(app.calls) == 1

    # Home's line, once; and the update can be undone.
    summary = knowledge.summary(app, "bot")
    assert summary["last"]["changed"] == 1 and summary["changes"][0]["kind"] == "changed"
    knowledge.saw(app.targets, "bot")
    assert knowledge.summary(app, "bot")["last"] is None
    knowledge.undo(app.targets, "bot")
    assert _claim(app, "direct")["text"] == "It refunds."
    assert "updated_from" not in _claim(app, "direct")
    assert understanding.load(app.targets, "bot")["by"] == "undo"
    with pytest.raises(LookupError):
        knowledge.undo(app.targets, "bot")


def test_working_tree_churn_marks_nothing(app) -> None:
    set_updates(app.targets, "bot", "auto")
    _check(app)
    (app.repo / "tools/a.py").write_text("changed, not committed\n")
    assert _check(app) == "idle"
    assert not app.calls and not _state(app)["stale"]


def test_auto_reads_a_commit_once_and_a_direct_change_is_direct(app) -> None:
    set_updates(app.targets, "bot", "auto")
    _check(app)
    _edit_a(app)
    app.answer = _says(
        {"id": "claim:direct", "verdict": "still_true"},
        {"id": "node:agent", "verdict": "still_true"},
    )
    assert _check(app) == "checked"
    assert _check(app) == "idle"
    assert len(app.calls) == 1
    assert '"rests": "direct"' in app.calls[0]
    assert understanding.load(app.targets, "bot")["version"] == 1
    # The new hash is on the link: the claim now rests on this text.
    assert (
        _state(app)["links"]["claim:direct"]["tools/a.py"]
        == (app.targets.code("bot").hashes()["tools/a.py"])
    )


def test_turning_auto_on_leaves_the_backlog_behind_the_button(app) -> None:
    _check(app)
    _edit_b(app)
    _check(app)
    asyncio.run(knowledge.choose(app, "bot", "auto"))
    assert _check(app) == "idle" and not app.calls
    assert set(_state(app)["stale"]) == {"claim:direct", "node:agent"}

    # The next commit is read, for what it touches alone.
    _commit(app.repo, {"docs/guide.md": "Waves at people.\n"})
    app.answer = _says({"id": "claim:near", "verdict": "still_true"})
    assert _check(app) == "checked"
    assert len(app.calls) == 1 and "claim:near" in app.calls[0]
    assert "claim:direct" not in app.calls[0]
    assert set(_state(app)["stale"]) == {"claim:direct", "node:agent"}

    # The button covers the backlog, from where it began.
    app.answer = _says(
        {"id": "claim:direct", "verdict": "still_true"},
        {"id": "node:agent", "verdict": "still_true"},
    )
    assert _check(app, asked=True) == "checked"
    assert len(app.calls) == 2 and "return 2" in app.calls[1]
    assert not _state(app)["stale"]


def test_the_owners_word_wins(app) -> None:
    set_updates(app.targets, "bot", "auto")
    understanding.steer(app.targets, "bot", {"claim": "direct", "fix": "It refunds by hand."})
    understanding.steer(app.targets, "bot", {"claim": "near", "verdict": "right"})
    _check(app)
    _commit(app.repo, {"tools/a.py": "print('other')\n", "docs/guide.md": "Bows.\n"})
    cite = [{"kind": "code", "ref": "tools/a.py"}]
    app.answer = _says(
        {"id": "claim:direct", "verdict": "rewrite", "text": "Refunds differ.", "evidence": cite},
        {"id": "claim:near", "verdict": "removed"},
        {"id": "node:agent", "verdict": "still_true"},
    )
    assert _check(app) == "updated"
    assert "It refunds by hand." in app.calls[0]
    assert _claim(app, "direct")["text"] == "It refunds."
    assert _claim(app, "near")["text"] == "It greets."
    assert understanding.load(app.targets, "bot")["version"] == 1
    found = understanding.view(app.targets, app, "bot")["sections"][0]["claims"]
    assert found[0]["flagged"] and found[0]["fix"] == "It refunds by hand." and found[1]["flagged"]
    assert knowledge.summary(app, "bot")["flagged"] == 2

    # Steering it again answers the flag.
    understanding.steer(app.targets, "bot", {"claim": "near", "verdict": "right"})
    assert knowledge.summary(app, "bot")["flagged"] == 1


@pytest.mark.parametrize("answer", [RuntimeError("down"), "no json", '{"claims": []}'])
def test_a_failed_call_changes_nothing_and_is_not_paid_for_again(app, answer) -> None:
    set_updates(app.targets, "bot", "auto")
    _check(app)
    before = _state(app)["checked"]
    version = understanding.load(app.targets, "bot")["version"]
    profile = read_profile(app.targets, "bot")
    _edit_a(app)
    app.answer = answer
    assert _check(app) == "failed"
    assert _check(app) == "failed"
    assert len(app.calls) == 1
    assert _state(app)["checked"] == before and "claim:direct" in _state(app)["stale"]
    assert understanding.load(app.targets, "bot")["version"] == version
    assert read_profile(app.targets, "bot") == profile


def test_a_rewrite_must_cite_a_file_and_new_claims_are_held_to_it(app) -> None:
    set_updates(app.targets, "bot", "auto")
    _check(app)
    _edit_b(app)
    made_up = [{"kind": "code", "ref": "nowhere/else.py"}, {"kind": "traces", "ref": "lib/b.py"}]
    app.answer = _says(
        {"id": "claim:direct", "verdict": "rewrite", "text": "Invented.", "evidence": made_up},
        {
            "id": "node:agent",
            "verdict": "removed",
        },
        new=[
            {
                "section": "can_do",
                "text": "Runs twice.",
                "evidence": [{"kind": "code", "ref": "lib/b.py"}],
            },
            {
                "section": "can_do",
                "text": "Uncited.",
                "evidence": [{"kind": "code", "ref": "docs/guide.md"}],
            },
        ],
    )
    assert _check(app) == "updated"
    assert _claim(app, "direct")["text"] == "It refunds."
    assert "claim:direct" in _state(app)["stale"]
    # Indirect, and a node: only flagged, never gone.
    assert understanding.load(app.targets, "bot")["nodes"]
    assert "node:agent" in _state(app)["flags"]
    added = [
        c["text"]
        for s in understanding.load(app.targets, "bot")["sections"]
        for c in s["claims"]
        if c.get("updated_from")
    ]
    assert added == ["Runs twice."]


def test_a_non_git_folder_has_links_and_no_commits(tmp_path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text("x = 1\n")
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    config = load(tmp_path / "evalution.yaml")
    targets = Targets(config)
    code = targets.code("bot")
    if code.commit() is not None:
        pytest.skip("the temp folder is inside a checkout")
    status = hooks.status(code, "bot")
    assert not status["available"] and status["reason"] == "not a git checkout"
    with pytest.raises(CodeError):
        hooks.install(code, "bot", "http://127.0.0.1:8080")


# The hooks


@pytest.fixture
def hooked(app):
    code = app.targets.code("bot")
    return code, code.hooks_folder()


URL = "http://127.0.0.1:8080"


def test_hooks_are_made_chained_and_removed_cleanly(hooked, app) -> None:
    code, folder = hooked
    mine = folder / "post-commit"
    mine.write_text("#!/usr/bin/env bash\necho mine\nexit 0\n")
    mine.chmod(0o755)
    other_block = "# keep\n"
    assert hooks.install(code, "bot", URL)["installed"]

    text = mine.read_text()
    assert text.startswith("#!/usr/bin/env bash\n# >>> evalution bot >>>")
    assert text.endswith("echo mine\nexit 0\n") and (mine.stat().st_mode & 0o777) == 0o755
    made = folder / "post-merge"
    assert (made.stat().st_mode & 0o777) == 0o700
    assert "/v1/targets/bot/changed" in made.read_text() and hooks.CREATED in made.read_text()

    # Twice is once.
    hooks.install(code, "bot", URL)
    assert mine.read_text().count("evalution bot >>>") == 1

    # What the owner adds to a file evalution made stays.
    (folder / "post-rewrite").write_text((folder / "post-rewrite").read_text() + other_block)

    assert not hooks.uninstall(code, "bot")["installed"]
    assert mine.read_text() == "#!/usr/bin/env bash\necho mine\nexit 0\n"
    assert not made.exists() and not (folder / "post-checkout").exists()
    assert other_block in (folder / "post-rewrite").read_text()
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=app.repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        == ""
    )


def test_a_hook_never_fails_or_waits_on_a_commit(hooked, app) -> None:
    code, _folder = hooked
    hooks.install(code, "bot", "http://127.0.0.1:9")
    # Nothing listens there, and git neither fails nor says a word about it.
    done = subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.test",
            "commit",
            "-qm",
            "e",
            "--allow-empty",
        ],
        cwd=app.repo,
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert done.stdout == "" and done.stderr == ""


def test_what_cannot_carry_a_hook_is_left_alone(hooked, tmp_path) -> None:
    code, folder = hooked
    python = folder / "post-commit"
    python.write_text("#!/usr/bin/env python3\nprint('mine')\n")
    python.chmod(0o755)
    off = folder / "post-merge"
    off.write_text("#!/bin/sh\necho off\n")
    off.chmod(0o644)
    target = tmp_path / "theirs"
    target.write_text("#!/bin/sh\n")
    target.chmod(0o755)
    (folder / "post-rewrite").symlink_to(target)
    found = hooks.install(code, "bot", URL)
    assert found["hooks"] == {
        "post-commit": "is not a shell script",
        "post-merge": "is switched off",
        "post-rewrite": "is a link",
        "post-checkout": "on",
    }
    assert not found["installed"]
    assert python.read_text() == "#!/usr/bin/env python3\nprint('mine')\n"
    assert (off.stat().st_mode & 0o777) == 0o644 and target.read_text() == "#!/bin/sh\n"


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("bot; rm -rf /", URL),
        ("../bot", URL),
        ("bot", "http://x/$(touch pwned)"),
        ("bot", "http://x'y"),
        ("bot", "file:///etc/passwd"),
        ("bot", "http://127.0.0.1:8080/path"),
    ],
)
def test_names_and_urls_are_refused_before_anything_is_written(hooked, name, url) -> None:
    code, folder = hooked
    with pytest.raises(ValueError):
        hooks.install(code, name, url)
    assert not (folder / "post-commit").exists()


def test_hooks_path_is_honoured_and_a_tracked_or_shared_one_refused(app, tmp_path) -> None:
    code = app.targets.code("bot")
    outside = tmp_path / "myhooks"
    _git(app.repo, "config", "core.hooksPath", str(outside))
    assert code.hooks_folder() == outside.resolve()
    hooks.install(code, "bot", URL)
    assert (outside / "post-commit").exists()
    hooks.uninstall(code, "bot")

    _commit(app.repo, {"githooks/keep": "x\n"})
    _git(app.repo, "config", "core.hooksPath", "githooks")
    assert not hooks.status(code, "bot")["available"]

    # Worktrees share the repo's hooks.
    _git(app.repo, "config", "--unset", "core.hooksPath")
    linked = tmp_path / "linked"
    _git(app.repo, "worktree", "add", "-q", str(linked))
    other = app.targets.code("bot")
    other.root = linked
    assert other.hooks_folder() == (app.repo / ".git" / "hooks").resolve()


def test_a_burst_of_hooks_is_one_check(app, monkeypatch) -> None:
    monkeypatch.setattr(impact, "SETTLE", 0.01)
    seen = []

    async def counted(_runs, name):
        seen.append(name)

    async def go():
        watcher = impact.Watcher(app, counted)
        for _ in range(5):
            watcher.changed("bot")
            await asyncio.sleep(0)
        await asyncio.sleep(0.2)
        await watcher.close()

    asyncio.run(go())
    assert seen == ["bot"]
