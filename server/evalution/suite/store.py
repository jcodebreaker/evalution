"""Where tests are kept: documents of the workspace (:mod:`evalution.storage`), one kind each,
keyed by path. What their text may say is :mod:`evalution.suite.cases`.

* :data:`SET`, ``<target>/<set>``: the set's ``set.json``, as text;
* :data:`CASE`, ``<target>/<set>/<case>``: a case's ``<case>.case.json``, the text exactly as
  written, since the page edits it as text and its version is the text's hash;
* :data:`FILE`, ``<target>/<set>/<path>``: any other file of the set, e.g. ``fixtures/orders.csv``
  a turn sends, or the ``plan.json`` that chose a benchmark: text as text, other bytes as
  ``{"base64": ...}``.

:func:`export_sets` and :func:`import_sets` carry them to and from folders laid out as
``<folder>/<target>/<set>/set.json``, ``*.case.json`` and the files, which is how a repo keeps its
tests for CI.
"""

from __future__ import annotations

import base64
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from evalution.core.case import CASE_SUFFIX, SLUG, Case, EvalSet
from evalution.storage import Docs
from evalution.suite.cases import (
    CaseError,
    as_text,
    is_benchmark,
    meaning_changes,
    parse_case,
    read_case,
    read_object,
    read_set,
    slugify,
    with_pass_marks,
)

#: The set's ``set.json``, under ``<target>/<set>``.
SET = "set"
#: A case's text, under ``<target>/<set>/<case>``.
CASE = "case"
#: Any other file of a set, under ``<target>/<set>/<path>``.
FILE = "set-file"
SET_FILE = "set.json"


def _key(target: str, slug: str, *rest: str) -> str:
    return "/".join((target, slug, *rest))


def _set_where(target: str, slug: str) -> str:
    return f"{target}/{slug}/{SET_FILE}"


def _case_where(target: str, slug: str, stem: str) -> str:
    return f"{target}/{slug}/{stem}{CASE_SUFFIX}"


# Sets


def _set_text(docs: Docs, target: str, slug: str) -> str:
    """:raises CaseError: An unsafe id or no such set."""
    if not SLUG.match(slug):
        raise CaseError(f"{slug}: not a set id")
    text = docs.get(SET, _key(target, slug))
    if not isinstance(text, str):
        raise CaseError(f"{target}/{slug}: no such set")
    return text


def load_set(docs: Docs, target: str, slug: str) -> EvalSet:
    """The set *slug* of *target*.

    :raises CaseError: An unsafe id, no such set or a bad ``set.json``.
    """
    return read_set(_set_text(docs, target, slug), _set_where(target, slug), slug)


def save_set(docs: Docs, target: str, slug: str, data: dict[str, Any]) -> None:
    """Write *data* as the ``set.json`` of set *slug* of *target*, made or replaced; its cases and
    files stay."""
    if not SLUG.match(slug):
        raise CaseError(f"{slug}: not a set id")
    docs.put(SET, _key(target, slug), as_text(data))


def save_pass_marks(docs: Docs, target: str, slug: str, value: Any) -> dict[str, float]:
    """Set the pass marks of set *slug* (:func:`~evalution.suite.cases.with_pass_marks`); the
    marks now in force.

    :raises CaseError: An unknown set, or marks that are not numbers from 0 to 1.
    """
    text = _set_text(docs, target, slug)
    text, marks = with_pass_marks(text, _set_where(target, slug), value)
    docs.put(SET, _key(target, slug), text)
    return marks


def list_sets(docs: Docs, target: str) -> list[str]:
    """The set ids of *target*, sorted."""
    return [key.split("/", 1)[1] for key in docs.keys(SET, f"{target}/")]


def own_sets(docs: Docs, target: str) -> list[str]:
    """The app's own tests: its sets, benchmarks left out."""
    return [slug for slug in list_sets(docs, target) if not is_benchmark(slug)]


def stored_targets(docs: Docs) -> list[str]:
    """The targets that have a set, sorted."""
    return sorted({key.split("/", 1)[0] for key in docs.keys(SET)})


