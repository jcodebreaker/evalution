"""What every test shares."""

import pytest

from evalution.api import deps


@pytest.fixture(autouse=True)
def _test_client_is_local(monkeypatch):
    """Starlette's test client calls itself these; the server knows no such names."""
    monkeypatch.setattr(deps, "LOCAL_NAMES", {*deps.LOCAL_NAMES, "testclient", "testserver"})
