"""The stateful test library as it applies to one target (:mod:`evalution.suite.library`)."""

from __future__ import annotations

from typing import Any

from evalution.config import ConfigError
from evalution.project.profile import read_profile
from evalution.suite.library import fit, load_templates
from evalution.targets import Targets


def for_target(targets: Targets, name: str) -> list[dict[str, Any]]:
    """Every template with its fit for target *name*.

    :raises LibraryError: A bad template.
    """
    try:
        declared = targets.adapter(name).capabilities
        capabilities, state_checks = set(declared.names()), declared.state_checks
        clean = declared.starts_clean()
    except (ConfigError, ImportError):
        capabilities, state_checks, clean = set(), None, None
    traced = targets.source(name) is not None
    profile = read_profile(targets, name)
    return [
        {**t.summary(), "fit": fit(t, capabilities, state_checks, traced, profile, clean)}
        for t in load_templates(targets.config.library_dir)
    ]
