"""Tests for skyops.lock module."""

import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from skyops.lock import _lock_path, requires_lock


class TestLockPath:
    def test_returns_path_in_skyops_dir(self, tmp_path: Path):
        with patch.object(Path, "home", return_value=tmp_path):
            lp = _lock_path("my-instance")
        assert "skyops" in str(lp)
        assert "my-instance.lock" in str(lp)

    def test_sanitizes_slashes(self, tmp_path: Path):
        with patch.object(Path, "home", return_value=tmp_path):
            lp = _lock_path("a/b/c")
        assert "/" not in lp.name


class TestRequiresLock:
    def test_executes_function(self, tmp_path: Path):
        with patch.object(Path, "home", return_value=tmp_path):

            @requires_lock
            def my_func(instance_name: str) -> str:
                return f"ok:{instance_name}"

            result = my_func(instance_name="test-inst")
        assert result == "ok:test-inst"

    def test_passes_args_and_kwargs(self, tmp_path: Path):
        with patch.object(Path, "home", return_value=tmp_path):

            @requires_lock
            def my_func(instance_name: str, value: int) -> int:
                return value * 2

            result = my_func(instance_name="test", value=21)
        assert result == 42

    def test_cleans_up_lock_file(self, tmp_path: Path):
        with patch.object(Path, "home", return_value=tmp_path):

            @requires_lock
            def my_func(instance_name: str) -> None:
                pass

            my_func(instance_name="cleanup-test")
            lock_file = _lock_path("cleanup-test")
        assert not lock_file.exists()

    def test_reraises_exception(self, tmp_path: Path):
        with patch.object(Path, "home", return_value=tmp_path):

            @requires_lock
            def bad_func(instance_name: str) -> None:
                raise ValueError("oops")

            with pytest.raises(ValueError, match="oops"):
                bad_func(instance_name="err-test")

    def test_concurrent_lock_raises(self, tmp_path: Path):
        """A second concurrent call on the same instance should raise RuntimeError."""
        holding = threading.Event()
        release = threading.Event()
        results: list[str] = []

        with patch.object(Path, "home", return_value=tmp_path):

            @requires_lock
            def slow_func(instance_name: str) -> None:
                holding.set()  # signal: lock is held
                release.wait(timeout=3)  # wait until test tells us to release

            def holder():
                try:
                    slow_func(instance_name="concurrent")
                    results.append("ok")
                except RuntimeError:
                    results.append("locked")

            def contender():
                holding.wait(timeout=3)  # wait until holder has the lock
                try:
                    slow_func(instance_name="concurrent")
                    results.append("ok")
                except RuntimeError:
                    results.append("locked")

            t1 = threading.Thread(target=holder)
            t2 = threading.Thread(target=contender)
            t1.start()
            t2.start()
            t2.join(timeout=5)  # contender should fail quickly
            release.set()  # let the holder finish
            t1.join(timeout=5)

        assert "locked" in results
        assert "ok" in results
