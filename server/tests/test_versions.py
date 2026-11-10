"""Versions of a target's code, checked out by evalution on its own (ADR 0011, plan phase 2), on
throwaway repos with real git: a remote-only branch fetched into evalution's refs and nowhere
else, worktrees made detached, reused and removed, the person's checkout byte-identical
throughout, and refusals in plain words."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution import cli
from evalution.api.app import create_app
from evalution.config import load
from evalution.project import versions
from evalution.project.versions import VersionError, Versions

ENV = {
    **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def run(folder: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(folder), *args],
        capture_output=True,
        text=True,
        env=ENV,
        check=True,
    )
    return done.stdout


def commit(folder: Path, name: str, text: str) -> str:
    (folder / name).write_text(text)
    run(folder, "add", name)
    run(folder, "commit", "-q", "-m", f"change {name}")
    return run(folder, "rev-parse", "HEAD").strip()


@pytest.fixture(autouse=True)
def _plain_git(monkeypatch):
    """No one's git settings in these repos: evalution's own runner sees what the test made."""
    for key, value in ENV.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)


@pytest.fixture
def repos(tmp_path):
    """An origin with ``main`` and ``feature``, the person's clone of it working on ``mine`` with
    a staged change, an unstaged change, an untracked file and a stash; then ``remote-only``
    pushed to origin by someone else after the clone."""
    origin = tmp_path / "origin.git"
    run(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    run(tmp_path, "clone", "-q", str(origin), str(seed))
    commit(seed, "app.py", "reply = 'main'\n")
    run(seed, "push", "-q", "origin", "HEAD:main")
    run(seed, "checkout", "-q", "-b", "feature")
    commit(seed, "app.py", "reply = 'feature'\n")
    run(seed, "push", "-q", "origin", "feature")
    run(seed, "tag", "-a", "v1", "-m", "first release")
    run(seed, "push", "-q", "origin", "v1")

    person = tmp_path / "person"
    run(tmp_path, "clone", "-q", str(origin), str(person))
    run(person, "checkout", "-q", "-b", "mine")
    commit(person, "notes.txt", "one\n")
    (person / "notes.txt").write_text("stashed\n")
    run(person, "stash", "-q")
    (person / "staged.txt").write_text("staged\n")
    run(person, "add", "staged.txt")
    (person / "app.py").write_text("reply = 'work in progress'\n")
    (person / "untracked.txt").write_text("new\n")

    run(seed, "checkout", "-q", "-b", "remote-only", "main")
    only = commit(seed, "app.py", "reply = 'remote only'\n")
    run(seed, "push", "-q", "origin", "remote-only")
    return {"origin": origin, "person": person, "remote_only": only, "data": tmp_path / "data"}


def state(folder: Path) -> str:
    """The person's checkout, as a hash: index file, status, their refs, stash, HEAD,
    FETCH_HEAD."""
    git_dir = Path(run(folder, "rev-parse", "--absolute-git-dir").strip())
    common = Path(run(folder, "rev-parse", "--git-common-dir").strip())
    common = common if common.is_absolute() else (folder / common)
    parts = [
        (git_dir / "index").read_bytes(),
        run(
            folder, "--no-optional-locks", "status", "--porcelain=v2", "--branch", "-uall"
        ).encode(),
        run(
            folder, "for-each-ref", "refs/heads", "refs/remotes", "refs/tags", "refs/stash"
        ).encode(),
        run(folder, "stash", "list").encode(),
        (git_dir / "HEAD").read_bytes(),
        run(folder, "rev-parse", "HEAD").encode(),
        b"fetch" if (common / "FETCH_HEAD").exists() else b"",
        *(
            p.read_bytes()
            for p in sorted(folder.rglob("*"))
            if p.is_file() and ".git" not in p.parts
        ),
    ]
    return hashlib.sha256(b"\0".join(parts)).hexdigest()


def test_a_remote_only_branch_is_fetched_into_evalutions_refs_only(repos) -> None:
    person = repos["person"]
    before = state(person)
    heads = run(person, "for-each-ref", "refs/heads", "refs/remotes", "refs/tags")
    assert "remote-only" not in heads

    found = Versions("bot", person, repos["data"])
    commit_ = found.fetch("origin", "remote-only")

    assert commit_ == repos["remote_only"]
    assert run(person, "for-each-ref", "refs/heads", "refs/remotes", "refs/tags") == heads
    ours = run(person, "for-each-ref", "--format=%(refname) %(objectname)", "refs/evalution")
    assert ours.strip() == f"refs/evalution/bot/origin/remote-only {commit_}"
    assert state(person) == before
    # Resolvable by the name a person says, and listed as evalution's.
    assert found.resolve("origin/remote-only") == commit_
    listed = {(r.kind, r.name): r for r in found.refs()}
    assert listed[("fetched", "origin/remote-only")].commit == commit_
    assert listed[("fetched", "origin/remote-only")].subject == "change app.py"


def push_more(origin: Path, branch: str, text: str) -> str:
    """Someone else pushes a new commit to *branch* on *origin*, after every fetch here."""
    other = origin.parent / f"other-{branch}"
    if not other.exists():
        run(origin.parent, "clone", "-q", str(origin), str(other))
    run(other, "fetch", "-q", "origin")
    run(other, "checkout", "-q", "-B", branch, f"origin/{branch}")
    made = commit(other, "app.py", text)
    run(other, "push", "-q", "origin", branch)
    return made


def test_a_remote_branch_is_fetched_again_every_time_it_is_played(repos) -> None:
    person = repos["person"]
    before = state(person)
    found = Versions("bot", person, repos["data"])
    assert found.reach("origin/remote-only") == repos["remote_only"]

    newer = push_more(repos["origin"], "remote-only", "reply = 'newer'\n")
    # Not an earlier fetch: the remote's branch as it is now.
    assert found.reach("origin/remote-only") == newer
    # The person's remote-tracking branch is behind too; it is fetched, into evalution's refs.
    stale = run(person, "rev-parse", "origin/feature").strip()
    feature = push_more(repos["origin"], "feature", "reply = 'feature, later'\n")
    assert found.reach("origin/feature") == feature
    assert run(person, "rev-parse", "origin/feature").strip() == stale
    assert state(person) == before
    # Without a fetch (the estimate before Run), what is known here.
    assert found.reach("origin/feature", fetch=False) == feature


def test_a_remote_branch_that_cant_be_fetched_is_not_played_from_an_earlier_fetch(repos) -> None:
    found = Versions("bot", repos["person"], repos["data"])
    found.fetch("origin", "remote-only")
    run(repos["person"], "remote", "set-url", "origin", str(repos["origin"].parent / "gone.git"))
    with pytest.raises(VersionError, match="Fetching remote-only from origin failed"):
        found.reach("origin/remote-only")
    assert found.reach("origin/remote-only", fetch=False) == repos["remote_only"]


def open_pull_request(origin: Path, number: int, text: str) -> str:
    """A pull request's head on *origin*, as a code host keeps it: ``refs/pull/N/head``."""
    other = origin.parent / f"pr-{number}"
    run(origin.parent, "clone", "-q", str(origin), str(other))
    made = commit(other, "app.py", text)
    run(other, "push", "-q", "origin", f"HEAD:refs/pull/{number}/head")
    return made


