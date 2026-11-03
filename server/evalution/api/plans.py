"""The test page's plan of a case, which the tests and runs routes both show."""

from __future__ import annotations

import asyncio
from typing import Any

from evalution.core.case import Case
from evalution.project import understanding
from evalution.review import testplan
from evalution.runs.runner import Runs

#: Seconds the test page's plan waits for its words before answering without them.
WORDING_WAIT = 2.0


class CasePlans:
    def __init__(self, runs: Runs) -> None:
        self.runs = runs
        #: The test page's wording, one model call per case version at a time.
        self.wording: dict[str, asyncio.Task[dict[str, Any]]] = {}

    async def plan(self, target: str, set_name: str, found: Case, marks: dict) -> dict:
        """The test page's plan of *found*, worded on the light model and cached."""
        runs, targets, wording = self.runs, self.runs.targets, self.wording
        doc = await asyncio.to_thread(understanding.load, targets, target)
        labels = (doc or {}).get("step_labels") or {}
        labels = (
            {str(k): str(v) for k, v in labels.items() if v} if isinstance(labels, dict) else {}
        )
        cache = targets.config.data_dir / "plans" / target / set_name / f"{found.case}.json"
        caller = runs.caller("light")
        set_dir = targets.sets(target) / set_name
        # Worded in the background, once; the page gets plain mechanical words until then
        # (``wording: true``) and asks again.
        task = wording.get(str(cache))
        if task is None or task.done():
            task = asyncio.create_task(
                testplan.case_plan(found, set_dir, labels, marks, caller, cache)
            )
            wording[str(cache)] = task
            task.add_done_callback(lambda done: wording.pop(str(cache), None))
        try:
            return await asyncio.wait_for(asyncio.shield(task), WORDING_WAIT)
        except TimeoutError:
            plan = await asyncio.to_thread(testplan.mechanical, found, set_dir, labels, marks)
            return {**plan, "wording": True}
