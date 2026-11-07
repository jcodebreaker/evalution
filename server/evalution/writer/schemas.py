"""The test writer's tools as the model sees them: names, descriptions and JSON schemas."""

from __future__ import annotations

from typing import Any

from evalution.project import understanding
from evalution.review.findings import SUSPECTS

STEP_KINDS = ("message", "generated", "check")

_PLAN_SCHEMA = {
    "type": "object",
    "description": "When you propose a case: what it does and its tries.",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(STEP_KINDS)},
                    "title": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["kind", "title"],
            },
        },
        "tries": {"type": "integer"},
        "case": {"type": "string", "description": "The case id"},
        "replaces": {
            "type": "string",
            "description": (
                "The id of the test this one would replace, when it does. The plan then shows "
                "the admin what changes in what it checks."
            ),
        },
        "file": {
            "type": "object",
            "description": (
                "The whole test as save_case will save it. Approval covers exactly this: "
                "any change is a new plan. Leave limits.seconds out unless the admin named a "
                "time: the plan fills it in from how long messages take here."
            ),
        },
    },
    "required": ["steps", "tries", "case", "file"],
}
_REVIEW_SCHEMA = {
    "type": "object",
    "description": "After reading a run: what happened and where each fault is.",
    "properties": {
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "suspected": {"type": "string", "enum": list(SUSPECTS)},
                    "text": {"type": "string"},
                    "case": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["suspected", "text"],
            },
        },
        "proposal": {"type": "string", "description": "One concrete next change, if any"},
    },
    "required": ["summary", "findings"],
}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required},
    }