def test_a_pull_request_is_fetched_into_evalutions_refs_only(repos) -> None:
    person = repos["person"]
    head = open_pull_request(repos["origin"], 412, "reply = 'pull request'\n")
    before = state(person)
    theirs = run(person, "for-each-ref", "refs/heads", "refs/remotes", "refs/tags")
    found = Versions("bot", person, repos["data"])

    assert versions.pull_number("pull/412") == 412 and versions.pull_number("#412") == 412
    assert versions.pull_number("pull/x") is None and versions.pull_number("main") is None
    assert found.pull(412) == head
    ours = run(person, "for-each-ref", "--format=%(refname) %(objectname)", "refs/evalution")
    assert ours.strip() == f"refs/evalution/bot/pull/412 {head}"
    assert run(person, "for-each-ref", "refs/heads", "refs/remotes", "refs/tags") == theirs
    assert state(person) == before
    # Played by either name; listed as a pull request.
    assert found.reach("#412") == head and found.reach("pull/412") == head
    assert found.resolve("pull/412") == head
    listed = {(r.kind, r.name): r for r in found.refs()}
    assert listed[("pull", "pull/412")].commit == head
    # Pushed to again: fetched again when played.
    newer = commit(repos["origin"].parent / "pr-412", "app.py", "reply = 'pr, later'\n")
    run(repos["origin"].parent / "pr-412", "push", "-q", "origin", "HEAD:refs/pull/412/head")
    assert found.reach("#412", fetch=False) == head
    assert found.reach("#412") == newer
    path = found.checkout("pull/412")
    assert (path / "app.py").read_text() == "reply = 'pr, later'\n"
    assert state(person) == before


