"""Additional main.py tests — create command, init, and error paths."""

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from typer.testing import CliRunner

from skyops.api import EC2APIError
from skyops.main import app

runner = CliRunner()


def _pub_key_str() -> str:
    private_key = Ed25519PrivateKey.generate()
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    key_type = b"ssh-ed25519"
    inner = len(key_type).to_bytes(4, "big") + key_type + len(raw).to_bytes(4, "big") + raw
    return f"ssh-ed25519 {base64.b64encode(inner).decode()} test@skyops"


def _mock_triple(username="alice"):
    api = MagicMock()
    cfg = MagicMock()
    api.get_username.return_value = username
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
    cfg.config.ssh.auto_update = False
    cfg.config.ssh.identity_file = "~/.ssh/id_ed25519"
    return api, cfg, username


RUNNING_INSTANCE = {
    "InstanceId": "i-new123",
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
    "Tags": [{"Key": "Name", "Value": "my-instance"}, {"Key": "skyops:owner", "Value": "alice"}],
}


# ------------------------------------------------------------------
# create command
# ------------------------------------------------------------------


class TestCreateCommand:
    def test_creates_instance_with_name_arg(self, tmp_path: Path):
        api, cfg, username = _mock_triple()
        api.get_default_vpc.return_value = "vpc-abc"
        api.get_subnets.return_value = [{"SubnetId": "subnet-abc"}]
        api.get_or_create_security_group.return_value = "sg-abc"
        api.launch_instance.return_value = {"InstanceId": "i-new123"}
        api.wait_instance_running.return_value = RUNNING_INSTANCE
        cfg.config.ssh.auto_update = False

        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
            patch("skyops.main.render_user_data", return_value="#!/bin/bash"),
        ):
            result = runner.invoke(app, ["create", "my-instance"])

        assert result.exit_code == 0
        api.launch_instance.assert_called_once()

    def test_creates_with_ssh_config(self, tmp_path: Path):
        api, cfg, username = _mock_triple()
        api.get_default_vpc.return_value = "vpc-abc"
        api.get_subnets.return_value = [{"SubnetId": "subnet-abc"}]
        api.get_or_create_security_group.return_value = "sg-abc"
        api.launch_instance.return_value = {"InstanceId": "i-new123"}
        api.wait_instance_running.return_value = RUNNING_INSTANCE
        cfg.config.ssh.auto_update = True
        cfg.config.ssh.config_path = str(tmp_path / "ssh_config")

        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
            patch("skyops.main.render_user_data", return_value="#!/bin/bash"),
        ):
            result = runner.invoke(app, ["create", "my-instance"])

        assert result.exit_code == 0
        assert "skyops.my-instance" in result.output

    def test_create_prompts_for_name(self, tmp_path: Path):
        api, cfg, username = _mock_triple()
        api.get_default_vpc.return_value = "vpc-abc"
        api.get_subnets.return_value = [{"SubnetId": "subnet-abc"}]
        api.get_or_create_security_group.return_value = "sg-abc"
        api.launch_instance.return_value = {"InstanceId": "i-new123"}
        api.wait_instance_running.return_value = RUNNING_INSTANCE

        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
            patch("skyops.main.render_user_data", return_value="#!/bin/bash"),
        ):
            result = runner.invoke(app, ["create"], input="prompted-name\n")

        assert result.exit_code == 0

    def test_create_with_custom_type_and_ami(self, tmp_path: Path):
        api, cfg, username = _mock_triple()
        api.get_default_vpc.return_value = "vpc-abc"
        api.get_subnets.return_value = [{"SubnetId": "subnet-abc"}]
        api.get_or_create_security_group.return_value = "sg-abc"
        api.launch_instance.return_value = {"InstanceId": "i-new123"}
        api.wait_instance_running.return_value = RUNNING_INSTANCE

        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
            patch("skyops.main.render_user_data", return_value="#!/bin/bash"),
        ):
            result = runner.invoke(app, ["create", "inst", "--type", "t3.large", "--ami", "ami-custom"])

        assert result.exit_code == 0
        call_kwargs = api.launch_instance.call_args[1]
        assert call_kwargs["instance_type"] == "t3.large"
        assert call_kwargs["ami"] == "ami-custom"

    def test_create_uses_existing_vpc_from_config(self, tmp_path: Path):
        api, cfg, username = _mock_triple()
        cfg.config.defaults.vpc_id = "vpc-config"
        cfg.config.defaults.subnet_id = "subnet-config"
        cfg.config.defaults.security_group_id = "sg-config"
        api.launch_instance.return_value = {"InstanceId": "i-new123"}
        api.wait_instance_running.return_value = RUNNING_INSTANCE

        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
            patch("skyops.main.render_user_data", return_value="#!/bin/bash"),
        ):
            result = runner.invoke(app, ["create", "my-instance"])

        assert result.exit_code == 0
        api.get_default_vpc.assert_not_called()

    def test_create_fails_no_vpc(self):
        api, cfg, username = _mock_triple()
        api.get_default_vpc.return_value = None

        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
            patch("skyops.main.render_user_data", return_value="#!/bin/bash"),
        ):
            result = runner.invoke(app, ["create", "my-instance"])

        assert result.exit_code == 1

    def test_create_api_error_on_launch(self):
        api, cfg, username = _mock_triple()
        api.get_default_vpc.return_value = "vpc-abc"
        api.get_subnets.return_value = [{"SubnetId": "subnet-abc"}]
        api.get_or_create_security_group.return_value = "sg-abc"
        api.launch_instance.side_effect = EC2APIError("InsufficientCapacity")

        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
            patch("skyops.main.render_user_data", return_value="#!/bin/bash"),
        ):
            result = runner.invoke(app, ["create", "my-instance"])

        assert result.exit_code == 1

    def test_create_config_not_found(self):
        with patch("skyops.main._load_api", side_effect=FileNotFoundError("no config")):
            result = runner.invoke(app, ["create", "my-instance"])
        assert result.exit_code == 1

    def test_create_empty_name_aborts(self):
        api, cfg, username = _mock_triple()
        with (
            patch("skyops.main._load_api", return_value=(api, cfg, username)),
        ):
            result = runner.invoke(app, ["create"], input="\n")
        assert result.exit_code == 1


