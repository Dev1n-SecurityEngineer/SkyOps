"""skyops — manage AWS EC2 instances from the command line."""

import subprocess
from pathlib import Path


def _get_version() -> str:
    # Development: running from a git repository
    try:
        repo_root = Path(__file__).parent.parent.parent
        if (repo_root / ".git").exists():
            return "dev"
    except Exception:
        pass

    # Installed: check for embedded version file written at build time
    try:
        version_file = Path(__file__).parent / "_version.txt"
        if version_file.exists():
            commit = version_file.read_text().strip()
            if commit:
                return f"0.1.0+git.{commit}"
    except Exception:
        pass

    # Fallback: try git at runtime
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
            cwd=Path(__file__).parent,
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
            if commit:
                return f"0.1.0+git.{commit}"
    except Exception:
        pass

    return "0.1.0"


__version__ = _get_version()