def test_a_pull_request_that_isnt_there_is_said_plainly(repos) -> None:
    found = Versions("bot", repos["person"], repos["data"])
    with pytest.raises(VersionError, match="origin has no pull request 9"):
        found.reach("#9")
    with pytest.raises(VersionError, match="no pull request 9 fetched here"):
        found.reach("#9", fetch=False)
    upstream = repos["origin"].parent / "upstream.git"
    run(upstream.parent, "clone", "-q", "--bare", str(repos["origin"]), str(upstream))
    run(repos["person"], "remote", "add", "upstream", str(upstream))
    head = open_pull_request(upstream, 9, "reply = 'nine'\n")
    # A fork's pull requests are on the repository it was forked from: upstream, when there is one.
    assert found.reach("#9") == head
    assert run(
        repos["person"], "for-each-ref", "--format=%(refname)", "refs/evalution"
    ).split() == ["refs/evalution/bot/pull/9"]


def test_refs_list_branches_remotes_and_tags_and_what_is_checked_out(repos) -> None:
    found = Versions("bot", repos["person"], repos["data"])
    listed = {(r.kind, r.name): r for r in found.refs()}
    assert {("local", "main"), ("local", "mine"), ("remote", "origin/feature"), ("tag", "v1")} <= (
        set(listed)
    )
    assert ("remote", "origin/HEAD") not in listed
    assert listed[("local", "mine")].checked_out and not listed[("local", "main")].checked_out
    # An annotated tag names its commit, with the commit's subject.
    assert listed[("tag", "v1")].commit == listed[("remote", "origin/feature")].commit
    assert listed[("tag", "v1")].subject == "change app.py"
    assert found.remotes() == ["origin"]


def test_a_ref_resolves_to_its_full_commit(repos) -> None:
    found = Versions("bot", repos["person"], repos["data"])
    feature = run(repos["person"], "rev-parse", "origin/feature").strip()
    for ref in ("origin/feature", "v1", feature, feature[:7]):
        assert found.resolve(ref) == feature
    with pytest.raises(VersionError, match="no branch, tag or commit by that name"):
        found.resolve("nope")
    with pytest.raises(VersionError, match="not a branch, tag or commit"):
        found.resolve("--output=/tmp/x")


def test_a_worktree_is_made_detached_reused_and_removed_alone(repos) -> None:
    person = repos["person"]
    run(person, "worktree", "add", "-q", str(person.parent / "theirs"), "main")
    theirs = run(person, "worktree", "list", "--porcelain")
    before = state(person)

    found = Versions("bot", person, repos["data"])
    path = found.checkout("origin/feature")
    feature = found.resolve("origin/feature")
    assert path == (repos["data"] / "versions" / "bot" / feature[:12]).resolve()
    assert (path / "app.py").read_text() == "reply = 'feature'\n"
    assert run(path, "rev-parse", "HEAD").strip() == feature
    detached = subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "-q", "HEAD"], env=ENV, check=False
    )
    assert detached.returncode != 0, "detached: no branch of the person's is checked out"
    assert state(person) == before

    (path / "built.txt").write_text("something a build left\n")
    assert found.checkout(feature) == path, "the same commit reuses its worktree"
    assert (path / "built.txt").exists()
    [kept] = found.kept()
    assert kept.commit == feature and kept.size > 0
    assert any(r.worktree for r in found.refs() if r.commit == feature)

    gone = found.remove(feature[:7])
    assert gone.commit == feature and not path.exists()
    assert found.kept() == []
    assert run(person, "worktree", "list", "--porcelain") == theirs, "theirs is left"
    assert state(person) == before


def test_removing_prunes_only_evalutions_entries(repos) -> None:
    person = repos["person"]
    stale = person.parent / "stale"
    run(person, "worktree", "add", "-q", "--detach", str(stale), "main")
    found = Versions("bot", person, repos["data"])
    path = found.checkout("main")
    # Both folders vanish behind git's back; removing evalution's prunes only its entry.
    shutil.rmtree(stale)
    shutil.rmtree(path)
    other = found.checkout("origin/feature")
    found.remove(found.resolve("origin/feature"))
    assert not other.exists()
    listed = run(person, "worktree", "list", "--porcelain")
    assert str(stale.resolve()) in listed, "the person's stale entry is theirs to prune"
    assert str(path) not in listed


