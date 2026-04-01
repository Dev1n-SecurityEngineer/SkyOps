"""Tests for skyops.config module."""

import base64
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
)
from pydantic import ValidationError

from skyops.config import (
    AWSConfig,
    Config,
    DefaultsConfig,
    SkyOpsConfig,
    SSHConfig,
    UserDataConfig,
)

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_ed25519_pub_key() -> str:
    """Generate a real ed25519 public key string."""
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_type = b"ssh-ed25519"
    inner = len(key_type).to_bytes(4, "big") + key_type + len(raw).to_bytes(4, "big") + raw
    b64 = base64.b64encode(inner).decode()
    return f"ssh-ed25519 {b64} test@skyops"


@pytest.fixture
def pub_key_str() -> str:
    return _make_ed25519_pub_key()


@pytest.fixture
def pub_key_file(pub_key_str: str, tmp_path: Path) -> Path:
    key_file = tmp_path / "id_ed25519.pub"
    key_file.write_text(pub_key_str)
    return key_file


@pytest.fixture
def minimal_config(tmp_path: Path, pub_key_file: Path) -> SkyOpsConfig:
    return SkyOpsConfig(
        aws=AWSConfig(region="us-east-1"),
        defaults=DefaultsConfig(
            region="us-east-1",
            instance_type="t3.medium",
            ami="ami-12345678",
            key_pair_name="skyops-testuser",
        ),
        userdata=UserDataConfig(ssh_keys=[str(pub_key_file)]),
        ssh=SSHConfig(
            config_path=str(tmp_path / ".ssh" / "config"),
            identity_file=str(tmp_path / "id_ed25519"),
        ),
    )


# ------------------------------------------------------------------
# AWSConfig
# ------------------------------------------------------------------


class TestAWSConfig:
    def test_region_required(self):
        with pytest.raises(ValidationError):
            AWSConfig(region="")

    def test_profile_optional(self):
        cfg = AWSConfig(region="us-west-2")
        assert cfg.profile is None

    def test_profile_set(self):
        cfg = AWSConfig(region="us-east-1", profile="prod")
        assert cfg.profile == "prod"


# ------------------------------------------------------------------
# UserDataConfig
# ------------------------------------------------------------------


class TestUserDataConfig:
    def test_ssh_keys_required(self):
        with pytest.raises(ValidationError):
            UserDataConfig(ssh_keys=[])

    def test_valid(self, pub_key_file: Path):
        cfg = UserDataConfig(ssh_keys=[str(pub_key_file)])
        assert len(cfg.ssh_keys) == 1


# ------------------------------------------------------------------
# SkyOpsConfig extras=forbid
# ------------------------------------------------------------------


class TestSkyOpsConfig:
    def test_extra_fields_forbidden(self, minimal_config: SkyOpsConfig, tmp_path: Path):
        data = minimal_config.model_dump()
        data["unknown_field"] = "value"
        with pytest.raises(ValidationError):
            SkyOpsConfig(**data)


# ------------------------------------------------------------------
# Config.detect_ssh_keys
# ------------------------------------------------------------------


class TestDetectSSHKeys:
    def test_returns_pub_files(self, tmp_path: Path, pub_key_str: str):
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        (ssh_dir / "id_ed25519.pub").write_text(pub_key_str)
        (ssh_dir / "id_ed25519").write_text("private")

        with patch.object(Path, "home", return_value=tmp_path):
            keys = Config.detect_ssh_keys()

        assert any(k.endswith(".pub") for k in keys)
        assert not any(k == "id_ed25519" for k in keys)

    def test_empty_when_no_ssh_dir(self, tmp_path: Path):
        with patch.object(Path, "home", return_value=tmp_path / "nonexistent"):
            keys = Config.detect_ssh_keys()
        assert keys == []


# ------------------------------------------------------------------
# Config.validate_ssh_public_key
# ------------------------------------------------------------------


