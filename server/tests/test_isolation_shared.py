"""What the copy shares with the person's own app (plan B8): every bind mount and external volume
is said, with whether it may be written; a plan is refused only when the rehearsal's probe wrote
into a writable one. Offline: a compose config read as compose would give it, and a runner whose
search says where the canary went. ``test_isolation_real.py`` shows the refusal on Docker."""

from __future__ import annotations

from pathlib import Path

from evalution.isolation import isolate as iso
from evalution.isolation.launch import ComposeCopy, Copy, Mount, override_for
from evalution.isolation.plan import Plan
from evalution.isolation.rehearse import Rehearsal, Step, rehearse, within

CONFIG = {
    "services": {
        "web": {
            "image": "web:1",
            "volumes": [
                {"type": "bind", "source": "/home/me/app/src", "target": "/app"},
                {
                    "type": "bind",
                    "source": "/home/me/app/conf",
                    "target": "/conf",
                    "read_only": True,
                },
                {
                    "type": "bind",
                    "source": "/var/run/docker.sock",
                    "target": "/var/run/docker.sock",
                },
                {"type": "volume", "source": "data", "target": "/data"},
            ],
        }
    },
    "volumes": {"data": {}, "photos": {"external": True, "name": "family_photos"}},
}


def test_the_override_says_which_shared_folders_the_copy_may_write() -> None:
    out = override_for(CONFIG, "eval-bot-0", pick=lambda: 5000, linux=False)
    assert [(m.source, m.writable) for m in out.shared] == [
        ("/home/me/app/src", True),
        ("/home/me/app/conf", False),
        ("/var/run/docker.sock", True),
        ("volume:family_photos", True),
    ]


def _started(tmp_path: Path, shared: list[Mount]) -> ComposeCopy:
    copy = ComposeCopy([], None, "bot", 0, tmp_path)
    copy.copy = Copy(
        project=copy.project, ports={}, url="http://127.0.0.1:5000", containers=[], volumes=[],
        shared=shared, rewired=[], fixed=[], notes=[], started=0, start_seconds=1,
        override=tmp_path / "o.yaml",
    )  # fmt: skip
    return copy


def test_a_copy_names_what_it_shares_but_not_the_docker_socket(tmp_path) -> None:
    out = override_for(CONFIG, "eval-bot-0", pick=lambda: 5000, linux=False)
    copy = _started(tmp_path, out.shared)
    assert iso.shared_of(copy) == [
        {"path": "/home/me/app/src", "service": "web", "writable": True},
        {"path": "/home/me/app/conf", "service": "web", "writable": False},
        {"path": "volume:family_photos", "service": "*", "writable": True},
    ]
    said = iso.summary(copy)
    assert "/home/me/app/src (web, writable)" in said
    assert "/home/me/app/conf (web, read-only)" in said
    assert iso.shared_of(ComposeCopy([], None, "bot", 0, tmp_path)) == []  # not started


def test_a_place_is_within_a_shared_folder_or_volume() -> None:
    shared = ["/home/me/app/src", "volume:family_photos"]
    assert within("/home/me/app/src/main.py", shared)
    assert within("/home/me/app/src", shared)
    assert not within("/home/me/app/srcs/main.py", shared)
    assert within("eval-bot-0-web-1:volume:family_photos/2024/a.jpg", shared)
    assert not within("eval-bot-0-web-1:volume:eval-bot-0_data/a", shared)
    assert not within("/anything", [])


class Store:
    """A store that covers every place: a shared folder is refused even so."""

    name = "postgres-app"
    needs_stop = "none"

    def __init__(self) -> None:
        self.content = "start"

    def covers(self, path: str) -> bool:
        return True

    def digest(self) -> str:
        return self.content

    def keep(self, into: Path) -> None:
        pass

    def put_back(self, kept: Path) -> None:
        self.content = "start"

    def holding(self, canary: str) -> list[str]:
        return [self.name] if canary in self.content else []


class Runner:
    def __init__(self, store: Store, writes: list[str]) -> None:
        self.store = store
        self.writes = writes
        self.canary = ""

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def probe(self, canary: str) -> None:
        self.canary = canary
        self.store.content = canary

    def watch_roots(self) -> list[Path]:
        return []

    async def search(self, canary: str) -> list[str]:
        return (
            [w for w in self.writes]
            if canary == self.canary and self.store.content != "start"
            else []
        )


SRC, PHOTOS = "/home/me/app/src", "volume:family_photos"


async def test_a_probe_writing_into_a_shared_writable_folder_is_refused(tmp_path) -> None:
    store = Store()
    runner = Runner(store, [f"{SRC}/notes.txt"])
    result = await rehearse([store], runner, tmp_path / "kept", shared=[SRC, PHOTOS])
    assert not result.ok
    assert result.shared == [f"{SRC}/notes.txt"] and result.leaks == [f"{SRC}/notes.txt"]
    step = next(s for s in result.steps if not s.ok)
    assert step.name == "the app wrote nothing into a folder shared with your app"
    assert "Refused: a test wrote into a folder your app shares" in result.report()
    assert f"{SRC}/notes.txt" in result.report()
    assert result.steps[-1].name == "left as it was found" and result.steps[-1].ok


async def test_a_shared_folder_no_test_writes_in_only_warns(tmp_path) -> None:
    store = Store()
    result = await rehearse([store], Runner(store, []), tmp_path / "kept", shared=[SRC])
    assert result.ok and result.shared == []
    assert "the app wrote nothing into a folder shared with your app" in [
        s.name for s in result.steps
    ]


async def test_a_read_only_shared_folder_is_not_refused_by_the_shared_rule(tmp_path) -> None:
    # Only writable folders are passed: what turns up in a read-only one is the usual leak rule's.
    store = Store()
    result = await rehearse([store], Runner(store, ["/home/me/app/conf/x"]), tmp_path / "k")
    assert result.shared == []


def test_the_plans_account_names_what_is_shared(tmp_path) -> None:
    plan = Plan(target="bot", shared=[{"path": SRC, "service": "web", "writable": True}])
    result = Rehearsal(ok=True, steps=[Step("starting state kept", True)], seconds=3)
    said = iso._account(plan, result, [], "eval-bot-0", [])
    assert f"Shared with your app (not copied): {SRC} (web, writable)" in said
    assert "Refused" not in said


def test_an_old_plan_reads_with_nothing_shared() -> None:
    text = Plan(target="bot").to_json().replace('"shared": [],', "")
    assert '"shared"' not in text
    assert Plan.from_json(text).shared == []
