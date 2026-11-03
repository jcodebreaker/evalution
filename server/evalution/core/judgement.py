"""What a judgement is, as data (docs/judging.md): the words every part of evalution shares for how
a check, a try and a test came out. How they are decided is :mod:`evalution.core.rules`.

Models are frozen and dump to the plain JSON a run file holds; reading one back validates it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator


class Outcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNJUDGED = "unjudged"


class Decider(StrEnum):
    CODE = "code"
    MODEL = "model"


class Role(StrEnum):
    """Whether a check decides: a guide is recorded, never decides."""

    MUST = "must"
    GUIDE = "guide"


class WhyUnjudged(StrEnum):
    NO_TRACE_SOURCE = "no_trace_source"
    NO_TRACES = "no_traces"
    PARTIAL_TRACE = "partial_trace"
    CONTENT_NOT_CAPTURED = "content_not_captured"
    JUDGE_FAILED = "judge_failed"
    JUDGE_UNSURE = "judge_unsure"
    APP_UNREACHABLE = "app_unreachable"
    NOT_SUPPORTED = "not_supported"
    ADAPTER_MISREAD = "adapter_misread"
    BLOCKED = "blocked"
    #: Out of time, with no sign of the agent working. The name is what stored runs hold.
    NEVER_STARTED = "never_started"
    CREDENTIALS_REFUSED = "credentials_refused"
    APP_BUSY = "app_busy"


class CheckKind(StrEnum):
    STATE = "state"
    CONTEXT = "context"
    REPLY_TEXT = "reply_text"
    REPLY_LACKS = "reply_lacks"
    TOOL_RESULT = "tool_result"
    NEVER = "never"
    STEPS = "steps"
    RUBRIC = "rubric"
    REFERENCE = "reference"
    LIMIT = "limit"
    TURN = "turn"
    TIMEOUT = "timeout"


class Basis(StrEnum):
    """What a pass rests on: a code check of the outcome, a model's reading alone, or neither."""

    CHECKED = "checked"
    MODEL_ONLY = "model_only"
    NONE = "none"


class Route(StrEnum):
    EXPECTED = "expected"
    DIFFERENT = "different"
    UNKNOWN = "unknown"


class VerdictKind(StrEnum):
    PASS = "pass"  # noqa: S105
    FLAKY = "flaky"
    FAIL = "fail"
    UNJUDGED = "unjudged"
    NOT_RUN = "not_run"


class Label(StrEnum):
    """How a try or a test came out, in the one plain word a person reads
    (:func:`evalution.core.rules.label_of`). Only the first three say anything about the app."""

    PASSED = "Passed"
    FAILED_CHECK = "Failed check"
    TIMED_OUT = "Timed out"
    SETUP_BLOCKED = "Setup blocked"
    ADAPTER_ERROR = "Adapter error"
    GRADER_UNAVAILABLE = "Grader unavailable"
    MISSING_EVIDENCE = "Missing evidence"


class Change(StrEnum):
    """How a test moved between two runs."""

    BETTER = "better"
    WORSE = "worse"
    SAME = "same"
    #: No one could judge it on one side or the other.
    UNKNOWN = "unknown"
    #: It is in only one of the two runs.
    ONLY_ONE = "only_one"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class EvidenceRef(Frozen):
    """Where a check looked: ``span:ab12``, ``turn:3``, ``state:2`` or ``reply``.

    :param verified: For a model's quote: whether code found it in the evidence.
    """

    ref: str
    quote: str | None = None
    verified: bool | None = None


class Claim(Frozen):
    """Something a model says about a run. ``verified``: its refs exist and its quotes occur in the
    evidence. That doesn't make it right."""

    text: str
    refs: tuple[str, ...] = ()
    quotes: tuple[str, ...] = ()
    verified: bool = False


class CheckResult(Frozen):
    """One check of one try.

    :param id: Stable within a try: ``state:3:memory_lacks``, ``rubric:2``, ``never:x``, ``steps``.
    :param score: Shown only; no rule reads it.
    :param cut: Characters of the evidence the grader was not shown (``judging/render.py``).
    """

    id: str
    kind: CheckKind
    decided_by: Decider
    role: Role
    outcome: Outcome
    why: str
    unjudged: WhyUnjudged | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    score: float | None = None
    cut: int = 0

    @model_validator(mode="after")
    def _says_why_it_could_not_judge(self) -> CheckResult:
        if (self.outcome is Outcome.UNJUDGED) != (self.unjudged is not None):
            raise ValueError(f"{self.id}: unjudged reason and outcome disagree")
        return self


class JudgeStamp(Frozen):
    """Who graded: the model key, the model behind it (``None``: not recorded, so never comparable)
    and a hash of the prompts."""

    key: str
    model: str | None = None
    prompt_hash: str | None = None


class Judgement(Frozen):
    """One try as judged. A try keeps every judgement it was given; the newest is current.

    :param rules_from: The rules the try was first judged under, when these are later ones.
    """

    outcome: Outcome
    checks: tuple[CheckResult, ...]
    rules: int
    judge: JudgeStamp | None = None
    basis: Basis = Basis.NONE
    route: Route = Route.UNKNOWN
    rules_from: int | None = None


class Verdict(Frozen):
    """One test over its tries. ``passed`` and ``judged`` count tries; ``unjudged`` too."""

    kind: VerdictKind
    passed: int = 0
    judged: int = 0
    unjudged: int = 0
    basis: Basis = Basis.NONE


class ResultKey(Frozen):
    """What two results of a test must share to be compared (:func:`rules.comparable`)."""

    case_version: str | None
    rules: int
    #: The rules its checks were made under, which a regrade under today's doesn't change.
    rules_from: int
    judge_model: str | None
    pass_marks: tuple[tuple[str, float], ...]
