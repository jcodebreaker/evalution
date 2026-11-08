"""What UI testing needs on this computer (ADR 0010), as ``evalution doctor`` says it: found
without launching anything, and failing only when a target plays through its page."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evalution import doctor
from evalution.runs.page import target as page_target
from evalution.runs.page.target import PAGE_SETUP, page_parts


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
