"""Runs one after another: the write-up after a run is outside the run lock (the next run starts
while it's written), and a run asked for while one plays waits in a queue, then plays."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from scripted_agent import SET, TARGET

from evalution.runs.runner import RunBusyError, Runs

ONE_TURN = {
    "name": "Answers",
    "turns": [{"message": "Hello there."}],
    "reply": {"rubric": ["Replies"]},
    "limits": {"tries": 1, "seconds": 900},
}


async def _until(check, seconds: float = 10.0) -> None:
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


async def test_the_write_up_lets_the_next_run_start(make_rig, monkeypatch) -> None:
    written = asyncio.Event()
    began: list[str] = []
    analyze = Runs._analyze

    async def slow(self, doc: dict[str, Any]) -> None:
        began.append(doc["run"])
        await written.wait()
        await analyze(self, doc)

    monkeypatch.setattr(Runs, "_analyze", slow)
    rig = make_rig()
    rig.add(ONE_TURN)
    runs = rig.runs
    first = await runs.start(runs.estimate(TARGET, SET, None, 1)["token"])
    await _until(lambda: began == [first["run"]])
    # The last try is judged and the write-up goes on: the lock is free, and so is the runner.
    assert not runs.busy() and not runs.lock.held
    assert runs.load(first["run"])["analyzing"] is True
    second = await runs.start(runs.estimate(TARGET, SET, None, 1)["token"])
    assert second["status"] == "running"
    # One run's write-up at a time: asking again while it's written is refused.
    with pytest.raises(RunBusyError):
        await runs.analyze(first["run"])
    written.set()
    await runs.wait()
    done = runs.load(first["run"])
    assert done["status"] == "done" and done["analyzing"] is False
    assert runs.load(second["run"])["status"] == "done"


async def test_a_run_asked_for_while_one_plays_is_queued_then_played(make_rig, monkeypatch) -> None:
    rig = make_rig()
    rig.add(ONE_TURN)
    runs = rig.runs
    first = await runs.start(runs.estimate(TARGET, SET, None, 1)["token"])
    assert first["status"] == "running"
    token = runs.estimate(TARGET, SET, None, 1)["token"]
    # Not asked to queue (the command line), it is refused as before, and the token kept.
    with pytest.raises(RunBusyError):
        await runs.start(token)
    second = await runs.start(token, queue=True)
    assert second["status"] == "queued" and second["run"] != first["run"]
    assert runs.load(second["run"])["status"] == "queued"
    assert runs.queued() == [second["run"]]
    await runs.wait()
    assert runs.queued() == []
    played = runs.load(second["run"])
    assert played["status"] == "done" and any(c["tries"] for c in played["cases"].values())
    assert played["started_at"] >= runs.load(first["run"])["ended_at"]


async def test_stop_on_a_queued_run_takes_it_off_the_queue(make_rig) -> None:
    rig = make_rig()
    rig.add(ONE_TURN)
    runs = rig.runs
    first = await runs.start(runs.estimate(TARGET, SET, None, 1)["token"])
    second = await runs.start(runs.estimate(TARGET, SET, None, 1)["token"], queue=True)
    stopped = await runs.stop(second["run"])
    assert stopped is not None and stopped["status"] == "stopped"
    assert runs.queued() == [] and runs.load(second["run"]) is None
    await runs.wait()
    assert runs.load(first["run"])["status"] == "done"
