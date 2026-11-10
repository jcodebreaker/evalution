"""The bundled 30-question size of LongMemEval: chosen the same way every time from the source file,
with every kind and some unanswerable questions, each of which loads into a valid test."""

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest

from evalution.benchmarks import longmemeval
from evalution.benchmarks.longmemeval import SIZES, bundle, install, to_case
from evalution.storage import Database
from evalution.suite.cases import parse_case

KINDS = {
    "single-session-user",
    "single-session-preference",
    "multi-session",
    "temporal-reasoning",
    "knowledge-update",
}
BUNDLED = Path(longmemeval.__file__).parent / "longmemeval_data" / "short.json"
#: The copy of ``longmemeval_oracle.json`` the bundle was made from (docs/benchmarks.md).
SOURCE_SHA256 = "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c"


def _kind(entry: dict) -> str:
    return "unanswerable" if "_abs" in entry["question_id"] else entry["question_type"]


def _shipped() -> list[dict]:
    return json.loads(BUNDLED.read_text())


def _source_entry(qid: str, kind: str) -> dict:
    """A question as the source file has it, with an assistant reply that must not be kept."""
    return {
        "question_id": qid,
        "question_type": kind,
        "question": f"What about {qid}?",
        "answer": f"answer {qid}",
        "question_date": "2023/05/30",
        "haystack_session_ids": ["s1", "s2"],
        "answer_session_ids": ["s2"],
        "haystack_dates": ["2023/05/20", "2023/05/21"],
        "haystack_sessions": [
            [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "recorded"}],
            [{"role": "user", "content": f"the fact of {qid}"}],
        ],
    }


def _source() -> list[dict]:
    entries = [_source_entry(f"{kind}-{n}", kind) for kind in sorted(KINDS) for n in range(12)]
    entries += [_source_entry(f"asst{n}", "single-session-assistant") for n in range(12)]
    entries += [_source_entry(f"abs{n}_abs", "multi-session") for n in range(4)]
    entries += [_source_entry(f"tabs{n}_abs", "temporal-reasoning") for n in range(4)]
    return entries


def _bundle_from(entries: list[dict], tmp_path: Path, monkeypatch) -> list[dict]:
    data = tmp_path / "data"
    folder = data / "benchmarks" / "longmemeval"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "longmemeval_oracle.json").write_text(json.dumps(entries))
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    monkeypatch.setattr(longmemeval, "_BUNDLED", out)
    bundle("short", data)
    return json.loads((out / "short.json").read_text())


def test_the_short_size_is_30_questions_from_the_short_histories() -> None:
    assert SIZES["short"] == ("longmemeval_oracle.json", 30)


def test_bundling_the_short_size_picks_the_same_questions_every_time(tmp_path, monkeypatch) -> None:
    entries = _source()
    first = _bundle_from(entries, tmp_path, monkeypatch)
    again = _bundle_from(list(reversed(entries)), tmp_path, monkeypatch)
    assert [e["question_id"] for e in first] == [e["question_id"] for e in again]
    counts = Counter(_kind(e) for e in first)
    assert counts == {kind: 5 for kind in [*KINDS, "unanswerable"]}
    assert all(e["question_type"] != "single-session-assistant" for e in first)
    said = [turn for e in first for session in e["haystack_sessions"] for turn in session]
    assert said and all(turn["role"] == "user" for turn in said), "no recorded replies kept"


def test_the_shipped_short_size_holds_every_kind_and_unanswerable_questions() -> None:
    shipped = _shipped()
    assert len(shipped) == 30 and len({e["question_id"] for e in shipped}) == 30
    counts = Counter(_kind(e) for e in shipped)
    assert set(counts) == KINDS | {"unanswerable"}
    assert counts["unanswerable"] >= 4
    assert max(counts.values()) - min(counts.values()) <= 1, "spread evenly"
    assert all(e["question_type"] != "single-session-assistant" for e in shipped)


def test_each_shipped_question_loads_into_a_valid_test() -> None:
    for entry in _shipped():
        case = to_case(entry)
        stem = entry["question_id"].lower()
        test = parse_case(case, stem)
        assert test.source.startswith("longmemeval:") and test.rubric
        assert test.turns[-1].is_message and entry["question"] in case["turns"][-1]["message"]
        assert ("memory_holds" in str(case["turns"])) is ("_abs" not in entry["question_id"])
        assert case["limits"]["tries"] == 1


def test_the_short_size_installs_without_a_download(tmp_path: Path) -> None:
    docs, data = Database.open(tmp_path / "db").docs(), tmp_path / "data"
    assert install("short", "bot", docs, data) == ("longmemeval-short", 30)
    assert not data.exists(), "nothing was fetched"


@pytest.mark.skipif(
    not os.environ.get("LONGMEMEVAL_ORACLE"),
    reason="set LONGMEMEVAL_ORACLE to a downloaded longmemeval_oracle.json to check the bundle",
)
def test_the_shipped_bundle_rebuilds_from_the_source_file(tmp_path, monkeypatch) -> None:
    source = Path(os.environ["LONGMEMEVAL_ORACLE"])
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    rebuilt = _bundle_from(json.loads(source.read_text()), tmp_path, monkeypatch)
    assert rebuilt == _shipped()
