"""What UI testing needs on this computer (ADR 0010), as ``evalution doctor`` says it: found
without launching anything, and failing only when a target plays through its page."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evalution import doctor
from evalution.runs import isolated
from evalution.runs.page import target as page_target
from evalution.runs.page.target import PAGE_SETUP, page_lacks, page_parts


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    monkeypatch.setattr(page_target, "system_chrome", lambda: None)
    return tmp_path


def _config(*, page: bool) -> SimpleNamespace:
    options = {"page": {"url": "{copy_page}/"}} if page else {}
    return SimpleNamespace(targets={"bot": SimpleNamespace(options=options)})


def test_the_parts_are_read_from_playwrights_own_folder(cache) -> None:
    assert page_parts()["browser"] is False and page_parts()["ffmpeg"] is False
    (cache / "chromium-1200").mkdir()
    (cache / "ffmpeg-1013").mkdir()
    found = page_parts()
    assert found["browser"] and found["ffmpeg"]


def test_the_system_chrome_counts_as_a_browser(cache, monkeypatch) -> None:
    monkeypatch.setattr(page_target, "system_chrome", lambda: "/Applications/Chrome")
    assert page_parts()["browser"]


def test_without_a_page_target_a_missing_part_fails_nothing(cache, monkeypatch) -> None:
    monkeypatch.setattr(
        doctor, "page_parts", lambda: {"playwright": False, "browser": False, "ffmpeg": False}
    )
    found = doctor._page(_config(page=False))
    assert found.ok and "optional" in found.what and PAGE_SETUP in found.what


def test_a_page_target_without_ffmpeg_is_told_the_one_command(cache, monkeypatch) -> None:
    monkeypatch.setattr(
        doctor, "page_parts", lambda: {"playwright": True, "browser": True, "ffmpeg": False}
    )
    found = doctor._page(_config(page=True))
    assert not found.ok
    assert "ffmpeg for video" in found.what and found.fix == f"Run: {PAGE_SETUP}"


def test_everything_there_is_ready(monkeypatch) -> None:
    monkeypatch.setattr(
        doctor, "page_parts", lambda: {"playwright": True, "browser": True, "ffmpeg": True}
    )
    assert doctor._page(_config(page=True)).ok


def test_each_missing_piece_is_one_sentence_with_the_command_that_installs_it(
    cache, monkeypatch
) -> None:
    """What the page shows for each: what's missing, and the exact command; ffmpeg only when
    video is asked about, since a try plays on without it."""
    browser, ffmpeg = page_lacks(video=True)
    assert (browser.part, ffmpeg.part) == ("browser", "ffmpeg")
    for piece in (browser, ffmpeg):
        assert piece.said.endswith(".") and ". " not in piece.said, "one sentence"
    assert browser.command is not None and browser.command.endswith("playwright install chromium")
    assert ffmpeg.command is not None and ffmpeg.command.endswith("playwright install ffmpeg")
    assert [m.part for m in page_lacks()] == ["browser"], "a run needs no ffmpeg"
    monkeypatch.setattr(page_target, "page_missing", lambda: "no Playwright")
    (playwright,) = page_lacks(video=True)
    assert playwright.part == "playwright" and "--extra page" in (playwright.command or "")
    (cache / "chromium-1200").mkdir()
    (cache / "ffmpeg-1013").mkdir()
    monkeypatch.undo()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(cache))
    monkeypatch.setattr(page_target, "system_chrome", lambda: None)
    assert page_lacks(video=True) == []


def test_a_page_run_without_a_copy_or_a_browser_is_refused_with_its_fix(cache) -> None:
    lacking = isolated.page_refusal("chat", isolated.Footing("none"))
    assert lacking is not None and lacking.part == "copy" and lacking.command is None
    assert "evalution's own copy" in lacking.said
    proven = isolated.Footing("proven", plan=SimpleNamespace())  # type: ignore[arg-type]
    found = isolated.page_refusal("chat", proven)
    assert found is not None and found.part == "browser"
