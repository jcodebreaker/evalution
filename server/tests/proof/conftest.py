"""The rig: the scripted agent as an adapter, and simulated time in what plays a try."""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import scripted_agent

from evalution import adapters
from evalution.judging import rubric
from evalution.runs import collect, play


@pytest.fixture
def clock(monkeypatch) -> scripted_agent.Clock:
    made = scripted_agent.Clock(time.time())
    monkeypatch.setattr(play, "time", made)
    monkeypatch.setattr(play, "_sleep", made.sleep)
    monkeypatch.setattr(rubric, "_sleep", made.sleep)
    monkeypatch.setattr(collect, "time", made)
    monkeypatch.setattr(collect, "asyncio", SimpleNamespace(sleep=made.sleep))
    return made


@pytest.fixture
def make_rig(tmp_path, clock, monkeypatch):
    """Rigs in temporary folders. The agent is found the way any adapter is: a module in the
    adapters' folder, and here one shared with this test's own import of it."""
    seam = tmp_path / "adapters"
    seam.mkdir()
    (seam / "scripted_agent.py").symlink_to(Path(scripted_agent.__file__))
    monkeypatch.setattr(adapters, "__path__", [*adapters.__path__, str(seam)])
    monkeypatch.setattr(adapters, "_built", {})
    monkeypatch.setitem(sys.modules, "evalution.adapters.scripted_agent", scripted_agent)
    adapters.infos.cache_clear()
    adapters.own_traffic.cache_clear()
    made = 0

    def make(faults=(), **options) -> scripted_agent.Rig:
        nonlocal made
        made += 1
        root = tmp_path / f"rig{made}"
        root.mkdir()
        adapters._built.clear()
        return scripted_agent.Rig(root, clock, faults, **options)

    try:
        yield make
    finally:
        scripted_agent.WIRES.clear()
        monkeypatch.undo()
        adapters.infos.cache_clear()
        adapters.own_traffic.cache_clear()
