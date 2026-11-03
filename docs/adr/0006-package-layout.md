# 0006: The server package has layers, and a linter holds them

> **Status:** Accepted 2026-11-01 by the owner.

`server/evalution/` was thirty flat modules. It had no import cycles only because one `TYPE_CHECKING` in `impact.py` hid the real one, and responsibilities were mixed in ways folders alone would not fix: `run.py` held the runner, the read models, the estimates and the verdict rules; `grade.py` held the JSON helpers every model call uses; `library.py` read targets; `traces/view.py` needed the adapters' `Section`. Moving those files as they stood would have created six package cycles.

## Decision

Each layer imports only from below it. `lint-imports` enforces this, including imports under `TYPE_CHECKING` and inside functions; the contract in `server/pyproject.toml` has no exceptions and lists every top-level module.

```
cli
api
writer | connect
runs
review
project
targets
judging | results
suite | traces | code | settings
adapters
llm
config
core
```

Parts on one line don't import each other.

- **`core` is what everything shares:** a case, a call, a section, and the rules that read a stored try. It imports nothing else of evalution.
- **Read models sit below `runs` and `project`.** `results/views.py` reads a stored run, which is a document; it needs no runner, no target and no model. The Home page, the understanding and the change card read results too, so if views lived with the runner, reading a result would mean depending on what plays a run. `latest_rows` is told how to tell a stale case instead of importing `drift`.
- **`project` depends on a `History` protocol, not on `Runs`.** That is what ended the hidden cycle: the runner calls into `project`, and `project` sees only `targets`, `llm`, `caller` and `all`.
- **`review` sits between them,** because the runner writes findings after a run and the health page reads `project`.
- **`adapters` sit below `traces`,** because telling eval traffic from real use needs every adapter's marks, and an adapter needs nothing of the traces.
- **Private names stay private:** ruff's `SLF001` is on for the package. Tests may reach in, because they drive the agent's tools directly.

## Why a pure move came first

Behaviour is pinned by golden views over neutral run files (`server/tests/test_golden_views.py`), and they did not change byte for byte across the move. Symbols moved first with files still flat, then files moved with renames and import rewrites only, so `git log --follow` and review both still work. Splitting the big files (`create_router`, `agent.py`, `understanding.py`) and fixing what the move turned up, such as `case_row` counting tries without traces, are later work on purpose: a diff that moves code and changes it can't be reviewed as either.