def test_a_code_folder_that_is_itself_a_worktree_works(repos) -> None:
    person = repos["person"]
    lite = person.parent / "lite"
    run(person, "worktree", "add", "-q", "-b", "lite", str(lite), "main")
    (lite / "sub").mkdir()
    before = state(lite)

    found = Versions("lite", lite / "sub", repos["data"])
    assert found.top == lite.resolve()
    assert found.head()[0] == "refs/heads/lite"
    commit_ = found.fetch("origin", "remote-only")
    path = found.checkout(commit_)
    assert (path / "app.py").read_text() == "reply = 'remote only'\n"
    assert found.code_folder(commit_) == path / "sub"
    # The worktree belongs to the repository both folders share.
    assert str(path) in run(person, "worktree", "list", "--porcelain")
    found.remove(commit_)
    assert state(lite) == before


def test_the_oldest_version_goes_first_past_the_limit(repos) -> None:
    found = Versions("bot", repos["person"], repos["data"], limit=1)
    first = found.checkout("main")
    os.utime(first, (1, 1))
    second = found.checkout("origin/feature")
    assert not first.exists() and second.exists(), "the one just made is kept"
    assert [k.path for k in found.kept()] == [second]


def test_too_little_disk_is_refused_with_what_to_remove(repos, monkeypatch) -> None:
    found = Versions("bot", repos["person"], repos["data"])
    found.checkout("main")
    monkeypatch.setattr(versions, "free", lambda _path: 2 * versions.GB)
    with pytest.raises(VersionError) as refused:
        found.checkout("origin/feature")
    said = str(refused.value)
    assert "Only 2.0 GB free" in said and "needs 5.0 GB" in said
    assert "evalution versions --target bot --remove" in said
    assert found.resolve("main")[:7] in said
    # Reusing one already there needs no room.
    assert found.checkout("main").exists()


# Versions in use, and more than one evalution at a time


def test_a_version_a_run_is_playing_is_not_trimmed_nor_removed(repos) -> None:
    playing = Versions("bot", repos["person"], repos["data"], limit=1)
    held = playing.take("main")
    assert held.path.is_dir() and held.commit == playing.resolve("main")
    os.utime(held.path, (1, 1))
    assert [k.in_use for k in playing.kept()] == [True]

    # Another run, past the limit: the one in use stays, though it was used longest ago.
    other = Versions("bot", repos["person"], repos["data"], limit=1)
    second = other.checkout("origin/feature")
    assert held.path.is_dir() and second.is_dir()
    with pytest.raises(versions.InUse, match="a run is playing it"):
        other.remove(held.commit)
    assert held.path.is_dir()

    held.release()
    held.release()  # twice is harmless
    assert other.trim(keep=[other.resolve("origin/feature")])[0].commit == held.commit
    assert not held.path.exists()


def test_two_runs_of_one_version_share_it(repos) -> None:
    found = Versions("bot", repos["person"], repos["data"])
    first, second = found.take("main"), found.take("main")
    assert first.path == second.path
    first.release()
    with pytest.raises(versions.InUse):
        found.remove(first.commit)
    with second:
        pass
    assert found.remove(first.commit).commit == first.commit


HOLDER = """
import sys
from pathlib import Path
from evalution.project.versions import Versions
found = Versions("bot", Path(sys.argv[1]), Path(sys.argv[2]))
if sys.argv[3] == "take":
    held = found.take("main")
    print(held.commit, flush=True)
else:
    with found.changing():
        print("locked", flush=True)
        sys.stdin.readline()
sys.stdin.readline()
"""


def hold(repos, how: str) -> tuple[subprocess.Popen[str], str]:
    """Another evalution process: holding a version (*how* ``take``) or the versions folder's
    lock (``lock``) until its stdin is closed."""
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(repos["person"]), str(repos["data"]), how],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None
    return holder, holder.stdout.readline().strip()


def test_a_version_another_process_plays_is_not_removed(repos) -> None:
    holder, commit_ = hold(repos, "take")
    found = Versions("bot", repos["person"], repos["data"])
    try:
        assert commit_ == found.resolve("main")
        assert [k.in_use for k in found.kept()] == [True]
        with pytest.raises(versions.InUse):
            found.remove(commit_)
    finally:
        holder.communicate("\n\n", timeout=30)
    # Its lease ends with it, however it ends.
    assert [k.in_use for k in found.kept()] == [False]
    assert found.remove(commit_).commit == commit_