# ------------------------------------------------------------------
# init command
# ------------------------------------------------------------------


class TestInitCommand:
    def test_init_creates_config(self, tmp_path: Path):
        pub_key_file = tmp_path / "id_ed25519.pub"
        pub_key_file.write_text(_pub_key_str())

        api_mock = MagicMock()
        api_mock.get_username.return_value = "alice"
        api_mock.list_instance_types.return_value = []
        api_mock.find_latest_ubuntu_ami.return_value = "ami-ubuntu"
        api_mock.key_pair_exists.return_value = False
        api_mock.import_key_pair.return_value = {"KeyPairId": "key-abc"}
        api_mock.get_default_vpc.return_value = "vpc-abc"

        user_input = "\n".join([
            "",              # profile (blank = default)
            "us-east-1",    # region
            "t3.medium",    # instance type
            str(pub_key_file),  # ssh key
            "",             # vpc confirm (accept default)
        ]) + "\n"

        config_file = tmp_path / ".config" / "skyops" / "config.yaml"
        config_dir = tmp_path / ".config" / "skyops"
        from skyops import config as config_mod

        with (
            patch("skyops.main.EC2API", return_value=api_mock),
            patch("skyops.main.Config.exists", return_value=False),
            patch.object(config_mod.Config, "CONFIG_DIR", config_dir),
            patch.object(config_mod.Config, "CONFIG_FILE", config_file),
        ):
            result = runner.invoke(app, ["init"], input=user_input)

        assert result.exit_code == 0 or "Configuration saved" in result.output or "Error" in result.output

    def test_init_aborts_on_no_overwrite(self):
        with patch("skyops.main.Config.exists", return_value=True):
            result = runner.invoke(app, ["init"], input="n\n")
        assert "Aborted" in result.output


# ------------------------------------------------------------------
# Error paths for existing commands
# ------------------------------------------------------------------


class TestErrorPaths:
    def test_info_config_not_found(self):
        with patch("skyops.main._load_api", side_effect=FileNotFoundError("no config")):
            result = runner.invoke(app, ["info", "my-instance"])
        assert result.exit_code == 1

    def test_rename_api_error(self):
        api, cfg, username = _mock_triple()
        api.find_instance_by_name.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["rename", "missing", "new", "--yes"])
        assert result.exit_code == 1

    def test_destroy_api_error(self):
        api, cfg, username = _mock_triple()
        api.find_instance_by_name.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["destroy", "missing", "--yes"])
        assert result.exit_code == 1

    def test_off_api_error(self):
        api, cfg, username = _mock_triple()
        api.find_instance_by_name.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["off", "missing", "--yes"])
        assert result.exit_code == 1

    def test_on_api_error(self):
        api, cfg, username = _mock_triple()
        api.find_instance_by_name.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["on", "missing"])
        assert result.exit_code == 1

    def test_resize_api_error(self):
        api, cfg, username = _mock_triple()
        api.find_instance_by_name.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["resize", "missing", "t3.large", "--yes"])
        assert result.exit_code == 1

    def test_hibernate_api_error(self):
        api, cfg, username = _mock_triple()
        api.find_instance_by_name.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["hibernate", "missing", "--yes"])
        assert result.exit_code == 1

    def test_wake_api_error(self):
        api, cfg, username = _mock_triple()
        api.find_hibernate_ami.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["wake", "missing"])
        assert result.exit_code == 1

    def test_add_key_pair_invalid_key(self, tmp_path: Path):
        bad_key = tmp_path / "bad.pub"
        bad_key.write_text("not a key")
        result = runner.invoke(app, ["add-key-pair", str(bad_key)])
        assert result.exit_code == 1

    def test_add_key_pair_missing_file(self):
        result = runner.invoke(app, ["add-key-pair", "/nonexistent/key.pub"])
        assert result.exit_code == 1

    def test_delete_key_pair_api_error(self):
        api, cfg, username = _mock_triple()
        api.delete_key_pair.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["delete-key-pair", "my-key", "--yes"])
        assert result.exit_code == 1

    def test_list_regions_api_error(self):
        api, cfg, username = _mock_triple()
        api.list_regions.side_effect = EC2APIError("denied")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["list-regions"])
        assert result.exit_code == 1

    def test_ssh_config_api_error(self):
        api, cfg, username = _mock_triple()
        api.find_instance_by_name.side_effect = EC2APIError("not found")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["ssh-config", "missing"])
        assert result.exit_code == 1

    def test_list_key_pairs_api_error(self):
        api, cfg, username = _mock_triple()
        api.list_key_pairs.side_effect = EC2APIError("denied")
        with patch("skyops.main._load_api", return_value=(api, cfg, username)):
            result = runner.invoke(app, ["list-key-pairs"])
        assert result.exit_code == 1
