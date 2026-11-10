"""Connecting an app by its repository's address on the code host: the address read, the
repository cloned into evalution's folder with the person's own git (no prompt, no hooks), a pull
request or ref checked out there, then connected as a folder. Offline: the code host is a folder
of bare repositories made here, and the check that would reach the app is stubbed."""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import load
from evalution.connect import discover, repository
from evalution.connect.repository import Address
from evalution.storage import Database
from evalution.traces.builtin import SpanStore

ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
URL = "http://127.0.0.1:8080"
MAIN = (
    "from fastapi import FastAPI\nfrom pydantic import BaseModel\n\napp = FastAPI()\n\n\n"
    "class Ask(BaseModel):\n    message: str\n\n\n"
    "class Answer(BaseModel):\n    answer: str\n\n\n"
    '@app.post("/api/chat", response_model=Answer)\nasync def chat(body: Ask):\n'
    "    return Answer(answer=body.message)\n\n\n"
    'if __name__ == "__main__":\n    import uvicorn\n\n'
    '    uvicorn.run(app, host="0.0.0.0", port=8123)\n'
)


def git(folder: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(folder), *args], capture_output=True, text=True, env=ENV, check=True
    )
    return done.stdout


@pytest.fixture(autouse=True)
def _plain_git(monkeypatch):
    for key, value in ENV.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)


@pytest.fixture
def host(tmp_path, monkeypatch):
    """The code host: ``acme/shop-bot.git`` with ``main``, a branch ``feature``, and pull request
    7; evalution clones from here instead of the network."""
    base = tmp_path / "host"
    bare = base / "acme" / "shop-bot.git"
    bare.parent.mkdir(parents=True)
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(bare), str(seed))
    (seed / "app").mkdir()
    (seed / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (seed / "app" / "main.py").write_text(MAIN)
    git(seed, "add", ".")
    git(seed, "commit", "-q", "-m", "first")
    git(seed, "push", "-q", "origin", "HEAD:main")
    main = git(seed, "rev-parse", "HEAD").strip()
    git(seed, "checkout", "-q", "-b", "feature")
    (seed / "app" / "main.py").write_text(MAIN.replace("8123", "8124"))
    git(seed, "commit", "-q", "-am", "feature")
    git(seed, "push", "-q", "origin", "feature")
    feature = git(seed, "rev-parse", "HEAD").strip()
    git(seed, "checkout", "-q", "main")
    (seed / "app" / "main.py").write_text(MAIN.replace("8123", "8125"))
    git(seed, "commit", "-q", "-am", "a pull request")
    git(seed, "push", "-q", "origin", "HEAD:refs/pull/7/head")
    pull = git(seed, "rev-parse", "HEAD").strip()
    # A hook every new clone gets from the person's git templates never runs for evalution.
    hooks = tmp_path / "template" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / "post-checkout").write_text(f"#!/bin/sh\ntouch {tmp_path / 'hook-ran'}\n")
    (hooks / "post-checkout").chmod(0o755)
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(tmp_path / "template"))
    monkeypatch.setattr(repository, "BASE", str(base))
    return {"main": main, "feature": feature, "pull": pull, "tmp": tmp_path}


@pytest.mark.parametrize(
    ("text", "address"),
    [
        ("github.com/acme/shop-bot", Address("acme", "shop-bot")),
        ("https://github.com/acme/shop-bot.git", Address("acme", "shop-bot")),
        ("https://www.github.com/acme/shop-bot/", Address("acme", "shop-bot")),
        ("acme/shop-bot", Address("acme", "shop-bot")),
        ("acme/shop-bot#412", Address("acme", "shop-bot", pull=412)),
        ("github.com/acme/shop-bot/pull/412", Address("acme", "shop-bot", pull=412)),
        ("acme/shop-bot@feature/x", Address("acme", "shop-bot", ref="feature/x")),
        ("acme/shop.bot@v1.2", Address("acme", "shop.bot", ref="v1.2")),
    ],
)
def test_an_address_is_read(text, address) -> None:
    assert repository.parse(text) == address


@pytest.mark.parametrize(
    "text",
    [
        "http://127.0.0.1:7001/api/chat",
        "localhost:3000/chat",
        "gitlab.example.com/acme/shop-bot",
        "acme",
        "~/code/shop-bot",
        "./shop-bot/app",
        "/Users/me/shop-bot",
        "acme/shop-bot@-x",
        "acme/shop-bot@../x",
        "acme/..",
        "acme/shop-bot#0",
        "acme/shop-bot/extra",
        "-acme/shop-bot",
    ],
)
def test_what_isnt_an_address(text) -> None:
    assert repository.parse(text) is None


def test_a_repository_is_cloned_with_no_hook_run_and_its_default_branch_out(host, tmp_path):
    data = tmp_path / "data"
    where, commit = repository.clone(Address("acme", "shop-bot"), data, "shop-bot")
    assert where == (data / "code" / "shop-bot").resolve()
    assert commit == host["main"] and git(where, "rev-parse", "HEAD").strip() == host["main"]
    assert (where / "app" / "main.py").is_file()
    assert not (tmp_path / "hook-ran").exists()
    # Connecting it again fetches what's new rather than cloning again.
    (where / "mine.txt").write_text("left by a build\n")
    assert (
        repository.clone(Address("acme", "shop-bot", ref="feature"), data, "shop-bot")[1]
        == (host["feature"])
    )
    assert (where / "mine.txt").exists()


