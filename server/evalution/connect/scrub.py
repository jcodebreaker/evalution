"""What's taken out of anything shown to the model: secrets, by their values, their names and their
looks. Long things are cut short too, in what comes as data: the shape stays, for reading."""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import quote

from evalution.llm import cut

_TEXT_CHARS = 300
_LIST_ITEMS = 6
_DEPTH = 8
REMOVED = "[removed]"
_WORDS = (
    r"token|secret|pass(?:wd|word|phrase)?\b|pwd|(?:api|access|private)[_-]?key|authorization"
    r"|cookie|credential|bearer"
)
#: A key whose value is nobody's business but the app's.
_SECRET_KEY = re.compile(_WORDS, re.IGNORECASE)
#: A value that looks like a credential whatever it's called.
_SECRET_VALUE = re.compile(
    r"\b(?:sk|pk|rk|gh[pousr]|xox[a-z])[-_][A-Za-z0-9_-]{8,}"
    r"|\bgithub_pat_\w{8,}|\bglpat-[\w-]{8,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|\bAIza[\w-]{20,}"
    r"|\b(?:hf|npm)_[A-Za-z0-9]{8,}|\b[Bb]earer\s+\S+|\beyJ[\w-]{10,}\.\S+"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
#: A login written into an address.
_URL_LOGIN = re.compile(r"//[^\s@'\"]*@")
#: A header that's all credential, to the end of its line, whatever scheme it names.
_SECRET_LINE = re.compile(r"((?:authorization|cookie)\\?[\"']?\s*[:=]\s*)[^\r\n]*", re.IGNORECASE)
#: ``name=value``, ``name: value``, ``"name": "value"`` and the same inside an escaped string.
_SECRET_PAIR = re.compile(
    rf"([\w.-]*(?:{_WORDS})[\w.-]*\\?[\"']?\s*[=:]\s*)"
    r"(\\?\"[^\"]*\"|\\?'[^']*'|[^\s&,;}\]]+)",
    re.IGNORECASE,
)
#: A query parameter that holds a key under a name too short to go by anywhere else.
_SECRET_QUERY = re.compile(r"([?&](?:key|sig|signature|auth|jwt)=)[^&#\s]*", re.IGNORECASE)


def _parsed(text: str) -> Any:
    """The JSON object or list *text* is, else ``None``."""
    if text.lstrip()[:1] not in ("{", "["):
        return None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        return None


def scrub_text(text: str, secrets: set[str]) -> str:
    """*text* with every secret it shows taken out, the ones in *secrets* first."""
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, REMOVED).replace(quote(value, safe=""), REMOVED)
    if (inner := _parsed(text)) is not None:
        return json.dumps(scrub(inner, secrets), ensure_ascii=False)
    text = _SECRET_VALUE.sub(REMOVED, text)
    text = _SECRET_LINE.sub(rf"\1{REMOVED}", text)
    text = _SECRET_PAIR.sub(rf"\1{REMOVED}", text)
    text = _SECRET_QUERY.sub(rf"\1{REMOVED}", text)
    return _URL_LOGIN.sub(f"//{REMOVED}@", text)


def scrub(value: Any, secrets: set[str], depth: int = 0) -> Any:
    """*value* with secrets taken out and long things cut short."""
    if depth > _DEPTH:
        return "..."
    if isinstance(value, str):
        return cut(scrub_text(value, secrets), _TEXT_CHARS)
    if isinstance(value, dict):
        return {
            scrub_text(str(k), secrets): (
                REMOVED if _SECRET_KEY.search(str(k)) else scrub(v, secrets, depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        # Both ends: a reply is often the last of a list.
        kept = value
        if len(value) > _LIST_ITEMS:
            kept = [*value[: _LIST_ITEMS - 2], "...", *value[-2:]]
        return [scrub(v, secrets, depth + 1) for v in kept]
    return value
