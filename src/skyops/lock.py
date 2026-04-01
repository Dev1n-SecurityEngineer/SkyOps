"""File-based lock to prevent concurrent skyops operations on the same instance."""

import contextlib
import fcntl
import functools
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _lock_path(instance_name: str) -> Path:
    lock_dir = Path.home() / ".config" / "skyops" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_name = instance_name.replace("/", "_").replace(" ", "_")
    return lock_dir / f"{safe_name}.lock"


def requires_lock(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator: acquire an exclusive per-instance file lock before running.

    The decorated function must accept an ``instance_name`` keyword argument
    (or have it as the first positional argument after ``self``/``ctx``).
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        instance_name = kwargs.get("instance_name") or (args[0] if args else "default")
        lock_file = _lock_path(str(instance_name))
        with open(lock_file, "w") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError(
                    f"Another skyops operation is already running on '{instance_name}'. "
                    "If this is wrong, delete: " + str(lock_file)
                )
            try:
                return func(*args, **kwargs)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
                with contextlib.suppress(OSError):
                    lock_file.unlink()

    return wrapper
