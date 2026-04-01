"""Tests for skyops.ssh_config module."""

from pathlib import Path

import pytest

from skyops.ssh_config import (
    add_ssh_host,
    get_ssh_host_ip,
    host_exists,
    instance_host_name,
    remove_ssh_host,
)


@pytest.fixture
def ssh_config_file(tmp_path: Path) -> Path:
    return tmp_path / ".ssh" / "config"


# ------------------------------------------------------------------
# instance_host_name
# ------------------------------------------------------------------


class TestInstanceHostName:
    def test_prefixes_with_skyops(self):
        assert instance_host_name("my-instance") == "skyops.my-instance"

    def test_simple_name(self):
        assert instance_host_name("dev") == "skyops.dev"


# ------------------------------------------------------------------
# add_ssh_host
# ------------------------------------------------------------------


class TestAddSSHHost:
    def test_creates_config_and_entry(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.test", "1.2.3.4", "alice")
        content = ssh_config_file.read_text()
        assert "Host skyops.test" in content
        assert "HostName 1.2.3.4" in content
        assert "User alice" in content
        assert "ForwardAgent yes" in content

    def test_includes_identity_file(self, ssh_config_file: Path):
        add_ssh_host(
            str(ssh_config_file),
            "skyops.test",
            "1.2.3.4",
            "alice",
            identity_file="~/.ssh/id_ed25519",
        )
        assert "IdentityFile ~/.ssh/id_ed25519" in ssh_config_file.read_text()

    def test_updates_existing_host(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.test", "1.2.3.4", "alice")
        add_ssh_host(str(ssh_config_file), "skyops.test", "5.6.7.8", "alice")
        content = ssh_config_file.read_text()
        assert "1.2.3.4" not in content
        assert "5.6.7.8" in content
        assert content.count("Host skyops.test") == 1

    def test_preserves_other_hosts(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.a", "1.1.1.1", "alice")
        add_ssh_host(str(ssh_config_file), "skyops.b", "2.2.2.2", "bob")
        content = ssh_config_file.read_text()
        assert "skyops.a" in content
        assert "skyops.b" in content
        assert "1.1.1.1" in content
        assert "2.2.2.2" in content

    def test_permissions_set_to_600(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.test", "1.2.3.4", "alice")
        assert oct(ssh_config_file.stat().st_mode & 0o777) == "0o600"

    def test_creates_parent_directory(self, tmp_path: Path):
        config_path = tmp_path / "deep" / "nested" / "config"
        add_ssh_host(str(config_path), "skyops.test", "1.2.3.4", "alice")
        assert config_path.exists()


# ------------------------------------------------------------------
# get_ssh_host_ip
# ------------------------------------------------------------------


class TestGetSSHHostIP:
    def test_returns_ip_for_known_host(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.test", "10.0.0.1", "alice")
        assert get_ssh_host_ip(str(ssh_config_file), "skyops.test") == "10.0.0.1"

    def test_returns_none_for_unknown_host(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.test", "10.0.0.1", "alice")
        assert get_ssh_host_ip(str(ssh_config_file), "skyops.other") is None

    def test_returns_none_when_no_config(self, ssh_config_file: Path):
        assert get_ssh_host_ip(str(ssh_config_file), "skyops.test") is None


# ------------------------------------------------------------------
# host_exists
# ------------------------------------------------------------------


class TestHostExists:
    def test_true_for_existing(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.test", "1.2.3.4", "alice")
        assert host_exists(str(ssh_config_file), "skyops.test") is True

    def test_false_for_missing(self, ssh_config_file: Path):
        assert host_exists(str(ssh_config_file), "skyops.nope") is False

    def test_false_when_no_config(self, ssh_config_file: Path):
        assert host_exists(str(ssh_config_file), "skyops.test") is False


# ------------------------------------------------------------------
# remove_ssh_host
# ------------------------------------------------------------------


class TestRemoveSSHHost:
    def test_removes_existing_host(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.test", "1.2.3.4", "alice")
        assert remove_ssh_host(str(ssh_config_file), "skyops.test") is True
        assert not host_exists(str(ssh_config_file), "skyops.test")

    def test_preserves_other_hosts(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.a", "1.1.1.1", "alice")
        add_ssh_host(str(ssh_config_file), "skyops.b", "2.2.2.2", "alice")
        remove_ssh_host(str(ssh_config_file), "skyops.a")
        assert host_exists(str(ssh_config_file), "skyops.b")
        assert not host_exists(str(ssh_config_file), "skyops.a")

    def test_returns_false_when_not_found(self, ssh_config_file: Path):
        ssh_config_file.parent.mkdir(parents=True, exist_ok=True)
        ssh_config_file.write_text("")
        assert remove_ssh_host(str(ssh_config_file), "skyops.nope") is False

    def test_returns_false_when_no_file(self, ssh_config_file: Path):
        assert remove_ssh_host(str(ssh_config_file), "skyops.test") is False

    def test_creates_backup(self, ssh_config_file: Path):
        add_ssh_host(str(ssh_config_file), "skyops.test", "1.2.3.4", "alice")
        remove_ssh_host(str(ssh_config_file), "skyops.test")
        backup = ssh_config_file.parent / f"{ssh_config_file.name}.bak"
        assert backup.exists()