_STR = {"type": "string"}
_RUN = {"type": "string", "description": "A run id"}
_EVIDENCE = {
    "type": "array",
    "description": "Where you saw it: ref is the file (and section), or '9 asks' for traces",
    "items": {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(understanding.EVIDENCE_KINDS)},
            "ref": _STR,
        },
        "required": ["kind", "ref"],
    },
}
_NODE = {
    "type": "object",
    "properties": {
        "id": _STR,
        "kind": {"type": "string", "enum": list(understanding.NODE_KINDS)},
        "title": _STR,
        "sub": {"type": "string", "description": "A few words under the title"},
        "tools": {"type": "array", "items": _STR},
        "evidence": _EVIDENCE,
    },
    "required": ["id", "kind", "title", "evidence"],
}
_CLAIM = {
    "type": "object",
    "properties": {"id": _STR, "text": _STR, "evidence": _EVIDENCE},
    "required": ["id", "text", "evidence"],
}
TOOLS = [
    _tool("list_sets", "This target's sets, with their case count and latest run.", {}, []),
    _tool(
        "list_cases",
        "A set's cases with their latest verdict, scores and stale mark "
        "(default: this session's set).",
        {"set": _STR},
        [],
    ),
    _tool("read_case", "One case file as written.", {"case": _STR, "set": _STR}, ["case"]),
    _tool(
        "read_docs",
        "A file or folder under docs/, e.g. docs/case-format.md.",
        {"path": _STR},
        ["path"],
    ),
    _tool(
        "approve",
        "Record that the admin approved a plan, only when their last message clearly "
        "said to add it. Never for a question or a change request.",
        {"plan_id": _STR},
        ["plan_id"],
    ),
    _tool(
        "save_case",
        "Add the test of an approved plan to this session's set; returns its id. Give plan_id "
        "alone: the approved test is what is saved. replace: the id of a case you saved earlier "
        "in this session, when the plan named it in replaces.",
        {"plan_id": _STR, "case": {"type": "object"}, "replace": _STR},
        ["plan_id"],
    ),
    _tool(
        "save_fixture",
        "Write a data file a case needs (CSV, JSON, text) into this set's fixtures/ folder, for "
        "an approved plan. name: a plain file name like orders.csv; the case refers to it as "
        "fixtures/<name>. Never ask the admin to create files: list them in the plan and write "
        "them here once it is approved.",
        {"plan_id": _STR, "name": _STR, "content": _STR},
        ["plan_id", "name", "content"],
    ),
    _tool(
        "estimate",
        "How many tries and messages running this session's set, or one case of it, would "
        "take, for the plan. tries: overrides each case's tries (1-20). You never run anything.",
        {"case": _STR, "tries": {"type": "integer"}},
        [],
    ),
    _tool(
        "read_run",
        "A run's totals, its cases with scores, and its analysis.",
        {"run": _RUN},
        ["run"],
    ),
    _tool(
        "read_try",
        "One try of a case in a run: steps, rubric, analysis, reply, error, "
        "context assembly, trajectory and trace link.",
        {"run": _RUN, "case": _STR, "try": {"type": "integer"}},
        ["run", "case", "try"],
    ),
    _tool(
        "read_trace",
        "A try's traces: model and tool call counts and, with find, where a word or phrase "
        "appears across what the person said, the replies, what models were sent and tool results.",
        {"run": _RUN, "case": _STR, "try": {"type": "integer"}, "find": _STR},
        ["run", "case", "try"],
    ),
    _tool("read_profile", "The target's profile.", {}, []),
    _tool(
        "read_understanding",
        "What you last saved with write_understanding, with the admin's marks on it "
        "(verdict, fix, picked, selected), so you can keep ids.",
        {},
        [],
    ),
    _tool(
        "write_understanding",
        "Save what you understood of the target; the engine adds the numbers, the "
        "diagram's edges and the paths from the traces, and renders the profile from it. "
        "Every node and claim needs evidence; a node's tools must be in the trace digest or "
        "in a file it cites. Saving also takes a new fingerprint, keeps the previous version "
        "and clears the drift list.",
        {
            "one_line": {"type": "string", "description": "One sentence: what the app is"},
            "nodes": {
                "type": "array",
                "description": "The boxes of how it works: the person, the agent, its tools, "
                "workers it hands work to, state it keeps.",
                "items": _NODE,
            },
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "enum": list(understanding.SECTION_IDS)},
                        "title": _STR,
                        "note": {"type": "string", "description": "e.g. few real conversations"},
                        "claims": {
                            "type": "array",
                            "items": _CLAIM,
                        },
                    },
                    "required": ["id", "title", "claims"],
                },
            },
            "priorities": {
                "type": "array",
                "description": "What matters most to test",
                "items": {
                    "type": "object",
                    "properties": {"id": _STR, "title": _STR, "picked": {"type": "boolean"}},
                    "required": ["id", "title"],
                },
            },
            "starters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": _STR,
                        "title": _STR,
                        "what": _STR,
                        "why": _STR,
                        "priority": {"type": "string", "description": "A priority id"},
                        "library": {"type": "string", "description": "A library test id"},
                    },
                    "required": ["id", "title", "what", "why"],
                },
            },
            "step_labels": {
                "type": "object",
                "description": "tool name -> a plain step name ('Looks something up'); key "
                f"{understanding.NO_TOOLS!r} for an ask that used no tools",
            },
            "expected_paths": {
                "type": "array",
                "description": "Paths the code shows, as step names, even if never seen",
                "items": {"type": "array", "items": _STR},
            },
        },
        ["one_line", "nodes", "sections", "priorities", "starters"],
    ),
    _tool(
        "patch_understanding",
        "Change a few parts of the understanding saved, the rest left exactly as it is; saved "
        "like write_understanding. A node or claim with an id that exists replaces it; a new id "
        "adds it (a claim to its section).",
        {
            "note": {
                "type": "string",
                "description": "One line for the admin: what changed in what you understood",
            },
            "one_line": {"type": "string", "description": "Only when it changes"},
            "nodes": {"type": "array", "items": _NODE},
            "claims": {
                "type": "array",
                "items": {
                    **_CLAIM,
                    "properties": {
                        **_CLAIM["properties"],
                        "section": {"type": "string", "enum": list(understanding.SECTION_IDS)},
                    },
                    "required": [*_CLAIM["required"], "section"],
                },
            },
            "remove": {
                "type": "array",
                "description": "What is no longer true: 'claim:<id>' or 'node:<id>'",
                "items": _STR,
            },
        },
        ["note"],
    ),
    _tool(
        "read_trace_digest",
        "A compact digest of the target's recent sessions: tools with "
        "argument shapes and frequency, trajectory shapes, the main system prompt's sections, "
        "and typical time and tokens per ask (one message and what it set off; a case's "
        "limits cover its asks, not a conversation's lifetime). Eval traffic is left out.",
        {"sessions": {"type": "integer"}},
        [],
    ),
    _tool(
        "list_code", "Files of the target's code you may read, matching a glob.", {"glob": _STR}, []
    ),
    _tool(
        "read_code",
        "One file of the target's source (no tests, build output or lockfiles; never secrets).",
        {"path": _STR},
        ["path"],
    ),
    _tool(
        "walk_code",
        "A definition of the target's code (a function, class, prompt, tool, route... by name, "
        "or every one of a file by its path): its code, what it uses and what uses it, from the "
        "code map. depth 2 adds what is one step further, by signature.",
        {"name": _STR, "depth": {"type": "integer"}},
        ["name"],
    ),
    _tool("read_drift", "What changed in the target since its profile was written.", {}, []),
    _tool(
        "diff_code",
        "The diff of the target's watched files since a commit (a drift entry's 'since').",
        {"since": _STR},
        ["since"],
    ),
    _tool(
        "note_drift",
        "Write a short behaviour summary on a drift entry.",
        {"change": _STR, "summary": _STR},
        ["change", "summary"],
    ),
    _tool(
        "list_library",
        "The ready-made stateful tests, each with what it checks, why, what it needs and "
        "whether it fits this target (fits / needs ... / not_applicable).",
        {},
        [],
    ),
    _tool(
        "adapt_template",
        "A library test for this target. Without values: its case skeleton and each "
        "placeholder with what to fill it with. With values (one per placeholder: a number, "
        "text for a made-up value, {value, evidence} for a value from the profile, evidence "
        "quoting the profile line with its tag): the filled case, to show as a plan.",
        {"id": _STR, "values": {"type": "object"}},
        ["id"],
    ),
    _tool(
        "list_conversations",
        "The app's recent real conversations from its traces, newest first: each with "
        "session_id, trace_id, the ask, the tools called and the reply.",
        {"limit": {"type": "integer", "description": "1 to 50, default 20"}},
        [],
    ),
    _tool(
        "draft_from_conversation",
        "A case drafted from one past conversation: session_id (every message of it) or "
        "trace_id (one message). Returned, not saved: show it as a plan.",
        {"session_id": _STR, "trace_id": _STR},
        [],
    ),
    _tool(
        "reply",
        "Say something to the admin. The only way to speak. plan: when you propose a "
        "case. run: a run this message is about. review: after reading a run.",
        {"text": _STR, "plan": _PLAN_SCHEMA, "run": _RUN, "review": _REVIEW_SCHEMA},
        ["text"],
    ),
]
