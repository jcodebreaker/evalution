"""A fourth adapter, in one module and with no edit anywhere else: what the seam promises."""

from pathlib import Path
from typing import Any

from evalution.adapters.base import (
    AdapterInfo,
    Capabilities,
    Detection,
    Folder,
    Isolation,
    OwnTraffic,
    Session,
    Target,
    Turn,
    part,
)
from evalution.config import TargetConfig


class EchoTarget(Target):
    capabilities = Capabilities(Isolation.SESSION, "A new session id per try.", parallel=True)

    def __init__(self, target: TargetConfig) -> None:
        self.name = target.name

    async def open(self, run: str) -> Session:
        return Session(f"echo-{run}", "session.id", f"echo-{run}")

    async def send(
        self,
        session: Session,
        message: str,
        *,
        files: list[Path] = (),
        stop_after: float | None = None,
        deadline: float,
    ) -> Turn:
        return Turn(message, 0.0, 0.0)

    async def close(self, session: Session) -> None:
        return None


def create(target: TargetConfig) -> EchoTarget:
    return EchoTarget(target)


async def _check(client: Any, options: dict[str, Any], lookup: Any) -> dict[str, Any]:
    return part(True, "It echoes")


def _detect(folder: Folder) -> Detection | None:
    if not (folder.path / "echo.toml").is_file():
        return None
    return Detection("http://127.0.0.1:7777", {}, [("It's an echo app", "done")])


INFO = AdapterInfo(
    name="echo",
    label="Echo adapter",
    options=("url",),
    required=("url",),
    secrets=("api_key",),
    check=_check,
    detect=_detect,
    own_traffic=OwnTraffic(attributes=("echo.eval.run",)),
    rank=3,
)
