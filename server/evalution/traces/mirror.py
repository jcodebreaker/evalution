"""A mirror of finished runs in a target's Phoenix (on when its ``phoenix.mirror`` is true), written
once a run ends, over Phoenix's REST v1 only. The set is a dataset (one example per case), the run
an experiment, each try a repetition, each grade an evaluation linked to the try's trace. A mirror
failure is logged and never fails the run.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx

from evalution.config import PhoenixConfig
from evalution.core import net
from evalution.core.rules import current

_logger = logging.getLogger(__name__)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


class PhoenixStore:
    """A mirror of finished runs in Phoenix, over its REST v1 (no client library, no GraphQL)."""

    def __init__(self, url: str, key: str | None = None) -> None:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        self._client = net.client(base_url=url.rstrip("/"), headers=headers, timeout=30)

    async def _call(
        self, method: str, path: str, body: Any = None, params: Mapping[str, str] | None = None
    ) -> Any:
        response = await self._client.request(method, path, json=body, params=params)
        response.raise_for_status()
        return response.json() if response.content else None

    async def close(self) -> None:
        await self._client.aclose()

    async def save(self, run: Mapping[str, Any]) -> None:
        if run.get("status") == "running":
            return  # mirrored once, when it ends
        try:
            await self._mirror(run)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            _logger.warning("evals: run %s not mirrored to Phoenix", run.get("run"), exc_info=True)

    async def _dataset(self, run: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
        """The dataset of the run's cases at their versions (made on first use), and
        ``case -> example id``."""
        versions = json.dumps([run.get("set_version"), run.get("case_versions")], sort_keys=True)
        name = f"{run['target']}/{run['set']}@{hashlib.sha256(versions.encode()).hexdigest()[:12]}"
        cases: Mapping[str, Any] = run["cases"]
        found = await self._call("GET", "/v1/datasets", params={"name": name})
        rows = found.get("data") or []
        if rows:
            dataset_id = str(rows[0]["id"])
        else:
            made = await self._call(
                "POST",
                "/v1/datasets/upload?sync=true",
                {
                    "action": "create",
                    "name": name,
                    "inputs": [{"turns": c["turns"]} for c in cases.values()],
                    "outputs": [
                        {"tools": c["expected"], "rubric": c["reply_rubric"]}
                        for c in cases.values()
                    ],
                    "metadata": [{"case": k, "name": c["name"]} for k, c in cases.items()],
                },
            )
            dataset_id = str(made["data"]["dataset_id"])
        listed = await self._call("GET", f"/v1/datasets/{dataset_id}/examples")
        examples = (listed.get("data") or {}).get("examples") or []
        return dataset_id, {
            str((e.get("metadata") or {}).get("case")): str(e["id"]) for e in examples
        }

    async def _mirror(self, run: Mapping[str, Any]) -> None:
        dataset_id, examples = await self._dataset(run)
        cases: Mapping[str, Any] = run["cases"]
        repetitions = max((len(c["tries"]) for c in cases.values()), default=1) or 1
        made = await self._call(
            "POST",
            f"/v1/datasets/{dataset_id}/experiments",
            {
                "name": run["run"],
                "repetitions": repetitions,
                "metadata": {
                    "prompt": run.get("prompt"),
                    "pass_marks": run.get("pass_marks") or run.get("pass_scores"),
                },
            },
        )
        experiment_id = str(made["data"]["id"])
        started = float(run.get("started_at") or 0)
        for case, detail in cases.items():
            example = examples.get(case)
            if example is None:
                continue
            for attempt in detail["tries"]:
                await self._try(experiment_id, example, attempt, started)

    async def _try(
        self, experiment_id: str, example: str, attempt: Mapping[str, Any], started: float
    ) -> None:
        link = attempt.get("trace_url")
        trace_id = str(link).rsplit("/", 1)[-1] if link else None
        posted = await self._call(
            "POST",
            f"/v1/experiments/{experiment_id}/runs",
            {
                "dataset_example_id": example,
                "repetition_number": attempt["n"],
                "output": {"reply": attempt.get("reply_text"), "steps": attempt.get("steps")},
                "start_time": _iso(started),
                "end_time": _iso(started + float(attempt.get("seconds") or 0)),
                "trace_id": trace_id,
                "error": attempt.get("error"),
            },
        )
        run_id = str(posted["data"]["id"])
        grades: Sequence[tuple[str, str, float, str]] = [
            ("tools", "CODE", attempt["tools"], _reasons(attempt.get("steps") or [])),
            ("reply", "LLM", attempt["reply"], _reasons(attempt.get("rubric") or [])),
        ]
        for name, kind, score, explanation in grades:
            await self._call(
                "POST",
                "/v1/experiment_evaluations",
                {
                    "experiment_run_id": run_id,
                    "name": name,
                    "annotator_kind": kind,
                    "start_time": _iso(started),
                    "end_time": _iso(started + float(attempt.get("seconds") or 0)),
                    "result": {
                        "label": current(attempt).outcome.value,
                        "score": score,
                        "explanation": explanation,
                    },
                    "trace_id": trace_id,
                },
            )


def mirror_of(conf: PhoenixConfig | None) -> PhoenixStore | None:
    """Where a target's finished runs are mirrored, when its ``phoenix.mirror`` is true."""
    if conf is None or not conf.mirror:
        return None
    return PhoenixStore(conf.url, conf.keys.get(conf.api_key_env))


def _reasons(lines: Sequence[Mapping[str, Any]]) -> str:
    """Grade lines in a few words each: ``matched: called at 3 s`` or ``Links both files: ...``."""
    return "\n".join(
        f"{line.get('status') or line.get('line')}: {line.get('reason') or ''}" for line in lines
    )[:4_000]