class TestValidateSSHPublicKey:
    def test_valid_ed25519(self, pub_key_file: Path):
        Config.validate_ssh_public_key(str(pub_key_file))  # no exception

    def test_rejects_missing_file(self):
        with pytest.raises(FileNotFoundError):
            Config.validate_ssh_public_key("/nonexistent/path/key.pub")

    def test_rejects_non_pub_extension(self, tmp_path: Path, pub_key_str: str):
        key_file = tmp_path / "id_ed25519"
        key_file.write_text(pub_key_str)
        with pytest.raises(ValueError, match="private"):
            Config.validate_ssh_public_key(str(key_file))

    def test_rejects_empty_file(self, tmp_path: Path):
        empty = tmp_path / "empty.pub"
        empty.write_text("")
        with pytest.raises(ValueError, match="empty"):
            Config.validate_ssh_public_key(str(empty))

    def test_rejects_invalid_content(self, tmp_path: Path):
        bad = tmp_path / "bad.pub"
        bad.write_text("not a valid key")
        with pytest.raises(ValueError):
            Config.validate_ssh_public_key(str(bad))


# ------------------------------------------------------------------
# Config.compute_ssh_key_fingerprint
# ------------------------------------------------------------------


class TestComputeSSHKeyFingerprint:
    def test_returns_colon_hex(self, pub_key_str: str):
        fingerprint = Config.compute_ssh_key_fingerprint(pub_key_str)
        parts = fingerprint.split(":")
        assert len(parts) == 16
        assert all(len(p) == 2 for p in parts)

    def test_invalid_key_raises(self):
        with pytest.raises(ValueError):
            Config.compute_ssh_key_fingerprint("not a key")


# ------------------------------------------------------------------
# Config.read_ssh_key_content
# ------------------------------------------------------------------


class TestReadSSHKeyContent:
    def test_reads_content(self, pub_key_file: Path, pub_key_str: str):
        content = Config.read_ssh_key_content(str(pub_key_file))
        assert content == pub_key_str.strip()

    def test_missing_file(self):
        with pytest.raises(FileNotFoundError):
            Config.read_ssh_key_content("/nonexistent/key.pub")


# ------------------------------------------------------------------
# Config.get_system_username
# ------------------------------------------------------------------


class TestGetSystemUsername:
    def test_returns_user_env(self):
        with patch.dict(os.environ, {"USER": "testuser"}):
            assert Config.get_system_username() == "testuser"

    def test_fallback(self):
        env = os.environ.copy()
        env.pop("USER", None)
        with patch.dict(os.environ, env, clear=True):
            result = Config.get_system_username()
        assert result == "user"


# ------------------------------------------------------------------
# Config.save / load round-trip
# ------------------------------------------------------------------


class TestConfigSaveLoad:
    def test_round_trip(self, tmp_path: Path, minimal_config: SkyOpsConfig, pub_key_file: Path):
        config_dir = tmp_path / ".config" / "skyops"
        config_file = config_dir / "config.yaml"

        with (
            patch.object(Config, "CONFIG_DIR", config_dir),
            patch.object(Config, "CONFIG_FILE", config_file),
        ):
            mgr = Config()
            mgr._config = minimal_config
            mgr.save()

            assert config_file.exists()
            assert oct(config_file.stat().st_mode & 0o777) == "0o600"

            mgr2 = Config()
            mgr2.load()
            assert mgr2.config.aws.region == "us-east-1"
            assert mgr2.config.defaults.instance_type == "t3.medium"

    def test_load_missing_file(self, tmp_path: Path):
        config_file = tmp_path / "nonexistent.yaml"
        with patch.object(Config, "CONFIG_FILE", config_file):
            mgr = Config()
            with pytest.raises(FileNotFoundError):
                mgr.load()

    def test_config_property_raises_before_load(self):
        mgr = Config()
        with pytest.raises(ValueError, match="not loaded"):
            _ = mgr.config


# ------------------------------------------------------------------
# Config.create_default_config
# ------------------------------------------------------------------


class TestCreateDefaultConfig:
    def test_creates_valid_config(self, pub_key_file: Path):
        mgr = Config()
        mgr.create_default_config(
            region="us-west-2",
            instance_type="t3.small",
            ami="ami-99999999",
            key_pair_name="skyops-alice",
            ssh_keys=[str(pub_key_file)],
        )
        assert mgr.config.aws.region == "us-west-2"
        assert mgr.config.defaults.instance_type == "t3.small"

    def test_raises_with_empty_ssh_keys(self):
        mgr = Config()
        with pytest.raises(ValueError, match="No SSH keys"):
            mgr.create_default_config(
                region="us-east-1",
                instance_type="t3.medium",
                ami="ami-12345678",
                key_pair_name="skyops-bob",
                ssh_keys=[],
            )