def test_the_versions_folder_is_locked_across_processes(repos, monkeypatch) -> None:
    found = Versions("bot", repos["person"], repos["data"])
    holder, said = hold(repos, "lock")
    assert said == "locked"
    monkeypatch.setattr(versions, "LOCK_WAIT", 0.3)
    try:
        with pytest.raises(VersionError, match="Another evalution is changing"):
            found.checkout("main")
        assert found.kept() == []
    finally:
        holder.communicate("\n\n", timeout=30)
    assert found.checkout("main").is_dir()


class Images:
    """A stand-in for phase 3's built images."""

    def __init__(self) -> None:
        self.removed: list[str] = []

    def size(self, commit: str) -> int:
        return 7 * versions.GB

    def remove(self, commit: str) -> None:
        self.removed.append(commit)


def test_what_a_version_keeps_beside_counts_and_goes_with_it(repos) -> None:
    images = Images()
    found = Versions("bot", repos["person"], repos["data"], parts=[images])
    found.checkout("main")
    [kept] = found.kept()
    assert kept.size > 7 * versions.GB and kept.parts == {"Images": 7 * versions.GB}
    found.remove(kept.commit)
    assert images.removed == [kept.commit]


def test_refusals_read_plainly(repos, tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with pytest.raises(VersionError, match="isn't a git checkout"):
        Versions("bot", plain, repos["data"])
    with pytest.raises(VersionError, match="Say where the app's code is"):
        versions.of("bot", None, repos["data"])
    found = Versions("bot", repos["person"], repos["data"])
    with pytest.raises(VersionError, match="upstream: not a remote of person \\(origin\\)"):
        found.fetch("upstream", "main")
    with pytest.raises(VersionError, match="origin has no branch gone"):
        found.fetch("origin", "gone")
    with pytest.raises(VersionError, match="not a branch name"):
        found.fetch("origin", "-x")
    with pytest.raises(VersionError, match="not a commit"):
        found.remove("main")
    with pytest.raises(LookupError):
        found.remove("abcdef1")
    assert run(repos["person"], "for-each-ref", "refs/evalution") == ""


def test_a_data_folder_inside_the_repo_and_not_ignored_is_refused(repos) -> None:
    person = repos["person"]
    found = Versions("bot", person, person / "data")
    before = state(person)
    with pytest.raises(VersionError, match="git doesn't ignore it"):
        found.checkout("main")
    assert state(person) == before


# API and CLI


@pytest.fixture
def client(repos, tmp_path):
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {
                "data_dir": str(repos["data"]),
                "targets": {
                    "bot": {
                        "adapter": "http",
                        "http": {"send": {"url": "x"}},
                        "code": {"path": str(repos["person"])},
                    },
                    "nocode": {"adapter": "http", "http": {"send": {"url": "x"}}},
                },
            }
        )
    )
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as made:
        yield made


def test_the_api_lists_fetches_and_removes(client, repos) -> None:
    before = state(repos["person"])
    listed = client.get("/v1/targets/bot/versions")
    assert listed.status_code == 200, listed.text
    view = listed.json()
    assert view["checked_out"]["branch"] == "mine" and view["kept"] == []
    names = {(r["kind"], r["name"]) for r in view["refs"]}
    assert ("local", "mine") in names and ("fetched", "origin/remote-only") not in names

    fetched = client.post(
        "/v1/targets/bot/versions/fetch", json={"remote": "origin", "branch": "remote-only"}
    )
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["commit"] == repos["remote_only"]
    refs = client.get("/v1/targets/bot/versions").json()["refs"]
    assert {"kind": "fetched", "name": "origin/remote-only"}.items() <= next(
        r for r in refs if r["name"] == "origin/remote-only"
    ).items()

    Versions("bot", repos["person"], repos["data"]).checkout(repos["remote_only"])
    kept = client.get("/v1/targets/bot/versions").json()
    assert [k["commit"] for k in kept["kept"]] == [repos["remote_only"]]
    assert next(r for r in kept["refs"] if r["name"] == "origin/remote-only")["worktree"]

    gone = client.delete(f"/v1/targets/bot/versions/{repos['remote_only'][:12]}")
    assert gone.status_code == 200, gone.text
    assert gone.json()["removed"]["commit"] == repos["remote_only"] and gone.json()["kept"] == []
    assert client.delete(f"/v1/targets/bot/versions/{repos['remote_only']}").status_code == 404
    assert state(repos["person"]) == before


