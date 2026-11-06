"""What ``evalution doctor`` looks at: everything that has to be right before a first run, each with
what to do about it. Spends nothing: it lists models, and asks an app only what its adapter's own
connection check asks. ``evalution check`` is the one that sends a real message.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evalution.adapters import adapter_info, infos
from evalution.config import Config
from evalution.connect.connect import check
from evalution.core import net
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
    found = [_web(config), _proxy(), await _model(config)]
    if not targets.names():
        found.append(Finding(False, "No app connected", "Connect one on the page"))
    for name in targets.names():
        found += await _target(targets, name)
    return found
