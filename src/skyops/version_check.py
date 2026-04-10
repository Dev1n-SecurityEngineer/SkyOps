"""Version checking for skyops."""

import contextlib
import json
import subprocess
import time
from pathlib import Path

from skyops import __version__
from skyops.config import Config

_REPO = "https://github.com/Dev1n-SecurityEngineer/SkyOps.git"
_CHECK_INTERVAL = 86400  # 24 hours


def _check_file() -> Path:
    return Config.get_config_dir() / ".last_version_check"


def _should_check() -> bool:
    """Return True if 24 hours have elapsed since the last check."""
    path = _check_file()
    if not path.exists():
        return True
    try:
        data = json.loads(path.read_text())
        return (time.time() - data.get("timestamp", 0)) > _CHECK_INTERVAL
    except (json.JSONDecodeError, OSError):
        return True


def _record_check() -> None:
    path = _check_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        path.write_text(json.dumps({"timestamp": time.time(), "version": __version__}))


def _latest_remote_commit() -> str | None:
    """Fetch the HEAD commit hash from the remote repo (7 chars). Returns None on failure."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", _REPO, "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        commit = result.stdout.split("\t")[0].strip()
        return commit[:7] if commit else None
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError):
        return None


def _local_commit() -> str | None:
    """Extract the 7-char commit hash embedded in __version__, e.g. '0.1.0+git.a1b2c3d'."""
    if "+git." in __version__:
        return __version__.split("+git.", 1)[1][:7]
    if "+" in __version__:
        suffix = __version__.split("+", 1)[1]
        part = suffix.split(".")[-1] if "." in suffix else suffix
        return part[:7] if part else None
    return None


def check_for_updates() -> None:
    """Print a one-line update notice if a newer commit exists on main.

    Skips silently when:
    - Running from a git checkout (version == "dev")
    - The daily check interval has not elapsed
    - The remote is unreachable
    """
    if __version__ == "dev" or "dev" in __version__.lower():
        return

    if not _should_check():
        return

    _record_check()

    latest = _latest_remote_commit()
    if not latest:
        return

    local = _local_commit()
    if not local or local.lower() == latest.lower():
        return

    from rich.console import Console

    Console().print(
        f"\n[yellow]Update available:[/yellow] [cyan]{latest}[/cyan] "
        f"[dim](current: {local})[/dim]\n"
        "[yellow]Run[/yellow] [cyan]uv tool upgrade skyops[/cyan] [yellow]to update.[/yellow]\n"
    )
