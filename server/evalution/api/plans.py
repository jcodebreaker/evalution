"""The test page's plan of a case, which the tests and runs routes both show."""

from __future__ import annotations

import asyncio

from evalution.core.case import Case
from evalution.project import understanding
from evalution.review import testplan
from evalution.runs.runner import Runs
from evalution.suite.store import sent_files


class CasePlans:
    def __init__(self, runs: Runs) -> None:
        self.runs = runs

    async def plan(self, target: str, set_name: str, found: Case, marks: dict) -> dict:
        """The test page's plan of *found*, in the mechanical words."""
        targets = self.runs.targets
        doc = await asyncio.to_thread(understanding.load, targets, target)
        labels = (doc or {}).get("step_labels") or {}
        labels = (
            {str(k): str(v) for k, v in labels.items() if v} if isinstance(labels, dict) else {}
        )
        files = await asyncio.to_thread(sent_files, targets.docs, target, set_name, found)
        return await testplan.case_plan(found, files, labels, marks)
