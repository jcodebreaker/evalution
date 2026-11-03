"""Whether a test can run on a target at all, asked before anything is spent: a test the app can't
serve says nothing of the app, and failing it late reads as if it did."""

from __future__ import annotations

from evalution.adapters.base import Capabilities, Isolation
from evalution.core.case import CONTEXT_CHECKS, Case


def why_not(case: Case, capabilities: Capabilities, traced: bool) -> str | None:
    """Why *case* can't run here, in plain words; ``None`` when it can."""
    missing = [kind for kind in case.needs_clean if kind not in capabilities.starts_clean()]
    if missing:
        return (
            f"it needs a try to start with no earlier {' or '.join(missing)}, and this app can't "
            "promise that"
        )
    for turn in case.turns:
        if turn.files and not capabilities.files:
            return "it sends a file, and this app's adapter takes none"
        if turn.stop_after and not capabilities.stop:
            return "it presses Stop, and this app's adapter can't"
        if turn.new_conversation and not capabilities.conversation_break:
            return "it starts a new conversation, and this app's adapter can't"
        if turn.expect_blocked and not capabilities.blocks:
            return "it expects the app to stop and wait, and this app's adapter can't tell"
        if turn.fresh and capabilities.isolation is Isolation.NONE:
            return "it needs a fresh start, and this app's adapter can't give one"
        for key in turn.state or {}:
            if key in CONTEXT_CHECKS:
                if not traced:
                    return f"{key} reads traces, and this app has none"
            elif key not in capabilities.state_checks:
                return f"this app's adapter can't answer the state check {key}"
    return None
