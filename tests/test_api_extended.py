"""Additional tests for skyops.api — covering wait, AMI, and remaining methods."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from skyops.api import EC2API, EC2APIError


def _client_error(code: str, message: str = "Error") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": message}}, "op")


def _make_api(region: str = "us-east-1") -> tuple[EC2API, MagicMock, MagicMock]:
    with patch("skyops.api.boto3.Session") as mock_session_cls:
        mock_session = MagicMock()
        mock_ec2 = MagicMock()
        mock_sts = MagicMock()
        mock_session.client.side_effect = lambda svc, **_: mock_ec2 if svc == "ec2" else mock_sts
        mock_session_cls.return_value = mock_session
        api = EC2API(region=region)
        return api, mock_ec2, mock_sts


# ------------------------------------------------------------------
# stop / start / rename
# ------------------------------------------------------------------


class TestStopStart:
    def test_stop_calls_ec2(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.stop_instances.return_value = {}
        api.stop_instance("i-abc")
        mock_ec2.stop_instances.assert_called_once_with(InstanceIds=["i-abc"])

    def test_stop_error_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.stop_instances.side_effect = _client_error("Unauthorized")
        with pytest.raises(EC2APIError):
            api.stop_instance("i-abc")

    def test_start_calls_ec2(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.start_instances.return_value = {}
        api.start_instance("i-abc")
        mock_ec2.start_instances.assert_called_once_with(InstanceIds=["i-abc"])

    def test_start_error_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.start_instances.side_effect = _client_error("Unauthorized")
        with pytest.raises(EC2APIError):
            api.start_instance("i-abc")

    def test_rename_tags_instance(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.create_tags.return_value = {}
        api.rename_instance("i-abc", "new-name")
        mock_ec2.create_tags.assert_called_once_with(
            Resources=["i-abc"],
            Tags=[{"Key": "Name", "Value": "new-name"}],
        )


# ------------------------------------------------------------------
# modify_instance_type
# ------------------------------------------------------------------


class TestModifyInstanceType:
    def test_modifies_type(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.modify_instance_attribute.return_value = {}
        api.modify_instance_type("i-abc", "t3.large")
        mock_ec2.modify_instance_attribute.assert_called_once()

    def test_error_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.modify_instance_attribute.side_effect = _client_error("InvalidState")
        with pytest.raises(EC2APIError):
            api.modify_instance_type("i-abc", "t3.large")


# ------------------------------------------------------------------
# wait_instance_state
# ------------------------------------------------------------------


class TestWaitInstanceState:
    def test_returns_when_state_matches(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-abc", "State": {"Name": "stopped"}}]}]
        }
        result = api.wait_instance_state("i-abc", "stopped")
        assert result["InstanceId"] == "i-abc"

    def test_polls_until_state(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.side_effect = [
            {
                "Reservations": [
                    {"Instances": [{"InstanceId": "i-abc", "State": {"Name": "stopping"}}]}
                ]
            },
            {
                "Reservations": [
                    {"Instances": [{"InstanceId": "i-abc", "State": {"Name": "stopped"}}]}
                ]
            },
        ]
        with patch("skyops.api.time.sleep"):
            result = api.wait_instance_state("i-abc", "stopped")
        assert result["State"]["Name"] == "stopped"

    def test_raises_on_terminal_state(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {"Instances": [{"InstanceId": "i-abc", "State": {"Name": "terminated"}}]}
            ]
        }
        with pytest.raises(EC2APIError, match="terminal"):
            api.wait_instance_state("i-abc", "running")

    def test_raises_on_timeout(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-abc", "State": {"Name": "pending"}}]}]
        }
        with (
            patch("skyops.api.time.sleep"),
            patch("skyops.api.time.time", side_effect=[0, 9999]),
            pytest.raises(EC2APIError, match="Timed out"),
        ):
            api.wait_instance_state("i-abc", "running", timeout=1)


# ------------------------------------------------------------------
# wait_instance_running
# ------------------------------------------------------------------


class TestWaitInstanceRunning:
    def test_returns_when_running_with_ip(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-abc",
                            "State": {"Name": "running"},
                            "PublicIpAddress": "1.2.3.4",
                        }
                    ]
                }
            ]
        }
        result = api.wait_instance_running("i-abc")
        assert result["PublicIpAddress"] == "1.2.3.4"

    def test_polls_until_ip_available(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.side_effect = [
            {
                "Reservations": [
                    {"Instances": [{"InstanceId": "i-abc", "State": {"Name": "pending"}}]}
                ]
            },
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": "i-abc",
                                "State": {"Name": "running"},
                                "PublicIpAddress": "5.5.5.5",
                            }
                        ]
                    }
                ]
            },
        ]
        with patch("skyops.api.time.sleep"):
            result = api.wait_instance_running("i-abc")
        assert result["PublicIpAddress"] == "5.5.5.5"

    def test_raises_on_terminated(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [
                {"Instances": [{"InstanceId": "i-abc", "State": {"Name": "terminated"}}]}
            ]
        }
        with pytest.raises(EC2APIError, match="terminated"):
            api.wait_instance_running("i-abc")

    def test_raises_on_timeout(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-abc", "State": {"Name": "pending"}}]}]
        }
        with (
            patch("skyops.api.time.sleep"),
            patch("skyops.api.time.time", side_effect=[0, 9999]),
            pytest.raises(EC2APIError, match="Timed out"),
        ):
            api.wait_instance_running("i-abc", timeout=1)


# ------------------------------------------------------------------
# create_ami / _wait_ami_available
# ------------------------------------------------------------------


class TestCreateAMI:
    def test_creates_and_waits(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.create_image.return_value = {"ImageId": "ami-new"}
        mock_ec2.describe_images.return_value = {
            "Images": [{"ImageId": "ami-new", "State": "available"}]
        }
        ami_id = api.create_ami("i-abc", "skyops-hibernate-test", [])
        assert ami_id == "ami-new"

    def test_ami_creation_failure(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.create_image.side_effect = _client_error("Unauthorized")
        with pytest.raises(EC2APIError):
            api.create_ami("i-abc", "name", [])

    def test_ami_state_failed_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.create_image.return_value = {"ImageId": "ami-fail"}
        mock_ec2.describe_images.return_value = {
            "Images": [{"ImageId": "ami-fail", "State": "failed"}]
        }
        with pytest.raises(EC2APIError, match="failed"):
            api.create_ami("i-abc", "name", [])

    def test_ami_wait_timeout(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.create_image.return_value = {"ImageId": "ami-slow"}
        mock_ec2.describe_images.return_value = {
            "Images": [{"ImageId": "ami-slow", "State": "pending"}]
        }
        with (
            patch("skyops.api.time.sleep"),
            patch("skyops.api.time.time", side_effect=[0, 9999]),
            pytest.raises(EC2APIError, match="Timed out"),
        ):
            api.create_ami("i-abc", "name", [])


# ------------------------------------------------------------------
# deregister_ami / get_ami_snapshot_ids / delete_snapshot
# ------------------------------------------------------------------


class TestAMICleanup:
    def test_deregister_ami(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.deregister_image.return_value = {}
        api.deregister_ami("ami-abc")
        mock_ec2.deregister_image.assert_called_once_with(ImageId="ami-abc")

    def test_deregister_error_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.deregister_image.side_effect = _client_error("Unauthorized")
        with pytest.raises(EC2APIError):
            api.deregister_ami("ami-abc")

    def test_get_snapshot_ids(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_images.return_value = {
            "Images": [
                {
                    "ImageId": "ami-abc",
                    "BlockDeviceMappings": [
                        {"Ebs": {"SnapshotId": "snap-111"}},
                        {"Ebs": {"SnapshotId": "snap-222"}},
                    ],
                }
            ]
        }
        snaps = api.get_ami_snapshot_ids("ami-abc")
        assert snaps == ["snap-111", "snap-222"]

    def test_get_snapshot_ids_empty(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_images.return_value = {"Images": []}
        assert api.get_ami_snapshot_ids("ami-abc") == []

    def test_delete_snapshot(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.delete_snapshot.return_value = {}
        api.delete_snapshot("snap-abc")
        mock_ec2.delete_snapshot.assert_called_once_with(SnapshotId="snap-abc")


# ------------------------------------------------------------------
# find_hibernate_ami
# ------------------------------------------------------------------


class TestFindHibernateAMI:
    def test_finds_by_name_tag(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_images.return_value = {
            "Images": [
                {
                    "ImageId": "ami-snap",
                    "Name": "skyops-hibernate-myinst",
                    "Tags": [
                        {"Key": "Name", "Value": "myinst"},
                        {"Key": "skyops:owner", "Value": "alice"},
                    ],
                }
            ]
        }
        result = api.find_hibernate_ami("myinst", "alice")
        assert result["ImageId"] == "ami-snap"

    def test_not_found_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_images.return_value = {"Images": []}
        with pytest.raises(EC2APIError, match="hibernated snapshot"):
            api.find_hibernate_ami("missing", "alice")


# ------------------------------------------------------------------
# import_key_pair / delete_key_pair
# ------------------------------------------------------------------


class TestKeyPairOps:
    def test_import_key_pair(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.import_key_pair.return_value = {"KeyPairId": "key-abc", "KeyName": "skyops-alice"}
        result = api.import_key_pair("skyops-alice", "ssh-ed25519 AAAA test@host")
        assert result["KeyName"] == "skyops-alice"

    def test_import_key_pair_error(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.import_key_pair.side_effect = _client_error("InvalidKeyPair.Duplicate")
        with pytest.raises(EC2APIError):
            api.import_key_pair("skyops-alice", "ssh-ed25519 AAAA test@host")

    def test_delete_key_pair(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.delete_key_pair.return_value = {}
        api.delete_key_pair("skyops-alice")
        mock_ec2.delete_key_pair.assert_called_once_with(KeyName="skyops-alice")

    def test_delete_key_pair_error(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.delete_key_pair.side_effect = _client_error("Unauthorized")
        with pytest.raises(EC2APIError):
            api.delete_key_pair("skyops-alice")


# ------------------------------------------------------------------
# list_regions
# ------------------------------------------------------------------


class TestListRegions:
    def test_returns_sorted_regions(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_regions.return_value = {
            "Regions": [{"RegionName": "us-west-2"}, {"RegionName": "eu-west-1"}]
        }
        regions = api.list_regions()
        assert regions == ["eu-west-1", "us-west-2"]

    def test_error_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.describe_regions.side_effect = _client_error("Unauthorized")
        with pytest.raises(EC2APIError):
            api.list_regions()


# ------------------------------------------------------------------
# launch_instance
# ------------------------------------------------------------------


class TestLaunchInstance:
    def test_launches_without_subnet(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.run_instances.return_value = {
            "Instances": [{"InstanceId": "i-new", "State": {"Name": "pending"}}]
        }
        result = api.launch_instance(
            name="test",
            instance_type="t3.medium",
            ami="ami-abc",
            key_pair_name="skyops-alice",
            subnet_id=None,
            security_group_ids=["sg-abc"],
            user_data="#!/bin/bash",
            owner="alice",
        )
        assert result["InstanceId"] == "i-new"
        # SubnetId should NOT be in the call when subnet_id is None
        call_kwargs = mock_ec2.run_instances.call_args[1]
        assert "SubnetId" not in call_kwargs

    def test_launches_with_subnet(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.run_instances.return_value = {
            "Instances": [{"InstanceId": "i-new2", "State": {"Name": "pending"}}]
        }
        api.launch_instance(
            name="test",
            instance_type="t3.medium",
            ami="ami-abc",
            key_pair_name="skyops-alice",
            subnet_id="subnet-xyz",
            security_group_ids=["sg-abc"],
            user_data="#!/bin/bash",
            owner="alice",
        )
        call_kwargs = mock_ec2.run_instances.call_args[1]
        assert call_kwargs["SubnetId"] == "subnet-xyz"

    def test_launch_error_raises(self):
        api, mock_ec2, _ = _make_api()
        mock_ec2.run_instances.side_effect = _client_error("InsufficientInstanceCapacity")
        with pytest.raises(EC2APIError):
            api.launch_instance(
                name="test",
                instance_type="t3.medium",
                ami="ami-abc",
                key_pair_name="key",
                subnet_id=None,
                security_group_ids=[],
                user_data="",
                owner="alice",
            )