def delete_set(docs: Docs, target: str, slug: str) -> None:
    """Set *slug* of *target* gone: its ``set.json``, cases and files."""
    docs.delete(SET, _key(target, slug))
    docs.delete_prefix(CASE, _key(target, slug, ""))
    docs.delete_prefix(FILE, _key(target, slug, ""))


# Cases


def case_text(docs: Docs, target: str, slug: str, stem: str) -> str:
    """Case *stem* of set *slug* as written.

    :raises CaseError: An unsafe id or no such case.
    """
    if not SLUG.match(slug) or not SLUG.match(stem):
        raise CaseError(f"{slug}/{stem}: not a case id")
    text = docs.get(CASE, _key(target, slug, stem))
    if not isinstance(text, str):
        raise CaseError(f"{slug}/{stem}: no such test")
    return text


def load_case(docs: Docs, target: str, slug: str, stem: str) -> Case:
    """Case *stem* of set *slug*.

    :raises CaseError: An unsafe id, no such case or a bad case.
    """
    text = case_text(docs, target, slug, stem)
    return read_case(text, _case_where(target, slug, stem), stem)


def case_stems(docs: Docs, target: str, slug: str) -> list[str]:
    """The case ids of set *slug*, sorted."""
    prefix = _key(target, slug, "")
    return [key[len(prefix) :] for key in docs.keys(CASE, prefix)]


def load_cases(docs: Docs, target: str, slug: str) -> list[Case]:
    """Every case of set *slug*, by id.

    :raises CaseError: The first bad case.
    """
    prefix = _key(target, slug, "")
    found = []
    for key, text, _ in docs.items(CASE, prefix):
        stem = key[len(prefix) :]
        found.append(read_case(text, _case_where(target, slug, stem), stem))
    return found


def save_case(docs: Docs, target: str, slug: str, data: Any, stem: str | None = None) -> str:
    """Validate *data* and write it as a new case of set *slug*, named after it, or over case
    *stem* of that set when given; returns its stem. A new case never replaces one made at the
    same moment: it takes the next free name.

    :raises CaseError: An unknown set or case, or a bad case.
    """
    load_set(docs, target, slug)
    case = parse_case(data, f"{slug}/{stem or '(new case)'}")
    if stem is not None:
        case_text(docs, target, slug, stem)
        docs.put(CASE, _key(target, slug, stem), as_text(data))
        return stem
    base, n = slugify(case.name), 1
    stem = base
    while not docs.create(CASE, _key(target, slug, stem), as_text(data)):
        n += 1
        stem = f"{base}-{n}"
    return stem


def put_case(docs: Docs, target: str, slug: str, stem: str, data: Any) -> None:
    """Write *data* as case *stem* of set *slug*, made or replaced, once it reads as a case.

    :raises CaseError: An unsafe id or a bad case.
    """
    if not SLUG.match(stem):
        raise CaseError(f"{slug}/{stem}: not a case id")
    parse_case(data, _case_where(target, slug, stem), stem)
    docs.put(CASE, _key(target, slug, stem), as_text(data))


def changes_in_text(
    docs: Docs, target: str, slug: str, stem: str, text: str
) -> list[dict[str, Any]]:
    """What saving *text* over test *stem* would change in its meaning.

    :raises CaseError: An unknown set or test, text that is not JSON, or a bad test.
    """
    where = _case_where(target, slug, stem)
    now = read_object(case_text(docs, target, slug, stem), where)
    return meaning_changes(now, read_case(text, where, stem).raw)


def write_case_text(docs: Docs, target: str, slug: str, stem: str, text: str) -> Case:
    """Write *text*, as typed, over test *stem* of set *slug* once it reads as a valid test;
    returns the test as saved.

    :raises CaseError: An unknown set or test, text that is not JSON, or a bad test.
    """
    load_set(docs, target, slug)
    case_text(docs, target, slug, stem)
    text = text if text.endswith("\n") else text + "\n"
    found = read_case(text, _case_where(target, slug, stem), stem)
    docs.put(CASE, _key(target, slug, stem), text)
    return found


