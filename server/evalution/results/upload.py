"""A run played somewhere else and uploaded (``evalution run --server``, docs/hosting.md, "Run
where your app runs"): an app that only runs on someone's computer or in CI is played there, and
its run and evidence sent to the workspace, where the page reads them as any other.

What arrives is checked as the page will read it, so a run that would break a view is refused
whole rather than stored. Where it played is the uploader's word for the name only: it is always
``local``, since this server didn't play it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from evalution.core.evidence import TryEvidence
from evalution.results.migrate import UNREADABLE, upcast
from evalution.results.record import SCHEMA, environment
from evalution.results.store import is_run_id
from evalution.results.views import case_detail, detail

#: A run that ended; one still ``running`` would show as playing here for ever.
_ENDED = ("done", "stopped", "failed")


class UploadError(ValueError):
    """What is wrong with an upload, in words for whoever sent it."""


def read_upload(body: Mapping[str, Any]) -> tuple[dict[str, Any], list[TryEvidence]]:
    """The run and evidence of an upload ``{"run": <run document>, "evidence": [<try>, ...]}``,
    as they are to be stored: today's schema, ``local``, and with nothing left open (the sessions
    it held were on the uploader's computer).

    :raises UploadError: It doesn't read as an ended run of this schema or an older one, or a try
        of its evidence isn't one of its tries.
    """
    doc, kept = body.get("run"), body.get("evidence", [])
    if not isinstance(doc, dict):
        raise UploadError("run: must be a run")
    if not isinstance(kept, list):
        raise UploadError("evidence: must be a list")
    if not isinstance(doc.get("run"), str) or not is_run_id(doc["run"]):
        raise UploadError("run.run: not a run id")
    for key in ("target", "set"):
        if not isinstance(doc.get(key), str) or not doc[key]:
            raise UploadError(f"run.{key}: required")
    if doc.get("status") not in _ENDED:
        raise UploadError(f"run.status: must be one of {', '.join(_ENDED)}")
    schema = doc.get("schema")
    if isinstance(schema, bool) or not isinstance(schema, int) or schema > SCHEMA:
        raise UploadError(f"run.schema: this server reads up to {SCHEMA}; upgrade it")
    place = doc.get("environment")
    name = place.get("name") if isinstance(place, dict) else None
    try:
        run = upcast(doc)
        run = {
            **run,
            "environment": environment("local", name),
            "sessions": [],
            "current": None,
            "analyzing": False,
        }
        detail(run, [run], None)
        for stem in run["cases"]:
            case_detail(run, stem)
        played = {(stem, t["n"]) for stem, entry in run["cases"].items() for t in entry["tries"]}
    except UNREADABLE as exc:
        raise UploadError(f"run: doesn't read as a run ({exc})") from exc
    tries = []
    for i, entry in enumerate(kept):
        try:
            found = TryEvidence.model_validate(entry)
        except ValidationError as exc:
            raise UploadError(f"evidence[{i}]: not a try's evidence") from exc
        if (found.case, found.n) not in played:
            raise UploadError(f"evidence[{i}]: {found.case} try {found.n} isn't in the run")
        tries.append(found.capped())
    return run, tries