def test_a_pull_request_or_a_ref_is_checked_out_in_the_clone(host, tmp_path):
    data = tmp_path / "data"
    where, commit = repository.clone(Address("acme", "shop-bot", pull=7), data, "bot")
    assert commit == host["pull"] and git(where, "rev-parse", "HEAD").strip() == host["pull"]
    refs = git(where, "for-each-ref", "--format=%(refname)", "refs/evalution").split()
    assert refs == ["refs/evalution/bot/pull/7"]
    other = repository.clone(Address("acme", "shop-bot", ref="feature"), data, "other")[1]
    assert other == host["feature"]
    assert (
        repository.clone(Address("acme", "shop-bot", ref=host["main"][:7]), data, "c")[1]
        == (host["main"])
    )


def test_a_clone_that_fails_says_why_and_leaves_nothing(host, tmp_path):
    data = tmp_path / "data"
    with pytest.raises(repository.CloneError, match="acme/nope couldn't be cloned"):
        repository.clone(Address("acme", "nope"), data, "nope")
    assert not (data / "code" / "nope").exists()
    with pytest.raises(repository.CloneError, match="origin has no pull request 9"):
        repository.clone(Address("acme", "shop-bot", pull=9), data, "bot")
    with pytest.raises(repository.CloneError, match="no branch, tag or commit by that name"):
        repository.clone(Address("acme", "shop-bot", ref="gone"), data, "bot")
    other = data / "code" / "taken"
    other.mkdir(parents=True)
    with pytest.raises(repository.CloneError, match="isn't a clone of acme/shop-bot"):
        repository.clone(Address("acme", "shop-bot"), data, "taken")


def test_a_clone_that_would_ask_for_a_password_fails_instead(host, tmp_path, monkeypatch):
    monkeypatch.setattr(repository, "BASE", "http://127.0.0.1:9")  # nothing listens there
    with pytest.raises(repository.CloneError, match="acme/shop-bot couldn't be cloned"):
        repository.clone(Address("acme", "shop-bot"), tmp_path / "data", "bot")


def _stub_check(monkeypatch):
    async def check(conf, overlay, store):
        return {
            "adapter": {"ok": False, "detail": "Couldn't reach it"},
            "phoenix": {"ok": False, "detail": "", "sessions_seen": 0, "reached": False},
            "code": {"ok": True, "detail": "", "files_matched": 2},
        }

    monkeypatch.setattr(discover.connect, "check", check)


def _discoveries(tmp_path) -> discover.Discoveries:
    store = SpanStore(Database.open(tmp_path / "data"), "local")
    return discover.Discoveries(tmp_path, list, None, URL, store, data_dir=tmp_path / "data")


async def test_connect_by_address_clones_then_reads_it_as_a_folder(host, tmp_path, monkeypatch):
    _stub_check(monkeypatch)
    found = _discoveries(tmp_path)
    job = found.start("acme/shop-bot#7")
    assert job.clone == Address("acme", "shop-bot", pull=7) and job.name == "shop-bot"
    await asyncio.wait(found._tasks)
    view = job.view()
    assert job.status == "ready", view
    texts = [s["text"] for s in view["steps"]]
    assert texts[0] == f"Cloned acme/shop-bot#7 at {host['pull'][:7]}"
    clone = (tmp_path / "data" / "code" / "shop-bot").resolve()
    assert view["folder"] == str(clone)
    assert view["repository"] == {
        "host": "github.com",
        "owner": "acme",
        "repo": "shop-bot",
        "pull": 7,
        "ref": None,
        "said": "acme/shop-bot#7",
    }
    assert view["target"]["code"] == {"path": str(clone)}
    # Read from the pull request's code: its port.
    assert view["target"]["options"]["send"]["url"] == "http://127.0.0.1:8125/api/chat"


async def test_connect_by_an_address_that_fails_says_so(host, tmp_path, monkeypatch):
    _stub_check(monkeypatch)
    found = _discoveries(tmp_path)
    job = found.start("github.com/acme/nope")
    await asyncio.wait(found._tasks)
    assert job.status == "failed"
    assert job.steps[-1]["state"] == "problem"
    assert "acme/nope couldn't be cloned" in job.steps[-1]["text"]


async def test_a_folder_that_is_there_wins_over_an_address(host, tmp_path, monkeypatch):
    _stub_check(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "acme" / "shop-bot").mkdir(parents=True)
    found = _discoveries(tmp_path)
    job = found.start("acme/shop-bot")
    assert job.clone is None and job.folder == (tmp_path / "acme" / "shop-bot").resolve()
    await asyncio.wait(found._tasks)
    assert not (tmp_path / "data" / "code").exists()
    # An app's own address is still an address, not a repository.
    assert found.start("http://127.0.0.1:7001/api/chat").clone is None


def test_the_api_connects_by_address_into_the_workspaces_data_folder(host, tmp_path, monkeypatch):
    _stub_check(monkeypatch)
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"data_dir": "elsewhere"}))
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as client:
        started = client.post("/v1/targets/discover", json={"path": "github.com/acme/shop-bot"})
        assert started.status_code == 202, started.text
        deadline = time.time() + 20
        while (found := client.get(f"/v1/targets/discover/{started.json()['id']}").json())[
            "status"
        ] == "working":
            assert time.time() < deadline
            time.sleep(0.05)
        assert found["status"] == "ready", found
        clone = (tmp_path / "elsewhere" / "code" / "shop-bot").resolve()
        assert found["target"]["code"] == {"path": str(clone)}
        assert found["repository"]["said"] == "acme/shop-bot"
        saved = client.post(f"/v1/targets/discover/{found['id']}/accept", json={})
        assert saved.status_code == 201, saved.text
        # Its versions are the clone's: a pull request is played from there.
        assert client.get("/v1/targets/shop-bot/versions").json()["repo"] == str(clone)