# A set's other files


def _file_body(data: bytes) -> Any:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return {"base64": base64.b64encode(data).decode("ascii")}


def _file_bytes(body: Any) -> bytes:
    if isinstance(body, dict):
        return base64.b64decode(body["base64"])
    return str(body).encode("utf-8")


def _file_name(name: str) -> str:
    """*name* as a path inside the set, ``a/b.csv``.

    :raises CaseError: One that would leave it, or names a set's own file.
    """
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or ".." in parts or "\\" in name:
        raise CaseError(f"{name}: must be a path inside the set")
    path = "/".join(p for p in parts if p != ".")
    if path == SET_FILE or ("/" not in path and path.endswith(CASE_SUFFIX)):
        raise CaseError(f"{name}: is the set's own file")
    return path


def write_file(docs: Docs, target: str, slug: str, name: str, data: bytes) -> str:
    """Write *data* as the set's file *name* (``fixtures/x.csv``), made or replaced; its path.

    :raises CaseError: An unknown set, or a name outside it.
    """
    load_set(docs, target, slug)
    path = _file_name(name)
    docs.put(FILE, _key(target, slug, path), _file_body(data))
    return path


def read_file(docs: Docs, target: str, slug: str, name: str) -> bytes | None:
    """The set's file *name*, ``None`` when it has none."""
    try:
        path = _file_name(name)
    except CaseError:
        return None
    body = docs.get(FILE, _key(target, slug, path))
    return None if body is None else _file_bytes(body)


def sent_files(docs: Docs, target: str, slug: str, case: Case) -> dict[str, bytes]:
    """The set's files *case*'s turns send, by name as the case writes it; those the set has."""
    found = {}
    for name in dict.fromkeys(name for turn in case.turns for name in turn.files):
        data = read_file(docs, target, slug, name)
        if data is not None:
            found[name] = data
    return found


def _files(docs: Docs, target: str, slug: str) -> list[tuple[str, bytes]]:
    prefix = _key(target, slug, "")
    return [(key[len(prefix) :], _file_bytes(body)) for key, body, _ in docs.items(FILE, prefix)]


def unpack_files(docs: Docs, target: str, slug: str, into: Path) -> Path:
    """The set's files written out under *into*, emptied first, for an adapter that is handed
    paths; *into*."""
    if into.is_dir():
        shutil.rmtree(into)
    into.mkdir(parents=True)
    for path, data in _files(docs, target, slug):
        out = into / path
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    return into


# To and from folders


