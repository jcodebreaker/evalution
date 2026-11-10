"""Acceptance tests build, start and play whole apps, so each gets 15 minutes before it counts as
stuck, not the two every other test gets (``timeout`` in server/pyproject.toml)."""

import pytest

#: Seconds an acceptance test may take before the run ends on it, with every thread's stack.
ACCEPTANCE_SECONDS = 900


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "acceptance" in item.path.parts:
            item.add_marker(pytest.mark.timeout(ACCEPTANCE_SECONDS))
