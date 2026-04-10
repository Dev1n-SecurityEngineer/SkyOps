"""Tests for skyops.version_check module."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import skyops.version_check as vc

# ---------------------------------------------------------------------------
# _should_check / _record_check
# ---------------------------------------------------------------------------


class TestShouldCheck:
    def test_returns_true_when_no_file(self, tmp_path: Path):
        with patch.object(vc, "_check_file", return_value=tmp_path / ".last_check"):
            assert vc._should_check() is True

    def test_returns_false_within_24h(self, tmp_path: Path):
        path = tmp_path / ".last_check"
        path.write_text(json.dumps({"timestamp": time.time()}))
        with patch.object(vc, "_check_file", return_value=path):
            assert vc._should_check() is False

    def test_returns_true_after_24h(self, tmp_path: Path):
        path = tmp_path / ".last_check"
        past = time.time() - 86401
        path.write_text(json.dumps({"timestamp": past}))
        with patch.object(vc, "_check_file", return_value=path):
            assert vc._should_check() is True

    def test_returns_true_on_corrupt_file(self, tmp_path: Path):
        path = tmp_path / ".last_check"
        path.write_text("not json{{{")
        with patch.object(vc, "_check_file", return_value=path):
            assert vc._should_check() is True


class TestRecordCheck:
    def test_writes_timestamp(self, tmp_path: Path):
        path = tmp_path / ".last_check"
        with patch.object(vc, "_check_file", return_value=path):
            vc._record_check()
        data = json.loads(path.read_text())
        assert abs(data["timestamp"] - time.time()) < 5

    def test_silently_ignores_write_error(self, tmp_path: Path):
        path = tmp_path / "no_such_dir" / ".last_check"
        # Parent doesn't exist — mkdir call inside _record_check creates it,
        # so simulate an OSError on write instead.
        with (
            patch.object(vc, "_check_file", return_value=path),
            patch("builtins.open", side_effect=OSError("disk full")),
        ):
            vc._record_check()  # must not raise


# ---------------------------------------------------------------------------
# _local_commit
# ---------------------------------------------------------------------------


class TestLocalCommit:
    def test_extracts_from_git_format(self):
        with patch.object(vc, "__version__", "0.1.0+git.a1b2c3d"):
            assert vc._local_commit() == "a1b2c3d"

    def test_extracts_from_plus_format(self):
        with patch.object(vc, "__version__", "0.1.0+a1b2c3d"):
            assert vc._local_commit() == "a1b2c3d"

    def test_returns_none_for_plain_version(self):
        with patch.object(vc, "__version__", "0.1.0"):
            assert vc._local_commit() is None

    def test_truncates_to_7_chars(self):
        with patch.object(vc, "__version__", "0.1.0+git.abcdef1234567"):
            assert len(vc._local_commit()) == 7  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _latest_remote_commit
# ---------------------------------------------------------------------------


class TestLatestRemoteCommit:
    def test_parses_ls_remote_output(self):
        mock_result = MagicMock(returncode=0, stdout="abcdef1234567\tHEAD\n")
        with patch("subprocess.run", return_value=mock_result):
            assert vc._latest_remote_commit() == "abcdef1"

    def test_returns_none_on_nonzero_exit(self):
        mock_result = MagicMock(returncode=1, stdout="")
        with patch("subprocess.run", return_value=mock_result):
            assert vc._latest_remote_commit() is None

    def test_returns_none_on_empty_output(self):
        mock_result = MagicMock(returncode=0, stdout="")
        with patch("subprocess.run", return_value=mock_result):
            assert vc._latest_remote_commit() is None

    def test_returns_none_on_timeout(self):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=5)):
            assert vc._latest_remote_commit() is None

    def test_returns_none_on_subprocess_error(self):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.SubprocessError):
            assert vc._latest_remote_commit() is None


# ---------------------------------------------------------------------------
# check_for_updates (integration of the above)
# ---------------------------------------------------------------------------


class TestCheckForUpdates:
    def test_skips_in_dev_mode(self):
        with (
            patch.object(vc, "__version__", "dev"),
            patch.object(vc, "_should_check") as mock_check,
        ):
            vc.check_for_updates()
            mock_check.assert_not_called()

    def test_skips_when_interval_not_elapsed(self):
        with (
            patch.object(vc, "__version__", "0.1.0+git.abc1234"),
            patch.object(vc, "_should_check", return_value=False),
            patch.object(vc, "_latest_remote_commit") as mock_remote,
        ):
            vc.check_for_updates()
            mock_remote.assert_not_called()

    def test_no_output_when_up_to_date(self, capsys: pytest.CaptureFixture):
        with (
            patch.object(vc, "__version__", "0.1.0+git.abc1234"),
            patch.object(vc, "_should_check", return_value=True),
            patch.object(vc, "_record_check"),
            patch.object(vc, "_latest_remote_commit", return_value="abc1234"),
        ):
            vc.check_for_updates()
        captured = capsys.readouterr()
        assert "Update" not in captured.out

    def test_prints_notice_when_outdated(self, capsys: pytest.CaptureFixture):
        with (
            patch.object(vc, "__version__", "0.1.0+git.aaa0000"),
            patch.object(vc, "_should_check", return_value=True),
            patch.object(vc, "_record_check"),
            patch.object(vc, "_latest_remote_commit", return_value="bbb1111"),
        ):
            vc.check_for_updates()
        captured = capsys.readouterr()
        assert "Update available" in captured.out
        assert "bbb1111" in captured.out

    def test_no_output_when_remote_unreachable(self, capsys: pytest.CaptureFixture):
        with (
            patch.object(vc, "__version__", "0.1.0+git.abc1234"),
            patch.object(vc, "_should_check", return_value=True),
            patch.object(vc, "_record_check"),
            patch.object(vc, "_latest_remote_commit", return_value=None),
        ):
            vc.check_for_updates()
        captured = capsys.readouterr()
        assert "Update" not in captured.out
