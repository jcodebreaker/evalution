"""First difference: two tries lined up by what each agent did, not by step number."""

from typing import Any

from evalution.review.divergence import Step, Tried, divergence, first_divergence


def _s(tool: str | None, span: str, actor: str = "main", **args: Any) -> Step:
    return Step(tool, args, span, None, actor)


def _reply(span: str) -> Step:
    return _s(None, span)


def test_an_early_harmless_extra_doesnt_move_the_marker() -> None:
    passing = [_s("a", "pa"), _s("b", "pb"), _s("c", "pc"), _reply("pr")]
    failing = [_s("x", "fx"), _s("a", "fa"), _s("b", "fb"), _s("d", "fd"), _reply("fr")]
    found = first_divergence(passing, failing)
    assert found is not None
    assert (found.kind, found.pass_span, found.fail_span) == ("tool", "pc", "fd")
    assert found.detail == "d instead of c"
    assert found.step == 3


def test_a_call_left_out_is_missing_at_the_step_the_failing_try_took_instead() -> None:
    passing = [_s("a", "pa"), _s("b", "pb"), _reply("pr")]
    failing = [_s("a", "fa"), _reply("fr")]
    found = first_divergence(passing, failing)
    assert found is not None
    assert (found.kind, found.detail, found.pass_span, found.fail_span) == (
        "missing",
        "b not called",
        "pb",
        "fr",
    )


def test_args_and_context_are_read_on_steps_lined_up() -> None:
    passing = [_s("a", "pa", q="churn"), _reply("pr")]
    failing = [_s("x", "fx"), _s("a", "fa", q="revenue"), _reply("fr")]
    found = first_divergence(passing, failing)
    assert found is not None and found.kind == "args" and found.fail_span == "fa"


def test_a_helpers_call_lines_up_only_with_a_helpers() -> None:
    passing = [_s("search", "pa", actor="helper"), _reply("pr")]
    failing = [_s("search", "fa"), _reply("fr")]
    found = first_divergence(passing, failing)
    assert found is not None and found.kind == "tool"


def test_only_an_extra_call_is_still_said() -> None:
    passing = [_s("a", "pa"), _reply("pr")]
    failing = [_s("a", "fa"), _s("delete", "fd"), _reply("fr")]
    found = first_divergence(passing, failing)
    assert found is not None
    assert (found.kind, found.fail_span, found.detail) == (
        "missing",
        "fd",
        "delete called; the passing try didn't",
    )
    assert first_divergence(passing, [_s("a", "fa"), _reply("fr")]) is None


def test_of_several_pairs_the_one_alike_longest_is_taken() -> None:
    good = Tried("r", 1, [_s("a", "pa"), _s("b", "pb"), _s("c", "pc"), _reply("pr")])
    early = Tried("r", 2, [_s("z", "f2"), _reply("f2r")])
    late = Tried("r", 3, [_s("x", "fx"), _s("a", "fa"), _s("b", "fb"), _s("d", "fd"), _reply("fr")])
    found = divergence([good], [early, late])
    assert found is not None and found["fail"] == {"run": "r", "n": 3, "span": "fd"}
