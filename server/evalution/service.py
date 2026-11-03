"""``evalution service``: the page kept running from login on, the machine's own way (launchd on a
Mac, systemd on Linux). Only ever for this user, and only when asked: nothing else here installs
anything.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "dev.evalution.serve"


def _command(config: Path, port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "evalution.cli",
        "--config",
        str(config),
        "serve",
        "--port",
        str(port),
    ]


def launch_agent(config: Path, port: int) -> str:
    """The launchd job."""
    parts = "".join(f"<string>{escape(p)}</string>" for p in _command(config, port))
    logs = escape(str(config.parent / "data" / "service.log"))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>{LABEL}</string>
<key>ProgramArguments</key><array>{parts}</array>
<key>RunAtLoad</key><true/>
<key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
<key>StandardOutPath</key><string>{logs}</string>
<key>StandardErrorPath</key><string>{logs}</string>
</dict></plist>
"""


def systemd_unit(config: Path, port: int) -> str:
    """The systemd user unit."""
    return f"""[Unit]
Description=evalution

[Service]
ExecStart={" ".join(_command(config, port))}
Restart=on-failure

[Install]
WantedBy=default.target
"""


def _file(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    return home / ".config" / "systemd" / "user" / "evalution.service"


def _start(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "load", "-w", str(path)], check=False)  # noqa: S603, S607
    elif shutil.which("systemctl"):
        subprocess.run(  # noqa: S603
            ["systemctl", "--user", "enable", "--now", path.name],  # noqa: S607
            check=False,
        )


def _stop(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["launchctl", "unload", "-w", str(path)], check=False)  # noqa: S603, S607
    elif shutil.which("systemctl"):
        subprocess.run(  # noqa: S603
            ["systemctl", "--user", "disable", "--now", path.name],  # noqa: S607
            check=False,
        )


def act(action: str, config: Path, port: int, home: Path | None = None) -> str:
    """Do *action* (``install``, ``uninstall``, ``status``) and say what became of it."""
    path = _file(home or Path.home())
    if action == "status":
        return f"Installed: {path}" if path.exists() else "Not installed"
    if action == "uninstall":
        if not path.exists():
            return "Not installed"
        _stop(path)
        path.unlink()
        return f"Removed {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    made = launch_agent if sys.platform == "darwin" else systemd_unit
    path.write_text(made(config, port))
    _start(path)
    return f"Installed {path}: http://127.0.0.1:{port} from login on"
