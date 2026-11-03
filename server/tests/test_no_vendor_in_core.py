"""Core knows no app and no vendor: all of that is behind an adapter's module. Comments and
docstrings count, since they are how the knowledge creeps back in; docs/ doesn't."""

import re
from pathlib import Path

import evalution

ROOT = Path(evalution.__file__).parent
ADAPTERS = "adapters/"

VENDOR = re.compile(
    r"(?<![a-z])(langgraph|langchain)(?![a-z])",
    re.IGNORECASE,
)
#: ``word -> file -> why it may stay``.
ALLOWED = {
    "langchain": {"traces/setup.py": "the instrumentation package it tells any app to install"},
}

PHOENIX = re.compile(r"phoenix", re.IGNORECASE)
#: A trace source, at home here.
PHOENIX_HOME = ("traces/", "config.py")
#: Where else it may be named, and why.
PHOENIX_ALLOWED = {
    "api/": "the API's phoenix field and route descriptions, and starting evalution's own",
    "cli.py": "serve starts evalution's own, and its messages name it",
    "connect/": "a target's phoenix: block, the check's phoenix part, and the steps that name it",
    "targets.py": "reads a target's phoenix: block",
    "project/understanding/computed.py": "the page's label for where the traces are",
    "writer/tools.py": "the test writer is told a target has no Phoenix project",
}


def _core_files() -> dict[str, str]:
    files = {path.relative_to(ROOT).as_posix(): path for path in ROOT.rglob("*.py")}
    return {
        name: path.read_text(encoding="utf-8")
        for name, path in files.items()
        if not name.startswith(ADAPTERS)
    }


def vendors_in(name: str, text: str) -> list[str]:
    """What *text*, core file *name*, names that it shouldn't, with the line."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for match in VENDOR.finditer(line):
            if name not in ALLOWED.get(match[1].lower(), {}):
                found.append(f"{name}:{n}: {match[0]}")
        if (
            PHOENIX.search(line)
            and not name.startswith(PHOENIX_HOME)
            and not name.startswith(tuple(PHOENIX_ALLOWED))
        ):
            found.append(f"{name}:{n}: Phoenix")
    return found


def test_no_vendor_in_core() -> None:
    files = _core_files()
    assert "runs/runner.py" in files and "adapters/langgraph.py" not in files
    found = [hit for name, text in files.items() for hit in vendors_in(name, text)]
    assert not found, "Move this behind an adapter's INFO:\n" + "\n".join(found)


def test_the_guard_sees_what_it_is_for() -> None:
    for line in (
        '"langgraph" in text',
        "from evalution.traces.mirror import PhoenixStore",
    ):
        assert vendors_in("runs/runner.py", line), line
    for line in ("the phoenix: key", "langchain"):
        assert not vendors_in("traces/setup.py", line), line
