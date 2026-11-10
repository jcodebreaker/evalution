"""The test writer's tools as the model sees them: names, descriptions and JSON schemas."""

from __future__ import annotations

from typing import Any

from evalution.isolation.recipe import KINDS as RECIPE_KINDS
from evalution.project import understanding
from evalution.review.findings import SUSPECTS
from evalution.writer.isolating import MAX_FAILED, OPS, STRATEGIES
from evalution.writer.paging import MAX_REVISIONS

STEP_KINDS = ("message", "generated", "check")
_NEW_STORE = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "kind": {"type": "string"},
        "locator": {"type": "string"},
        "moved_by": {"type": "array", "items": {"type": "string"}},
        "keyed_by": {"type": "string"},
        "strategy": {"type": "string", "enum": list(STRATEGIES)},
    },
    "required": ["id", "kind", "locator"],
}

#: Where an element is looked for, and on which turn: a page check's element (decided by code).
_ELEMENT = {
    "type": "object",
    "description": "Found as a person finds it: role and accessible name, a test id, or its text",
    "properties": {
        "role": {"type": "string"},
        "name": {"type": "string"},
        "test_id": {"type": "string"},
        "text": {"type": "string"},
        "within": {"type": "string", "enum": ["reply", "page"]},
        "count": {
            "type": ["integer", "object"],
            "description": "How many: a number, or {min, max}",
            "properties": {"min": {"type": "integer"}, "max": {"type": "integer"}},
        },
        "turn": {"type": "integer"},
    },
}
#: A test's checks of what its page showed (played through the page, with an approved map).
PAGE_CHECKS = {
    "type": "object",
    "description": (
        "What each turn's page showed. shows/hides: elements, decided by code: use them whenever "
        "the element has a role and name, a test id or text. looks: a yes/no question about a "
        "turn's screenshot, read by a vision model, only for what has none (an image, a colour, "
        "a layout). Without a page map they are saved and wait for the page to be learned."
    ),
    "properties": {
        "shows": {"type": "array", "items": _ELEMENT},
        "hides": {"type": "array", "items": _ELEMENT},
        "looks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"question": {"type": "string"}, "turn": {"type": "integer"}},
                "required": ["question"],
            },
        },
    },
}

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
            "properties": {"page": PAGE_CHECKS},
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
        "(verdict, fix, picked, selected), so you can keep ids. With area: what is understood "
        "of one area of the code, named by its id, a file in it, or a tool, prompt, agent or "
        "store it holds; before you write a test that relies on an area, ask for it: one not "
        "learned yet is learned first (this can take a few minutes).",
        {
            "area": {
                "type": "string",
                "description": "An area's id, a file path, or a tool, prompt or store name",
            }
        },
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
    _tool(
        "note_file",
        "Keep up to 6 exact facts of a file you read this turn (read_code, walk_code) that the "
        "Details of what you understood should hold: conditions, order, refusals, prompt rules "
        "quoted. Only when the file is not summarized already; kept until the file changes.",
        {"path": _STR, "facts": {"type": "array", "items": _STR}},
        ["path", "facts"],
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
        "read_isolation",
        "How this target's tries are kept apart: the saved isolation plan (its stores, what is "
        "left alone, its report), files its evidence cites that changed since, what the code "
        "read named (candidates, c1...), what the probe and failed rehearsals found (findings, "
        "p1...), settings not tied to a store, and this session's proposal and rehearsals left.",
        {},
        [],
    ),
    _tool(
        "propose_isolation_plan",
        "Propose edits to the isolation plan, each checked against the evidence; nothing is "
        "saved or rehearsed until the admin approves. ops: set (store, and any of moved_by, "
        "keyed_by, strategy), add (new: {id, kind, locator, moved_by?, keyed_by?, strategy?}, "
        "and cite: the candidate or finding id that shows it), remove (store), exclude (entry: "
        "something the probe found, to leave alone), include (entry: reset it again). A refusal "
        "says why and keeps nothing.",
        {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": list(OPS)},
                        "store": {"type": "string", "description": "A store id in the plan"},
                        "new": _NEW_STORE,
                        "moved_by": {"type": "array", "items": _STR},
                        "keyed_by": {"type": ["string", "null"]},
                        "strategy": {"type": "string", "enum": list(STRATEGIES)},
                        "cite": {"type": "string", "description": "A candidate or finding id"},
                        "entry": _STR,
                    },
                    "required": ["op"],
                },
            },
            "why": {"type": "string", "description": "One line for the admin: why this change"},
        },
        ["changes"],
    ),
    _tool(
        "rehearse_isolation",
        "Rehearse the isolation proposal the admin approved (default: the latest) on "
        "evalution's own copy of the app: its report, and proven or failed. A proven plan is "
        f"saved. After {MAX_FAILED} failed rehearsals in a session, none are run: ask the admin.",
        {"plan_id": _STR},
        [],
    ),
    _tool(
        "read_build_recipe",
        "How a version of this target's code is built into evalution's own names: the saved "
        "build recipe (kind, the variables naming its images, services and profiles built, a "
        "process app's setup, its evidence and status), files changed under it, what the rule "
        "finds in the app's files now, and the last build's time and size.",
        {},
        [],
    ),
    _tool(
        "propose_build_recipe",
        "Adjust the build recipe, checked against the app's files and saved as a new proposed "
        "version (undoable); nothing is built. kind: compose_env (every built image named "
        "${name_var}/<name>:${tag_var}), compose_override (an override renames each built "
        "image) or process. services and profiles: what compose builds. setup: a process "
        "app's installer commands ({run: [...], cwd}). A refusal says why and saves nothing.",
        {
            "kind": {"type": "string", "enum": list(RECIPE_KINDS)},
            "name_var": _STR,
            "tag_var": _STR,
            "services": {"type": "array", "items": _STR},
            "profiles": {"type": "array", "items": _STR},
            "setup": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"run": {"type": "array", "items": _STR}, "cwd": _STR},
                    "required": ["run"],
                },
            },
            "why": {"type": "string", "description": "One line for the admin: why this change"},
        },
        ["kind"],
    ),
    _tool(
        "read_page_code",
        "The target's page code, read for a page map: candidates for each entry (the message "
        "box actions.type, Send actions.send, the replies replies.in, New chat "
        "actions.new_chat, busy signs and Stop busy, sign_in), each with file and line and a "
        "suggested locator where the code names one, and the page's routes (session: true when "
        "a route carries a conversation's id). From the code, not the page.",
        {},
        [],
    ),
    _tool(
        "look_at_page",
        "Open the target's page on evalution's own copy of the app (refused without one), "
        "signed in as sign_in says, and return its accessibility snapshot: roles and names. "
        "Nothing is clicked or typed, and nothing but a read leaves the page.",
        {
            "url": {"type": "string", "description": "A path on the copy's page, like /chat"},
            "sign_in": {
                "type": "object",
                "description": "As a page map's sign_in: {headers: {...}} with templates like "
                "{person.email} and {secret:NAME}, {form: {...}}, {cookie: {...}}, or none",
            },
        },
        [],
    ),
    _tool(
        "try_page_entry",
        "Try one entry of a page map on evalution's copy (map: the whole map; default your "
        "latest proposal): found or not, and what changed in the snapshot. Locating only: "
        "actions.type fills a fixed harmless text and clears it; Send, New chat and Stop are "
        "found, never clicked; no message is ever sent.",
        {
            "entry": {
                "type": "string",
                "enum": [
                    "url",
                    "ready",
                    "sign_in",
                    "open_session",
                    "actions.type",
                    "actions.send",
                    "actions.new_chat",
                    "actions.stop",
                    "actions.attach",
                    "replies.in",
                    "replies.item",
                    "replies.mine",
                    "busy",
                ],
            },
            "map": {"type": "object", "description": "A whole page map, as a page: block"},
        },
        ["entry"],
    ),
    _tool(
        "propose_page_map",
        "Propose a page map (as a page: block: url, ready, sign_in, open_session, actions, "
        "replies, busy, catch_up_seconds, evidence). Checked as a run reads it; elements by "
        "{role, name} or {test_id}, every url from {copy_page}. Each of ready, actions.type, "
        "actions.send and replies.in needs evidence: {file, line} from read_page_code, or a "
        "try_page_entry this session that found it. Kept as a draft, nothing saved.",
        {
            "map": {"type": "object", "description": "The page: block"},
            "why": {"type": "string", "description": "One line for the admin"},
        },
        ["map"],
    ),
    _tool(
        "rehearse_page_map",
        'Play one real message ("Hello, this is a test.") through the page map the admin '
        "approved (default: the latest) on evalution's copy: it spends one turn of the app. "
        "Returns what the page showed; a map whose reply showed is kept as tried, for the "
        f"admin to approve. After {MAX_REVISIONS} failed revisions, none are run: ask the admin.",
        {"plan_id": _STR},
        [],
    ),
    _tool(
        "reply",
        "Say something to the admin. The only way to speak. plan: when you propose a "
        "case. run: a run this message is about. review: after reading a run. isolation: the id "
        "of an isolation proposal to show for approval (its changes are added in words). "
        "page_map: the id of a page map proposal to show for approval (added in words). "
        "learn_page: true gives the admin one button to learn the page now, when the target "
        "has no page map; a plan with page checks gets it without asking.",
        {
            "text": _STR,
            "plan": _PLAN_SCHEMA,
            "run": _RUN,
            "review": _REVIEW_SCHEMA,
            "isolation": _STR,
            "page_map": _STR,
            "learn_page": {"type": "boolean"},
        },
        ["text"],
    ),
]
