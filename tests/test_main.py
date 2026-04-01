"""Tests for skyops.main CLI commands using typer.testing.CliRunner."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from skyops.main import app

runner = CliRunner()


# ------------------------------------------------------------------
# Fixtures / helpers
# ------------------------------------------------------------------


def _mock_api_triple(
    instances=None,
    username="alice",
    amis=None,
    key_pairs=None,
    regions=None,
    instance_types=None,
):
    """Return (api_mock, cfg_mock, username) matching _load_api() return shape."""
    api = MagicMock()
    cfg = MagicMock()

    api.get_username.return_value = username
    api.list_instances.return_value = instances or []
    api.list_hibernate_amis.return_value = amis or []
    api.list_key_pairs.return_value = key_pairs or []
    api.list_regions.return_value = regions or ["us-east-1", "us-west-2"]
    api.list_instance_types.return_value = instance_types or []

    cfg.config.aws.region = "us-east-1"
    cfg.config.aws.profile = None
    cfg.config.defaults.instance_type = "t3.medium"
    cfg.config.defaults.ami = "ami-12345678"
    cfg.config.defaults.key_pair_name = "skyops-alice"
    cfg.config.defaults.vpc_id = None
    cfg.config.defaults.subnet_id = None
    cfg.config.defaults.security_group_id = None
    cfg.config.defaults.extra_tags = []
    cfg.config.userdata.ssh_keys = []
    cfg.config.userdata.template_path = None
    cfg.config.ssh.config_path = "/tmp/test_ssh_config"
    cfg.config.ssh.auto_update = True
    cfg.config.ssh.identity_file = "~/.ssh/id_ed25519"

    return api, cfg, username


SAMPLE_INSTANCE = {
    "InstanceId": "i-abc123",
    "InstanceType": "t3.medium",
    "State": {"Name": "running"},
    "PublicIpAddress": "1.2.3.4",
    "PrivateIpAddress": "10.0.0.1",
    "ImageId": "ami-12345678",
    "KeyName": "skyops-alice",
    "VpcId": "vpc-abc",
    "SubnetId": "subnet-abc",
    "LaunchTime": "2024-01-01",
    "Placement": {"AvailabilityZone": "us-east-1a"},
    "Tags": [
        {"Key": "Name", "Value": "my-instance"},
        {"Key": "skyops:owner", "Value": "alice"},
    ],
}


# ------------------------------------------------------------------
# version
# ------------------------------------------------------------------


class TestVersionCommand:
    def test_shows_version(self):
        result = runner.invoke(app, ["version"])
        assert result.exit_code == 0
        assert "skyops" in result.output


# ------------------------------------------------------------------
# list
# ------------------------------------------------------------------


class TestListCommand:
    def test_empty_list(self):
        api, cfg, username = _mock_api_triple()
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "No instances found" in result.output

    def test_shows_instances(self):
        api, cfg, username = _mock_api_triple(instances=[SAMPLE_INSTANCE])
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 0
        assert "i-abc123" in result.output

    def test_api_error(self):
        api, cfg, username = _mock_api_triple()
        from skyops.api import EC2APIError

        api.list_instances.side_effect = EC2APIError("AWS error")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 1

    def test_config_not_found(self):
        with patch(
            "skyops.main._load_api", side_effect=FileNotFoundError("Config not found")
        ):
            result = runner.invoke(app, ["list"])
        assert result.exit_code == 1


# ------------------------------------------------------------------
# info
# ------------------------------------------------------------------


class TestInfoCommand:
    def test_shows_info(self):
        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.return_value = SAMPLE_INSTANCE
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["info", "my-instance"])
        assert result.exit_code == 0
        assert "i-abc123" in result.output

    def test_instance_not_found(self):
        from skyops.api import EC2APIError

        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["info", "missing"])
        assert result.exit_code == 1


# ------------------------------------------------------------------
# ssh-config
# ------------------------------------------------------------------


class TestSSHConfigCommand:
    def test_updates_ssh_config(self, tmp_path: Path):
        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.return_value = SAMPLE_INSTANCE
        cfg.config.ssh.config_path = str(tmp_path / "ssh_config")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["ssh-config", "my-instance"])
        assert result.exit_code == 0
        assert "skyops.my-instance" in result.output

    def test_no_public_ip_fails(self):
        api, cfg, username = _mock_api_triple()
        inst = {**SAMPLE_INSTANCE}
        del inst["PublicIpAddress"]
        api.find_instance_by_name.return_value = inst
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["ssh-config", "my-instance"])
        assert result.exit_code == 1


# ------------------------------------------------------------------
# rename
# ------------------------------------------------------------------


class TestRenameCommand:
    def test_renames_with_yes_flag(self, tmp_path: Path):
        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.return_value = SAMPLE_INSTANCE
        cfg.config.ssh.config_path = str(tmp_path / "ssh_config")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["rename", "my-instance", "new-name", "--yes"])
        assert result.exit_code == 0
        api.rename_instance.assert_called_once_with("i-abc123", "new-name")

    def test_aborts_on_no(self):
        api, cfg, username = _mock_api_triple()
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            runner.invoke(app, ["rename", "my-instance", "new-name"], input="n\n")
        api.rename_instance.assert_not_called()


# ------------------------------------------------------------------
# destroy
# ------------------------------------------------------------------


class TestDestroyCommand:
    def test_destroys_with_yes_flag(self, tmp_path: Path):
        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.return_value = SAMPLE_INSTANCE
        cfg.config.ssh.config_path = str(tmp_path / "ssh_config")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["destroy", "my-instance", "--yes"])
        assert result.exit_code == 0
        api.terminate_instance.assert_called_once_with("i-abc123")

    def test_aborts_on_no(self):
        api, cfg, username = _mock_api_triple()
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            runner.invoke(app, ["destroy", "my-instance"], input="n\n")
        api.terminate_instance.assert_not_called()


# ------------------------------------------------------------------
# off
# ------------------------------------------------------------------


class TestOffCommand:
    def test_stops_with_yes_flag(self):
        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.return_value = SAMPLE_INSTANCE
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["off", "my-instance", "--yes"])
        assert result.exit_code == 0
        api.stop_instance.assert_called_once_with("i-abc123")

    def test_aborts_on_no(self):
        api, cfg, username = _mock_api_triple()
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            runner.invoke(app, ["off", "my-instance"], input="n\n")
        api.stop_instance.assert_not_called()


# ------------------------------------------------------------------
# on
# ------------------------------------------------------------------


class TestOnCommand:
    def test_starts_instance(self, tmp_path: Path):
        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.return_value = SAMPLE_INSTANCE
        api.wait_instance_running.return_value = {**SAMPLE_INSTANCE, "PublicIpAddress": "5.6.7.8"}
        cfg.config.ssh.config_path = str(tmp_path / "ssh_config")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["on", "my-instance"])
        assert result.exit_code == 0
        api.start_instance.assert_called_once_with("i-abc123")


# ------------------------------------------------------------------
# resize
# ------------------------------------------------------------------


class TestResizeCommand:
    def test_resizes_with_yes_flag(self, tmp_path: Path):
        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.return_value = SAMPLE_INSTANCE
        api.wait_instance_state.return_value = SAMPLE_INSTANCE
        api.wait_instance_running.return_value = {**SAMPLE_INSTANCE, "PublicIpAddress": "1.2.3.4"}
        cfg.config.ssh.config_path = str(tmp_path / "ssh_config")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["resize", "my-instance", "t3.large", "--yes"])
        assert result.exit_code == 0
        api.stop_instance.assert_called_once()
        api.modify_instance_type.assert_called_once_with("i-abc123", "t3.large")
        api.start_instance.assert_called_once()


# ------------------------------------------------------------------
# hibernate
# ------------------------------------------------------------------


class TestHibernateCommand:
    def test_hibernates_with_yes_flag(self, tmp_path: Path):
        api, cfg, username = _mock_api_triple()
        api.find_instance_by_name.return_value = SAMPLE_INSTANCE
        api.wait_instance_state.return_value = SAMPLE_INSTANCE
        api.create_ami.return_value = "ami-new123"
        cfg.config.ssh.config_path = str(tmp_path / "ssh_config")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["hibernate", "my-instance", "--yes"])
        assert result.exit_code == 0
        api.create_ami.assert_called_once()
        api.terminate_instance.assert_called_once_with("i-abc123")

    def test_aborts_on_no(self):
        api, cfg, username = _mock_api_triple()
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            runner.invoke(app, ["hibernate", "my-instance"], input="n\n")
        api.create_ami.assert_not_called()


# ------------------------------------------------------------------
# wake
# ------------------------------------------------------------------


class TestWakeCommand:
    def test_wakes_instance(self, tmp_path: Path):

        ami = {
            "ImageId": "ami-snap123",
            "Tags": [
                {"Key": "Name", "Value": "my-instance"},
                {"Key": "skyops:owner", "Value": "alice"},
                {"Key": "skyops:size", "Value": "t3.medium"},
                {"Key": "skyops:region", "Value": "us-east-1"},
            ],
        }
        api, cfg, username = _mock_api_triple()
        api.find_hibernate_ami.return_value = ami
        api.get_default_vpc.return_value = "vpc-abc"
        api.get_subnets.return_value = [{"SubnetId": "subnet-abc"}]
        api.get_or_create_security_group.return_value = "sg-abc"
        api.launch_instance.return_value = {"InstanceId": "i-new123"}
        api.wait_instance_running.return_value = {
            "InstanceId": "i-new123",
            "PublicIpAddress": "5.5.5.5",
        }
        api.get_ami_snapshot_ids.return_value = ["snap-123"]
        cfg.config.ssh.config_path = str(tmp_path / "ssh_config")

        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
            patch("skyops.userdata.render_user_data", return_value="#!/bin/bash"),
        ):
            result = runner.invoke(app, ["wake", "my-instance", "--keep-ami"])
        assert result.exit_code == 0
        api.launch_instance.assert_called_once()


# ------------------------------------------------------------------
# list-key-pairs
# ------------------------------------------------------------------


class TestListKeyPairsCommand:
    def test_shows_key_pairs(self):
        kp = {"KeyName": "skyops-alice", "KeyPairId": "key-abc", "KeyFingerprint": "aa:bb", "CreateTime": "2024-01-01"}
        api, cfg, username = _mock_api_triple(key_pairs=[kp])
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["list-key-pairs"])
        assert result.exit_code == 0
        assert "skyops-alice" in result.output

    def test_empty(self):
        api, cfg, username = _mock_api_triple(key_pairs=[])
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["list-key-pairs"])
        assert result.exit_code == 0
        assert "No key pairs found" in result.output


# ------------------------------------------------------------------
# add-key-pair
# ------------------------------------------------------------------


class TestAddKeyPairCommand:
    def test_imports_key(self, tmp_path: Path):
        import base64

        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

        private_key = Ed25519PrivateKey.generate()
        raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        key_type = b"ssh-ed25519"
        inner = len(key_type).to_bytes(4, "big") + key_type + len(raw).to_bytes(4, "big") + raw
        pub_key_str = f"ssh-ed25519 {base64.b64encode(inner).decode()} test@skyops"
        key_file = tmp_path / "id_ed25519.pub"
        key_file.write_text(pub_key_str)

        api, cfg, username = _mock_api_triple()
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["add-key-pair", str(key_file)])
        assert result.exit_code == 0
        api.import_key_pair.assert_called_once()


# ------------------------------------------------------------------
# delete-key-pair
# ------------------------------------------------------------------


class TestDeleteKeyPairCommand:
    def test_deletes_with_yes_flag(self):
        api, cfg, username = _mock_api_triple()
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["delete-key-pair", "skyops-alice", "--yes"])
        assert result.exit_code == 0
        api.delete_key_pair.assert_called_once_with("skyops-alice")

    def test_aborts_on_no(self):
        api, cfg, username = _mock_api_triple()
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            runner.invoke(app, ["delete-key-pair", "skyops-alice"], input="n\n")
        api.delete_key_pair.assert_not_called()


# ------------------------------------------------------------------
# list-regions
# ------------------------------------------------------------------


class TestListRegionsCommand:
    def test_shows_regions(self):
        api, cfg, username = _mock_api_triple(regions=["us-east-1", "eu-west-1"])
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["list-regions"])
        assert result.exit_code == 0
        assert "us-east-1" in result.output


# ------------------------------------------------------------------
# Autocomplete helpers
# ------------------------------------------------------------------


class TestAutocompleteFunctions:
    def test_complete_instance_names_no_config(self):
        from skyops.main import complete_instance_name

        with patch("skyops.main.Config.exists", return_value=False):
            result = complete_instance_name("")
        assert result == []

    def test_complete_instance_names_with_match(self):
        from skyops.main import complete_instance_name

        api, cfg, username = _mock_api_triple(instances=[SAMPLE_INSTANCE])
        with (
            patch("skyops.main.Config.exists", return_value=True),
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
        ):
            result = complete_instance_name("my")
        assert "my-instance" in result

    def test_complete_hibernate_names_error(self):
        from skyops.main import complete_hibernate_name

        with (
            patch("skyops.main.Config.exists", return_value=True),
            patch("skyops.main._load_api", side_effect=Exception("boom")),
        ):
            result = complete_hibernate_name("")
        assert result == []