def test_the_api_wont_remove_a_version_a_run_plays(client, repos) -> None:
    held = Versions("bot", repos["person"], repos["data"]).take("main")
    view = client.get("/v1/targets/bot/versions").json()
    assert [(k["commit"], k["in_use"]) for k in view["kept"]] == [(held.commit, True)]
    during = client.delete(f"/v1/targets/bot/versions/{held.commit}")
    assert during.status_code == 409
    assert (
        during.json()["detail"]
        == f"{held.commit[:7]}: a run is playing it; remove it when the run ends"
    )
    held.release()
    assert client.delete(f"/v1/targets/bot/versions/{held.commit}").status_code == 200


def test_the_api_fetches_a_pull_request(client, repos) -> None:
    head = open_pull_request(repos["origin"], 412, "reply = 'pull request'\n")
    fetched = client.post("/v1/targets/bot/versions/fetch", json={"pull": 412})
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == {"remote": "origin", "pull": 412, "ref": "pull/412", "commit": head}
    refs = client.get("/v1/targets/bot/versions").json()["refs"]
    assert {"kind": "pull", "name": "pull/412", "commit": head}.items() <= next(
        r for r in refs if r["name"] == "pull/412"
    ).items()
    missing = client.post("/v1/targets/bot/versions/fetch", json={"pull": 9})
    assert missing.status_code == 400 and missing.json()["detail"] == "origin has no pull request 9"
    for wrong in ("412", 0, True):
        bad = client.post("/v1/targets/bot/versions/fetch", json={"pull": wrong})
        assert bad.status_code == 400 and "pull: the pull request's number" in bad.json()["detail"]


def test_the_api_refuses_plainly(client, monkeypatch) -> None:
    no_code = client.get("/v1/targets/nocode/versions")
    assert no_code.status_code == 400 and "Say where the app's code is" in no_code.json()["detail"]
    unsaid = client.post("/v1/targets/bot/versions/fetch", json={"remote": "origin"})
    assert unsaid.status_code == 400 and "branch" in unsaid.json()["detail"]
    wrong = client.post("/v1/targets/bot/versions/fetch", json={"remote": "nope", "branch": "main"})
    assert wrong.status_code == 400 and "not a remote" in wrong.json()["detail"]
    assert client.get("/v1/targets/ghost/versions").status_code == 404

    monkeypatch.setattr(client.app.state.runs, "busy", lambda: True)
    during = client.delete("/v1/targets/bot/versions/abcdef1")
    assert during.status_code == 409 and "A run is going" in during.json()["detail"]
    monkeypatch.setattr(client.app.state.runs, "busy", lambda: False)

    client.app.state.hosted = True
    for answer in (
        client.get("/v1/targets/bot/versions"),
        client.post("/v1/targets/bot/versions/fetch", json={"remote": "o", "branch": "m"}),
        client.delete("/v1/targets/bot/versions/abcdef1"),
    ):
        assert answer.status_code == 400 and answer.json()["detail"] == (
            "Versions are local only: hosted evalution has no copies of apps to play them on."
        )


def test_the_cli_lists_fetches_and_removes(client, repos, tmp_path, capsys) -> None:
    config = ["--config", str(tmp_path / "evalution.yaml")]
    assert cli.main([*config, "versions", "--target", "bot", "--fetch", "origin/remote-only"]) == 0
    assert "fetched origin/remote-only" in capsys.readouterr().out
    Versions("bot", repos["person"], repos["data"]).checkout(repos["remote_only"])
    assert cli.main([*config, "versions", "--target", "bot"]) == 0
    out = capsys.readouterr().out
    assert "checked out mine" in out and "origin/remote-only" in out
    assert repos["remote_only"][:7] in out.split("versions kept:")[1]
    assert cli.main([*config, "versions", "--target", "bot", "--remove", repos["remote_only"]]) == 0
    assert "removed" in capsys.readouterr().out
    assert cli.main([*config, "versions", "--target", "nocode"]) == 2
    assert "Say where the app's code is" in capsys.readouterr().err
