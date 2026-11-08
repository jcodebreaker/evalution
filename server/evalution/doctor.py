"""What ``evalution doctor`` looks at: everything that has to be right before a first run, each with
what to do about it. Spends nothing: it lists models, and asks an app only what its adapter's own
connection check asks. ``evalution check`` is the one that sends a real message.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evalution.adapters import adapter_info, broken, infos
from evalution.config import Config
from evalution.connect.connect import check
from evalution.core import net
from evalution.runs.page.target import PAGE_SETUP, page_parts
from evalution.settings import SettingsError, list_models
from evalution.targets import Targets

#: Any other part is the traces.
_TRACES_FIX = "Normal before a first run. After one, check the app sends traces with this value"
_FIXES = {
    "adapter": "Is it running? Then try: evalution check --target {name}",
    "code": "Check the folder set for {name}",
}


@dataclass(frozen=True)
class Finding:
    ok: bool
    what: str
    fix: str = ""


def _web(config: Config) -> Finding:
    if (Path(config.web_dir) / "index.html").exists():
        return Finding(True, "The page is built")
    return Finding(
        False, "The page isn't built", "Run: npm ci --prefix web && npm run build --prefix web"
    )


def _page(config: Config) -> Finding:
    """Whether UI testing (ADR 0010) has what it needs here. Only a target that plays through its
    page needs it, so without one, what's missing is said and nothing fails."""
    parts = page_parts()
    if all(parts.values()):
        return Finding(True, "UI testing is ready: Playwright, a browser and ffmpeg for video")
    missing = [name for name, there in parts.items() if not there]
    words = {"playwright": "Playwright", "browser": "a browser", "ffmpeg": "ffmpeg for video"}
    said = ", ".join(words[name] for name in missing)
    needed = any(isinstance(t.options.get("page"), dict) for t in config.targets.values())
    if not needed and not parts["playwright"]:
        return Finding(True, f"UI testing isn't installed (optional). To add it: {PAGE_SETUP}")
    return Finding(not needed, f"UI testing lacks {said}", f"Run: {PAGE_SETUP}")


def _proxy() -> Finding:
    found = net.proxies_set()
    if not found:
        return Finding(True, "No proxy set")
    names = ", ".join(found)
    return Finding(True, f"Proxy set ({names}): used for the outside world, never localhost")


async def _model(config: Config) -> Finding:
    if config.model is None:
        return Finding(
            False,
            "No model set: tests can't be graded or written",
            "Choose one under Settings on the page",
        )
    try:
        found = await list_models(config.model)
    except SettingsError as exc:
        return Finding(False, f"The model's provider: {exc}", f"Check {config.model.base_url}")
    return Finding(True, f"The model's provider answers, with {len(found)} models")


async def _target(targets: Targets, name: str) -> list[Finding]:
    conf = targets.conf(name)
    if why := broken().get(conf.adapter):
        where = targets.config.adapters_dir or targets.config.data_dir / "adapters"
        file = where / f"{conf.adapter}.py"
        fix = f"Fix {file}: an evalution update may have changed what it imports"
        return [Finding(False, f"{name}: adapter {conf.adapter!r} doesn't load ({why})", fix)]
    if adapter_info(conf.adapter) is None:
        where = targets.config.adapters_dir or targets.config.data_dir / "adapters"
        have = ", ".join(infos())
        fix = f"Have: {have}. Your own goes in {where}"
        return [Finding(False, f"{name}: no adapter {conf.adapter!r}", fix)]
    said = await check(conf, {}, targets.store)
    found = []
    for part_name, part in said.items():
        if isinstance(part, dict) and "ok" in part:
            ok = bool(part["ok"])
            fix = (
                ""
                if ok
                else part.get("fix") or _FIXES.get(part_name, _TRACES_FIX).format(name=name)
            )
            found.append(Finding(ok, f"{name}, {part_name}: {part.get('detail') or ''}", fix))
    return found


async def examine(config: Config, targets: Targets) -> list[Finding]:
    found = [_web(config), _page(config), _proxy(), await _model(config)]
    if not targets.names():
        found.append(Finding(False, "No app connected", "Connect one on the page"))
    for name in targets.names():
        found += await _target(targets, name)
    return found
