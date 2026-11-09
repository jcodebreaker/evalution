"""The test writer's part in a build recipe (ADR 0011, phase 3): reading it with what the rule
finds in the app's files, and adjusting it as data.

The rule's proposal (:func:`evalution.isolation.recipe.propose`) stays the default; the model
explains it and, when the admin asks, adjusts it: which kind, which variables name the images,
which services and profiles are built, the setup commands of a process app. Every adjustment is
checked against the app's files (:func:`evalution.isolation.recipe.check`): a variable must name
every built image, a service must be one the compose files build, setup runs only an installer.
An adjustment is saved as a new ``proposed`` version (undoable), and nothing is built: proving
it is the admin's, from the Test environment page, with its cost said first.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from evalution.isolation.build import copy_spec
from evalution.isolation.plan import hashes
from evalution.isolation.recipe import (
    KINDS,
    BuildRecipe,
    RecipeStore,
    SetupStep,
    check,
    profiles_of,
    propose,
    stale,
    words,
)

if TYPE_CHECKING:
    from evalution.targets import Targets


def _shown(recipe: BuildRecipe) -> dict[str, Any]:
    return {
        "kind": recipe.kind,
        "in_words": words(recipe),
        "status": recipe.status,
        "by": recipe.by,
        "files": recipe.files,
        "services": recipe.services,
        "profiles": recipe.profiles,
        "name_var": recipe.name_var,
        "tag_var": recipe.tag_var,
        "images_as_the_app_names_them": recipe.images,
        "setup": [{"run": s.run, "cwd": s.cwd} for s in recipe.setup],
        "evidence": [f"{c.at}: {c.says}" for c in recipe.evidence],
        "report": recipe.report,
    }


def _rule(targets: Targets, target: str) -> BuildRecipe:
    """What the rule proposes from the app's files now.

    :raises LookupError: No code folder, or nothing says how a copy of the app starts.
    """
    code = targets.code(target)
    if code is None:
        raise LookupError("no code folder: tell the admin to say where the app's code is")
    return propose(code.root.resolve(), target, copy_spec(targets.config, target))


async def read(targets: Targets, session: dict[str, Any]) -> Any:
    """The ``read_build_recipe`` result: the saved recipe, what the rule finds now, and whether
    they differ."""
    target = str(session["target"])
    store = RecipeStore(targets.config.data_dir)
    try:
        saved = await asyncio.to_thread(store.load, target)
        rule = await asyncio.to_thread(_rule, targets, target)
    except (LookupError, ValueError) as exc:
        return f"Error: {str(exc).strip(chr(39))}. Tell the admin."
    code = targets.code(target)
    changed = stale(saved, code.root.resolve()) if saved and code else []
    return {
        "saved": _shown(saved) if saved else None,
        "changed_since": changed,
        "rule_proposes": _shown(rule),
        "same_as_saved": bool(saved and saved.same_as(rule)),
        "last_build": saved.last_build if saved else None,
        "next": (
            "Explain in plain words how a version is built and that the admin's own images are "
            "never touched. To adjust it, call propose_build_recipe; proving it builds the app "
            "once (minutes and GBs), so only the admin starts that, from the Test environment "
            "page."
        ),
    }


def _list(value: Any, what: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ValueError(f"{what}: a list of names")
    return list(value)


def _setup(value: Any) -> list[SetupStep] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("setup: a list of {run: [...], cwd}")
    steps = []
    for item in value:
        run = item.get("run") if isinstance(item, dict) else None
        if not isinstance(run, list) or not run or not all(isinstance(a, str) for a in run):
            raise ValueError("setup.run: the command as a list of words")
        steps.append(SetupStep(list(run), str(item.get("cwd") or ".")))
    return steps


async def propose_recipe(targets: Targets, session: dict[str, Any], args: dict[str, Any]) -> Any:
    """The ``propose_build_recipe`` result: the adjusted recipe saved as a new version, or why
    not."""
    target = str(session["target"])
    kind = args.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind: one of {', '.join(KINDS)}")
    try:
        rule = await asyncio.to_thread(_rule, targets, target)
    except LookupError as exc:
        return f"Refused: {str(exc).strip(chr(39))}."
    if (kind == "process") != (rule.kind == "process"):
        return (
            "Refused: the app starts "
            + ("as a process" if rule.kind == "process" else "with docker compose")
            + f", so its recipe can't be {kind}."
        )
    services = _list(args.get("services"), "services")
    recipe = replace(
        rule,
        kind=kind,
        services=services if services is not None else rule.services,
        profiles=_list(args.get("profiles"), "profiles") or [],
        name_var=args.get("name_var") if kind == "compose_env" else None,
        tag_var=args.get("tag_var") if kind == "compose_env" else None,
        setup=_setup(args.get("setup")) or rule.setup,
        status="proposed",
        by="agent",
        why=str(args.get("why") or "").strip()[:500],
    )
    if kind == "compose_env" and not (recipe.name_var and recipe.tag_var):
        recipe.name_var, recipe.tag_var = rule.name_var, rule.tag_var
    recipe.images = {s: i for s, i in rule.images.items() if s in recipe.services}
    code = targets.code(target)
    assert code is not None  # _rule found one
    folder = code.root.resolve()
    if args.get("profiles") is None and kind != "process":
        recipe.profiles = profiles_of(folder, recipe.files, recipe.services)
    wrong = await asyncio.to_thread(check, recipe, folder)
    if wrong:
        return "Refused, nothing saved. Fix these and propose again:\n- " + "\n- ".join(wrong)
    store = RecipeStore(targets.config.data_dir)
    saved = await asyncio.to_thread(store.load, target)
    if saved is not None and saved.same_as(recipe):
        return "Refused: the saved recipe already builds this way."
    recipe.report = words(recipe) + (
        f"\nAdjusted by the test writer: {recipe.why}" if recipe.why else ""
    )
    recipe.evidence_hashes = await asyncio.to_thread(
        hashes, folder, [c.at for c in recipe.evidence]
    )
    number = await asyncio.to_thread(store.save, recipe)
    return {
        "saved_as_version": number,
        "status": "proposed",
        "in_words": words(recipe),
        "next": (
            "Tell the admin in plain words what changed and why. It is proposed, not proven: "
            "they prove it from the Test environment page (it builds the app once), and can "
            "undo it there."
        ),
    }