def _text(data: bytes, where: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CaseError(f"{where}: must be UTF-8 text") from exc


def _put_set(docs: Docs, target: str, slug: str, files: Mapping[str, bytes]) -> None:
    """Set *slug* of *target* replaced whole by *files* (:func:`set_files`). A case file whose name
    isn't an id is left out, as a folder's is.

    :raises CaseError: An unsafe set id or path, no ``set.json``, or a file that is not UTF-8 text
        where text is wanted; nothing of the set is changed.
    """
    where = f"{target}/{slug}"
    if not SLUG.match(slug):
        raise CaseError(f"{slug}: not a set id")
    if SET_FILE not in files:
        raise CaseError(f"{where}: no {SET_FILE}")
    texts = {SET_FILE: _text(files[SET_FILE], f"{where}/{SET_FILE}")}
    others: dict[str, bytes] = {}
    for rel, data in files.items():
        if rel == SET_FILE:
            continue
        if "/" not in rel and rel.endswith(CASE_SUFFIX):
            if SLUG.match(rel[: -len(CASE_SUFFIX)]):
                texts[rel] = _text(data, f"{where}/{rel}")
        else:
            others[_file_name(rel)] = data
    delete_set(docs, target, slug)
    docs.put(SET, _key(target, slug), texts.pop(SET_FILE))
    for rel, text in texts.items():
        docs.put(CASE, _key(target, slug, rel[: -len(CASE_SUFFIX)]), text)
    for rel, data in others.items():
        docs.put(FILE, _key(target, slug, rel), _file_body(data))


def _import_set(docs: Docs, target: str, folder: Path) -> None:
    files = {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if not path.is_symlink() and path.is_file()
    }
    _put_set(docs, target, folder.name, files)


def _set_folders(folder: Path, targets: Iterable[str] | None) -> list[tuple[str, Path]]:
    names = set(targets) if targets is not None else None
    found = []
    for target in sorted(folder.iterdir()) if folder.is_dir() else []:
        if not target.is_dir() or not SLUG.match(target.name):
            continue
        if names is not None and target.name not in names:
            continue
        found += [
            (target.name, s)
            for s in sorted(target.iterdir())
            if SLUG.match(s.name) and (s / SET_FILE).is_file() and not s.is_symlink()
        ]
    return found


def import_sets(docs: Docs, folder: Path, targets: Iterable[str] | None = None) -> list[str]:
    """Every set in *folder* (``<target>/<set>/set.json``, of *targets* only when given) put in
    the workspace, each replacing the set of that name whole; ``<target>/<set>`` of each.
    Symbolic links are left out.

    :raises CaseError: A file that is not UTF-8 text where text is wanted; nothing of that set is
        changed.
    """
    done = []
    for target, path in _set_folders(folder, targets):
        _import_set(docs, target, path)
        done.append(f"{target}/{path.name}")
    return done


def set_files(docs: Docs, target: str, slug: str) -> dict[str, bytes]:
    """Set *slug* of *target* as files, by path in its folder: ``set.json``, each
    ``<case>.case.json`` and the set's other files.

    :raises CaseError: An unsafe id or no such set.
    """
    found = {SET_FILE: _set_text(docs, target, slug).encode()}
    for stem in case_stems(docs, target, slug):
        found[f"{stem}{CASE_SUFFIX}"] = case_text(docs, target, slug, stem).encode()
    found.update(_files(docs, target, slug))
    return found


def set_bundle(docs: Docs, target: str, slug: str) -> dict[str, Any]:
    """:func:`set_files` as JSON: a text file as its text, other bytes as ``{"base64": ...}``.

    :raises CaseError: An unsafe id or no such set.
    """
    return {path: _file_body(data) for path, data in set_files(docs, target, slug).items()}


def put_bundle(docs: Docs, target: str, slug: str, bundle: Any) -> None:
    """Set *slug* of *target* replaced whole by a :func:`set_bundle`, which may have come from
    another server: every path is checked as a folder's would be.

    :raises CaseError: Not a bundle, an unsafe id or path, or no ``set.json``; nothing changed.
    """
    if not isinstance(bundle, dict) or not all(
        isinstance(k, str) and (isinstance(v, str) or (isinstance(v, dict) and "base64" in v))
        for k, v in bundle.items()
    ):
        raise CaseError(f"{target}/{slug}: not a set's files")
    try:
        files = {path: _file_bytes(body) for path, body in bundle.items()}
    except (ValueError, TypeError) as exc:
        raise CaseError(f"{target}/{slug}: a file isn't base64") from exc
    _put_set(docs, target, slug, files)


def export_sets(docs: Docs, folder: Path, targets: Iterable[str] | None = None) -> list[str]:
    """Every set of the workspace (of *targets* only when given) written into *folder* as
    :func:`import_sets` reads it, each set's folder replaced whole; ``<target>/<set>`` of each."""
    done = []
    for target in sorted(targets) if targets is not None else stored_targets(docs):
        for slug in list_sets(docs, target):
            out = folder / target / slug
            if out.is_dir() and not out.is_symlink():
                shutil.rmtree(out)
            out.mkdir(parents=True, exist_ok=True)
            for path, data in set_files(docs, target, slug).items():
                (out / path).parent.mkdir(parents=True, exist_ok=True)
                (out / path).write_bytes(data)
            done.append(f"{target}/{slug}")
    return done
